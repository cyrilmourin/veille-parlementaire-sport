"""SQLite de stockage et déduplication."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import Item


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    hash_key        TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    uid             TEXT NOT NULL,
    category        TEXT NOT NULL,
    chamber         TEXT,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    published_at    TEXT,
    summary         TEXT,
    matched_keywords TEXT,
    keyword_families TEXT,
    raw             TEXT,
    inserted_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # ---------- écriture ----------
    def upsert_many(self, items: Iterable[Item]) -> int:
        """Upsert d'items. Renvoie le nombre d'INSERT nouveaux.

        R15 (2026-04-22) : passage de `INSERT + IntegrityError pass` à un
        vrai `INSERT ... ON CONFLICT(hash_key) DO UPDATE` pour permettre
        l'enrichissement des items déjà en base.

        Motivation : 6411/6412 items `an_agenda` avaient `published_at=NULL`
        en DB alors que le parser actuel extrait 100 % des dates du dump
        officiel. Cause : un ancien parser buggy les avait insérés sans
        date, puis l'`IntegrityError: pass` avalait chaque ré-ingestion
        corrective. Même symptôme observé sur `dossiers_legislatifs`
        (5201/6766 NULL) et `communiques` (1183/1326 NULL).

        Règles d'upsert :
        - `published_at` : `COALESCE(new, old)` — on ne remplace que si
          l'ancien était NULL (évite de régresser sur une date déjà
          correcte si un scraper repasse avec une valeur vide).
        - `title` : remplacé si le nouveau est non-vide (permet de fixer
          les titres pauvres type "Réunion" quand un libellé arrive).
        - `summary` : remplacé si le nouveau est plus long (le parser
          s'enrichit au fil des versions → on prend la version la plus
          riche).
        - `matched_keywords`, `keyword_families` : toujours remplacés
          (refléter la vocab actuelle du matcher, notamment quand de
          nouveaux termes sont ajoutés à `keywords.yml`).
        - `raw` : toujours remplacé (contient des fixups incrementaux).
        - `url`, `source_id`, `uid`, `category`, `chamber` : immuables.
        - `inserted_at` : **jamais touché** — c'est la date de 1re
          détection, utilisée par `fetch_matched_since` pour le digest
          quotidien. La modifier casserait la fenêtre glissante.

        Retour : nombre d'INSERT nouveaux (pas les UPDATE). Les UPDATE
        sont silencieux — pour les tracer, utiliser les logs niveau
        DEBUG sur la couche applicative.
        """
        now = datetime.utcnow().isoformat(timespec="seconds")
        cur = self.conn.cursor()
        inserted = 0
        for it in items:
            # On détecte l'INSERT via le CHANGES après la requête. Pour
            # SQLite, `INSERT ... ON CONFLICT DO UPDATE` renvoie rowcount=1
            # dans les 2 cas, on doit donc distinguer nous-mêmes.
            cur.execute(
                "SELECT 1 FROM items WHERE hash_key = ?",
                (it.hash_key,),
            )
            existed = cur.fetchone() is not None
            cur.execute(
                """
                INSERT INTO items (
                    hash_key, source_id, uid, category, chamber, title, url,
                    published_at, summary, matched_keywords, keyword_families,
                    raw, inserted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hash_key) DO UPDATE SET
                    published_at = COALESCE(excluded.published_at, items.published_at),
                    title = CASE
                        WHEN excluded.title IS NOT NULL AND excluded.title != ''
                        THEN excluded.title
                        ELSE items.title
                    END,
                    summary = CASE
                        WHEN excluded.summary IS NOT NULL
                             AND length(COALESCE(excluded.summary, '')) >
                                 length(COALESCE(items.summary, ''))
                        THEN excluded.summary
                        ELSE items.summary
                    END,
                    matched_keywords = excluded.matched_keywords,
                    keyword_families = excluded.keyword_families,
                    raw = excluded.raw
                """,
                (
                    it.hash_key, it.source_id, it.uid, it.category, it.chamber,
                    it.title, it.url,
                    it.published_at.isoformat() if it.published_at else None,
                    it.summary,
                    json.dumps(it.matched_keywords, ensure_ascii=False),
                    json.dumps(it.keyword_families, ensure_ascii=False),
                    json.dumps(it.raw, ensure_ascii=False, default=str),
                    now,
                ),
            )
            if not existed:
                inserted += 1
        self.conn.commit()
        return inserted

    # ---------- lecture ----------
    def fetch_matched_since(self, since: datetime, only_matched: bool = True):
        q = """
            SELECT * FROM items
            WHERE inserted_at >= ?
              AND (? = 0 OR matched_keywords != '[]')
            ORDER BY published_at DESC, inserted_at DESC
        """
        cur = self.conn.execute(q, (since.isoformat(timespec="seconds"), 1 if only_matched else 0))
        return [dict(row) for row in cur.fetchall()]

    def fetch_recent(self, limit: int = 200):
        cur = self.conn.execute(
            "SELECT * FROM items ORDER BY inserted_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cur.fetchall()]

    def counts_by_category(self):
        cur = self.conn.execute(
            "SELECT category, COUNT(*) AS n FROM items "
            "WHERE matched_keywords != '[]' GROUP BY category ORDER BY n DESC"
        )
        return {r["category"]: r["n"] for r in cur.fetchall()}
