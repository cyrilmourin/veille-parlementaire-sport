"""R42-CZB (2026-05-16) — Backfill `last_seen_at` pour les items legacy.

Bug constaté en prod après R42-CY : à la migration, tous les items
existants ont `last_seen_at = NULL`. Le filtre
`_filter_stale_agenda_items` (R42-CY) considère NULL comme « safe
legacy » et les conserve. Les items agenda orphelins (absents du dump
AN) ne sont jamais ré-upsertés → leur `last_seen_at` reste NULL
indéfiniment → ils restent affichés sur le site.

Fix : à chaque ouverture du Store (idempotent), on backfille les
`last_seen_at` NULL avec `inserted_at`. Au prochain daily run, les
items vivants seront refreshés via upsert ; les orphelins garderont
la valeur ancienne d'inserted_at → tomberont sous la fenêtre 2j.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from src.store import Store, migrate_items


def test_backfill_sets_last_seen_at_to_inserted_at_for_null(tmp_path):
    """Items existants avec `last_seen_at IS NULL` → reçoivent `inserted_at`."""
    db_path = tmp_path / "test.sqlite3"
    # Crée la DB sans last_seen_at via un schéma legacy minimal puis
    # ajoute la colonne sans backfill, comme l'état post-R42-CY en prod.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE items (
          source_id TEXT, uid TEXT, category TEXT, chamber TEXT,
          title TEXT, url TEXT UNIQUE, published_at TEXT, summary TEXT,
          raw TEXT, inserted_at TEXT NOT NULL,
          PRIMARY KEY (source_id, uid)
        )
        """
    )
    legacy_inserted = "2026-04-01T10:00:00"
    conn.execute(
        "INSERT INTO items (source_id, uid, category, chamber, title, url, "
        "published_at, summary, raw, inserted_at) VALUES "
        "('an_agenda','RU_old','agenda','AN','Vieille séance',"
        "'https://x/1','2026-05-18T15:00:00','','{}',?)",
        (legacy_inserted,),
    )
    conn.commit()
    conn.close()

    # Ouvre via Store → migration s'exécute → backfill devrait poser
    # last_seen_at = inserted_at pour la row legacy.
    store = Store(str(db_path))
    cur = store.conn.execute(
        "SELECT last_seen_at FROM items WHERE uid='RU_old'"
    )
    row = cur.fetchone()
    assert row[0] == legacy_inserted, (
        f"backfill attendu = {legacy_inserted!r}, observé = {row[0]!r}"
    )


def test_backfill_does_not_touch_rows_with_existing_last_seen_at(tmp_path):
    """Idempotent : `last_seen_at` non-NULL préservé."""
    db_path = tmp_path / "test.sqlite3"
    store = Store(str(db_path))
    # Insère un row avec last_seen_at déjà posé
    fresh = "2026-05-16T08:00:00"
    store.conn.execute(
        "INSERT INTO items (source_id, uid, category, chamber, title, url, "
        "published_at, summary, raw, inserted_at, last_seen_at) VALUES "
        "('an_agenda','RU_fresh','agenda','AN','Séance fresh',"
        "'https://x/2','2026-05-20T15:00:00','','{}','2026-05-15T08:00:00',?)",
        (fresh,),
    )
    store.conn.commit()
    # Re-trigger migration manuellement (équivalent à un autre open)
    migrate_items(store.conn)
    cur = store.conn.execute(
        "SELECT last_seen_at FROM items WHERE uid='RU_fresh'"
    )
    assert cur.fetchone()[0] == fresh


def test_backfill_falls_back_when_inserted_at_missing(tmp_path):
    """Si `inserted_at` est NULL/absent → fallback à now()-7j."""
    # Cas pathologique théorique : inserted_at NOT NULL en schéma mais
    # une migration boiteuse pourrait laisser des trous. On vérifie que
    # le COALESCE protège : la valeur est non-NULL et antérieure à
    # maintenant - 1j (donc filtrable).
    db_path = tmp_path / "test.sqlite3"
    # Schéma sans contrainte NOT NULL pour pouvoir tester
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE items (
          source_id TEXT, uid TEXT, category TEXT, chamber TEXT,
          title TEXT, url TEXT UNIQUE, published_at TEXT, summary TEXT,
          raw TEXT, inserted_at TEXT,
          PRIMARY KEY (source_id, uid)
        )
        """
    )
    conn.execute(
        "INSERT INTO items (source_id, uid, category, chamber, title, url, "
        "published_at, summary, raw, inserted_at) VALUES "
        "('an_agenda','RU_noins','agenda','AN','Séance sans ins',"
        "'https://x/3','2026-05-22T15:00:00','','{}',NULL)"
    )
    conn.commit()
    # Ajoute last_seen_at sans valeur (post-R42-CY)
    conn.execute("ALTER TABLE items ADD COLUMN last_seen_at TEXT")
    conn.commit()
    migrate_items(conn)
    cur = conn.execute("SELECT last_seen_at FROM items WHERE uid='RU_noins'")
    val = cur.fetchone()[0]
    assert val is not None and val != ""
    parsed = datetime.fromisoformat(val)
    # Doit être ~ now - 7j → en tout cas < now - 1j
    assert parsed < datetime.now() - timedelta(days=1)


def test_backfill_runs_idempotent_no_error_on_no_null(tmp_path):
    """Aucune row avec NULL → migration ne lève pas et ne change rien."""
    db_path = tmp_path / "test.sqlite3"
    store = Store(str(db_path))
    store.conn.execute(
        "INSERT INTO items (source_id, uid, category, chamber, title, url, "
        "published_at, summary, raw, inserted_at, last_seen_at) VALUES "
        "('an_agenda','RU_a','agenda','AN','x','https://a','2026-05-20','','{}','2026-05-15','2026-05-15')"
    )
    store.conn.commit()
    # Plusieurs appels successifs : doit rester stable
    migrate_items(store.conn)
    migrate_items(store.conn)
    cur = store.conn.execute("SELECT last_seen_at FROM items WHERE uid='RU_a'")
    assert cur.fetchone()[0] == "2026-05-15"
