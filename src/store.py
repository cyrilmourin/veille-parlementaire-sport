"""SQLite de stockage et déduplication."""
from __future__ import annotations

import hashlib
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


# R33 (2026-04-24) — Persistance des champs recalculés (audit §4.5).
# Colonnes additionnelles ajoutées par migration idempotente au premier
# démarrage. Toutes `nullable` (SQLite `ALTER TABLE ADD COLUMN` crée
# toujours des colonnes nullable, valeur par défaut NULL). Motivation :
# aujourd'hui `snippet`, `dossier_id`, `canonical_url`, `status_label`
# sont recalculés à chaque export via `_fix_*_row` depuis `raw` — fragile
# (un parseur qui change la forme de `raw` casse l'export) et lent
# (itération sur tous les rows à chaque build Hugo). `content_hash` est
# nouveau : permet à un run de détecter un « refresh » silencieux de
# contenu (le site-source a re-publié sous le même UID avec un body
# différent — arrivé sur Élysée en R22c) sans avoir à diffuser le whole
# row.
#
# Conventions :
# - Toutes les colonnes sont nullable : un parseur/fixup les remplit
#   quand il les connaît, sinon NULL — l'export garde le fallback vers
#   `raw.*` / recalcul pour ne pas régresser.
# - `dossier_id` indexé pour préparer R4.4 (dédup par dossier_id clé
#   primaire, une seule passe de merge) — R33 ne fait que le préparer,
#   le refactor dédup sera une étape future.
MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("snippet", "TEXT"),
    ("dossier_id", "TEXT"),
    ("canonical_url", "TEXT"),
    ("status_label", "TEXT"),
    ("content_hash", "TEXT"),
)
MIGRATION_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_items_dossier_id ON items(dossier_id)",
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Retourne le set des colonnes existantes de `table`. Utilise
    `PRAGMA table_info(...)` qui ne lève pas si la table manque (retourne
    simplement une liste vide)."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def migrate_items(conn: sqlite3.Connection) -> list[str]:
    """Ajoute les colonnes R33 manquantes sur `items` + index dossier_id.

    Idempotent : appelable plusieurs fois sans effet de bord.
    Retourne la liste des colonnes effectivement ajoutées (utile pour
    logger ce qui a changé au 1er run post-upgrade).

    Aucune donnée existante touchée. Les nouvelles colonnes valent NULL
    par défaut pour les rows pré-R33.
    """
    existing = _existing_columns(conn, "items")
    added: list[str] = []
    for name, sql_type in MIGRATION_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {sql_type}")
            added.append(name)
    for idx_sql in MIGRATION_INDEXES:
        conn.execute(idx_sql)
    if added:
        conn.commit()
    return added


def compute_content_hash(title: str | None, summary: str | None) -> str:
    """Hash court (sha1, 12 premiers chars) sur `title||summary`.

    Utilisé pour détecter un refresh silencieux de contenu source : si
    le même (source_id, uid) revient avec un titre ou résumé différent,
    son `content_hash` change — un observateur peut lever une alerte de
    drift (parseur cassé, contenu réécrit par la source).

    Retourne `""` si les deux entrées sont vides (hash inutile sur du
    vide, évite de polluer la DB avec des hash d'entrée nulle).
    """
    t = (title or "").strip()
    s = (summary or "").strip()
    if not t and not s:
        return ""
    h = hashlib.sha1(f"{t}||{s}".encode("utf-8")).hexdigest()
    return h[:12]


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # R33 : migration idempotente des colonnes additionnelles
        # (snippet, dossier_id, canonical_url, status_label, content_hash).
        # Appelée à chaque __init__ — le no-op se fait en 1 `PRAGMA`
        # très rapide. Garantit que les DB existantes pré-R33 sont
        # mises à niveau au premier démarrage de l'app sans étape
        # manuelle.
        migrate_items(self.conn)

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
            # R33 : content_hash calculé à l'insert/update, les autres
            # colonnes persistées (snippet, dossier_id, canonical_url,
            # status_label) viennent de l'Item si le parseur / matcher
            # les a renseignées, sinon NULL. Un item sans snippet en DB
            # déclenchera le fallback recalcul côté export — le cutover
            # est progressif, pas d'effet big-bang.
            ch = compute_content_hash(it.title, it.summary) or None
            cur.execute(
                """
                INSERT INTO items (
                    hash_key, source_id, uid, category, chamber, title, url,
                    published_at, summary, matched_keywords, keyword_families,
                    raw, inserted_at,
                    snippet, dossier_id, canonical_url, status_label, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    raw = excluded.raw,
                    snippet = COALESCE(NULLIF(excluded.snippet, ''), items.snippet),
                    dossier_id = COALESCE(excluded.dossier_id, items.dossier_id),
                    canonical_url = COALESCE(excluded.canonical_url, items.canonical_url),
                    status_label = COALESCE(excluded.status_label, items.status_label),
                    content_hash = COALESCE(excluded.content_hash, items.content_hash)
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
                    it.snippet or None,
                    it.dossier_id,
                    it.canonical_url,
                    it.status_label,
                    ch,
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
