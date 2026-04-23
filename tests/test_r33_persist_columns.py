"""R33 (2026-04-24) — Tests persistance colonnes DB (audit §4.5).

Couvre :
- Migration idempotente `migrate_items` : ajout des colonnes manquantes,
  no-op sur DB déjà à jour, colonnes existantes préservées.
- `compute_content_hash` : stabilité, différenciation, tolérance empty.
- `upsert_many` : écriture des nouvelles colonnes, refresh silencieux
  détecté via `content_hash`, fallback NULL quand Item ne renseigne pas
  les champs optionnels.
- Rétrocompatibilité : DB pré-R33 (schéma sans les 5 colonnes) migrée
  sans perte au prochain `Store(path)`.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest

from src.models import Item
from src.store import (
    MIGRATION_COLUMNS,
    SCHEMA,
    Store,
    _existing_columns,
    compute_content_hash,
    migrate_items,
)


# ---------------------------------------------------------------------------
# compute_content_hash
# ---------------------------------------------------------------------------


class TestComputeContentHash:
    def test_empty_returns_empty(self):
        assert compute_content_hash(None, None) == ""
        assert compute_content_hash("", "") == ""
        assert compute_content_hash("   ", "   ") == ""

    def test_stable(self):
        assert compute_content_hash("Titre", "Résumé") == compute_content_hash("Titre", "Résumé")

    def test_differs_on_title_change(self):
        a = compute_content_hash("Titre A", "Résumé")
        b = compute_content_hash("Titre B", "Résumé")
        assert a != b

    def test_differs_on_summary_change(self):
        a = compute_content_hash("Titre", "Version 1")
        b = compute_content_hash("Titre", "Version 2")
        assert a != b

    def test_short_hash_12_chars(self):
        h = compute_content_hash("T", "S")
        assert len(h) == 12
        # Caractères hex uniquement
        int(h, 16)  # lève si non-hex

    def test_title_only(self):
        h = compute_content_hash("Titre seul", None)
        assert h
        assert len(h) == 12

    def test_summary_only(self):
        h = compute_content_hash(None, "Résumé seul")
        assert h
        assert len(h) == 12


# ---------------------------------------------------------------------------
# Migration idempotente
# ---------------------------------------------------------------------------


class TestMigrateItems:
    def test_migration_adds_all_columns_on_pre_r33_schema(self, tmp_path):
        # Simule une DB pré-R33 : on crée une table `items` sans les
        # colonnes R33.
        db_path = tmp_path / "pre_r33.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)
        # Injecte un row pré-R33 pour vérifier que la migration ne
        # détruit pas les données existantes.
        conn.execute(
            """
            INSERT INTO items (hash_key, source_id, uid, category, chamber,
                               title, url, inserted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("s::u", "s", "u", "agenda", "AN", "T", "https://x", "2026-04-24"),
        )
        conn.commit()

        added = migrate_items(conn)
        assert sorted(added) == sorted(c for c, _ in MIGRATION_COLUMNS)

        # Le row existant est toujours là, les nouvelles colonnes valent NULL
        row = conn.execute(
            "SELECT title, snippet, dossier_id, canonical_url, status_label, content_hash "
            "FROM items WHERE hash_key = 's::u'"
        ).fetchone()
        assert row[0] == "T"
        assert row[1] is None
        assert row[2] is None
        assert row[3] is None
        assert row[4] is None
        assert row[5] is None

    def test_migration_is_idempotent(self, tmp_path):
        db_path = tmp_path / "idem.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)

        added1 = migrate_items(conn)
        added2 = migrate_items(conn)
        added3 = migrate_items(conn)

        assert len(added1) == len(MIGRATION_COLUMNS)
        assert added2 == []
        assert added3 == []

    def test_migration_creates_dossier_id_index(self, tmp_path):
        db_path = tmp_path / "idx.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)
        migrate_items(conn)

        indexes = {
            row[1]
            for row in conn.execute(
                "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='items'"
            ).fetchall()
        }
        assert "idx_items_dossier_id" in indexes

    def test_existing_columns_preserved_if_partial_r33(self, tmp_path):
        # Simule un état intermédiaire : certaines colonnes R33 déjà
        # présentes (ex. un run R33 partiel qui a planté en route).
        db_path = tmp_path / "partial.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)
        conn.execute("ALTER TABLE items ADD COLUMN snippet TEXT")
        conn.execute("ALTER TABLE items ADD COLUMN dossier_id TEXT")
        conn.commit()

        added = migrate_items(conn)
        # Seules canonical_url, status_label, content_hash doivent être ajoutées
        assert set(added) == {"canonical_url", "status_label", "content_hash"}

        # Toutes les colonnes R33 sont maintenant présentes
        cols = _existing_columns(conn, "items")
        for name, _ in MIGRATION_COLUMNS:
            assert name in cols


# ---------------------------------------------------------------------------
# Store — persistance à l'upsert
# ---------------------------------------------------------------------------


def _mk_item(
    uid: str = "u1",
    title: str = "Titre",
    summary: str = "Résumé",
    *,
    snippet: str = "",
    dossier_id: str | None = None,
    canonical_url: str | None = None,
    status_label: str | None = None,
) -> Item:
    return Item(
        source_id="src",
        uid=uid,
        category="dossiers_legislatifs",
        chamber="AN",
        title=title,
        url="https://example.com/1",
        published_at=datetime(2026, 4, 24, 12, 0, 0),
        summary=summary,
        snippet=snippet,
        dossier_id=dossier_id,
        canonical_url=canonical_url,
        status_label=status_label,
    )


class TestStoreUpsertR33:
    def test_store_init_creates_r33_columns(self, tmp_path):
        db = tmp_path / "store.sqlite3"
        st = Store(db)
        cols = _existing_columns(st.conn, "items")
        for name, _ in MIGRATION_COLUMNS:
            assert name in cols

    def test_upsert_writes_r33_columns(self, tmp_path):
        db = tmp_path / "upsert.sqlite3"
        st = Store(db)
        item = _mk_item(
            snippet="extrait sport et citoyenneté",
            dossier_id="pjl24-630",
            canonical_url="https://senat.fr/dossier-legislatif/pjl24-630.html",
            status_label="En cours",
        )
        n = st.upsert_many([item])
        assert n == 1

        row = st.conn.execute(
            "SELECT snippet, dossier_id, canonical_url, status_label, content_hash "
            "FROM items WHERE hash_key = ?",
            (item.hash_key,),
        ).fetchone()
        assert row["snippet"] == "extrait sport et citoyenneté"
        assert row["dossier_id"] == "pjl24-630"
        assert row["canonical_url"] == "https://senat.fr/dossier-legislatif/pjl24-630.html"
        assert row["status_label"] == "En cours"
        assert row["content_hash"] == compute_content_hash(item.title, item.summary)

    def test_upsert_item_without_optional_fields_writes_null(self, tmp_path):
        db = tmp_path / "null.sqlite3"
        st = Store(db)
        # Item sans dossier_id / canonical_url / status_label / snippet
        item = _mk_item()
        st.upsert_many([item])
        row = st.conn.execute(
            "SELECT snippet, dossier_id, canonical_url, status_label, content_hash "
            "FROM items WHERE hash_key = ?",
            (item.hash_key,),
        ).fetchone()
        assert row["snippet"] is None
        assert row["dossier_id"] is None
        assert row["canonical_url"] is None
        assert row["status_label"] is None
        # content_hash toujours calculé (car title + summary non-vides)
        assert row["content_hash"] == compute_content_hash(item.title, item.summary)

    def test_upsert_content_hash_changes_on_refresh(self, tmp_path):
        """Refresh silencieux : même (source_id, uid), summary change → hash change."""
        db = tmp_path / "hash.sqlite3"
        st = Store(db)
        item_v1 = _mk_item(summary="Version 1")
        st.upsert_many([item_v1])
        hash_v1 = st.conn.execute(
            "SELECT content_hash FROM items WHERE hash_key = ?",
            (item_v1.hash_key,),
        ).fetchone()[0]

        item_v2 = _mk_item(summary="Version 2 enrichie avec détails supplémentaires")
        st.upsert_many([item_v2])
        hash_v2 = st.conn.execute(
            "SELECT content_hash FROM items WHERE hash_key = ?",
            (item_v2.hash_key,),
        ).fetchone()[0]

        assert hash_v1 != hash_v2

    def test_upsert_preserves_optional_field_if_new_is_null(self, tmp_path):
        """Un Item arrivant sans dossier_id ne doit pas écraser un dossier_id
        déjà persisté (COALESCE dans l'ON CONFLICT)."""
        db = tmp_path / "coalesce.sqlite3"
        st = Store(db)
        item_v1 = _mk_item(dossier_id="pjl24-630", status_label="En cours")
        st.upsert_many([item_v1])

        # Second upsert sans dossier_id (parseur appauvri)
        item_v2 = _mk_item(summary="Résumé enrichi plus long pour forcer un update")
        st.upsert_many([item_v2])

        row = st.conn.execute(
            "SELECT dossier_id, status_label FROM items WHERE hash_key = ?",
            (item_v2.hash_key,),
        ).fetchone()
        assert row["dossier_id"] == "pjl24-630"
        assert row["status_label"] == "En cours"

    def test_upsert_updates_optional_field_when_new_is_provided(self, tmp_path):
        db = tmp_path / "update.sqlite3"
        st = Store(db)
        item_v1 = _mk_item(status_label="En cours")
        st.upsert_many([item_v1])

        item_v2 = _mk_item(status_label="Adopté")
        st.upsert_many([item_v2])

        row = st.conn.execute(
            "SELECT status_label FROM items WHERE hash_key = ?",
            (item_v2.hash_key,),
        ).fetchone()
        assert row["status_label"] == "Adopté"

    def test_upsert_preserves_snippet_on_empty_new(self, tmp_path):
        """Un Item sans snippet (chaîne vide) ne doit pas écraser un snippet
        existant (NULLIF + COALESCE)."""
        db = tmp_path / "snip.sqlite3"
        st = Store(db)
        item_v1 = _mk_item(snippet="contexte sport")
        st.upsert_many([item_v1])

        item_v2 = _mk_item(snippet="", summary="Résumé plus long pour update")
        st.upsert_many([item_v2])

        row = st.conn.execute(
            "SELECT snippet FROM items WHERE hash_key = ?",
            (item_v2.hash_key,),
        ).fetchone()
        assert row["snippet"] == "contexte sport"

    def test_store_reopens_pre_r33_db_seamlessly(self, tmp_path):
        """Scénario prod : une DB créée pré-R33 (sans colonnes) doit être
        ouvrable avec le nouveau Store sans erreur et sans perte."""
        db_path = tmp_path / "legacy.sqlite3"
        # Simule pré-R33 : schéma basique, aucune migration R33
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)
        conn.execute(
            """
            INSERT INTO items (hash_key, source_id, uid, category, chamber,
                               title, url, inserted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy::1", "legacy", "1", "agenda", "AN", "Legacy",
             "https://x.com/1", "2026-04-20"),
        )
        conn.commit()
        conn.close()

        # Nouveau Store : doit migrer et lire le row legacy sans erreur
        st = Store(db_path)
        cols = _existing_columns(st.conn, "items")
        for name, _ in MIGRATION_COLUMNS:
            assert name in cols
        rows = st.fetch_recent(limit=10)
        assert len(rows) == 1
        assert rows[0]["title"] == "Legacy"
        assert rows[0]["snippet"] is None
        assert rows[0]["dossier_id"] is None
