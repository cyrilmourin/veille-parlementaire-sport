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
        """Insère les nouveaux items. Renvoie le nombre d'insertions."""
        now = datetime.utcnow().isoformat(timespec="seconds")
        cur = self.conn.cursor()
        inserted = 0
        for it in items:
            try:
                cur.execute(
                    """
                    INSERT INTO items (
                        hash_key, source_id, uid, category, chamber, title, url,
                        published_at, summary, matched_keywords, keyword_families,
                        raw, inserted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                inserted += 1
            except sqlite3.IntegrityError:
                pass
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
