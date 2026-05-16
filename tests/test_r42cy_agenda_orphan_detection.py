"""R42-CY (2026-05-16) — Détection orphan items agenda.

Cyril 2026-05-16 : « il y a toujours le problème des dates qui ne sont
plus à l'agenda AN (18 mai) ». Cas que R42-CI (refresh date_NULL) ne
traitait pas : AN supprime totalement l'item du dump Agenda.json.zip
(vs juste mettre `timeStampDebut=NULL`). L'upsert n'est jamais appelé
sur cet uid → notre DB garde la version périmée.

Fix : nouvelle colonne `last_seen_at` mise à jour à chaque upsert
(R42-CY migration). Filtre côté export qui masque les items agenda
FUTURS dont `last_seen_at < now - 2j` (= non revus dans les 2 derniers
runs daily).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.models import Item
from src.store import Store
from src.site_export import _filter_stale_agenda_items


# ---------------------------------------------------------------------------
# Migration : last_seen_at présent + posé à l'upsert
# ---------------------------------------------------------------------------

def test_last_seen_at_column_added(tmp_path):
    """La migration ajoute bien la colonne last_seen_at."""
    db_path = tmp_path / "test.sqlite3"
    store = Store(str(db_path))
    cur = store.conn.execute("PRAGMA table_info(items)")
    cols = {row[1] for row in cur.fetchall()}
    assert "last_seen_at" in cols


def test_upsert_sets_last_seen_at_on_insert(tmp_path):
    """Un INSERT pose last_seen_at = now() approximatif."""
    db_path = tmp_path / "test.sqlite3"
    store = Store(str(db_path))
    it = Item(
        source_id="an_agenda",
        uid="RU_test",
        category="agenda",
        chamber="AN",
        title="Séance test",
        url="https://example.com/s",
        published_at=datetime(2026, 5, 18, 15, 0),
        summary="",
        raw={},
    )
    before = datetime.utcnow()
    store.upsert_many([it])
    cur = store.conn.execute(
        "SELECT last_seen_at FROM items WHERE uid='RU_test'"
    )
    row = cur.fetchone()
    assert row[0] is not None
    last_seen = datetime.fromisoformat(row[0])
    assert last_seen >= before - timedelta(seconds=2)


def test_upsert_refreshes_last_seen_at_on_update(tmp_path):
    """Un UPDATE (même uid ré-upserté) met à jour last_seen_at."""
    import time
    db_path = tmp_path / "test.sqlite3"
    store = Store(str(db_path))
    it = Item(
        source_id="an_agenda",
        uid="RU_test",
        category="agenda",
        chamber="AN",
        title="Séance test",
        url="https://example.com/s",
        published_at=datetime(2026, 5, 18, 15, 0),
        summary="",
        raw={},
    )
    store.upsert_many([it])
    cur = store.conn.execute("SELECT last_seen_at FROM items WHERE uid='RU_test'")
    seen_1 = cur.fetchone()[0]

    time.sleep(1.1)  # garantit un seconde différente
    store.upsert_many([it])
    cur = store.conn.execute("SELECT last_seen_at FROM items WHERE uid='RU_test'")
    seen_2 = cur.fetchone()[0]
    assert seen_2 > seen_1, (
        f"last_seen_at non rafraîchi à l'update : {seen_1} → {seen_2}"
    )


# ---------------------------------------------------------------------------
# Filtre _filter_stale_agenda_items
# ---------------------------------------------------------------------------

def test_filter_drops_stale_future_agenda():
    """Un item agenda futur non revu depuis > 2j doit être masqué."""
    now = datetime.now()
    stale_seen = (now - timedelta(days=3)).isoformat(timespec="seconds")
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": stale_seen,
            "title": "Séance AN reportée",
        },
    ]
    kept = _filter_stale_agenda_items(rows)
    assert len(kept) == 0, "L'item orphelin doit être masqué"


def test_filter_keeps_fresh_future_agenda():
    """Un item agenda futur revu il y a < 2j → conservé."""
    now = datetime.now()
    fresh_seen = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": fresh_seen,
            "title": "Séance AN à venir",
        },
    ]
    kept = _filter_stale_agenda_items(rows)
    assert len(kept) == 1


def test_filter_keeps_past_agenda_even_stale():
    """Un item agenda PASSÉ non revu reste affiché (cycle normal AN,
    on garde l'historique récent)."""
    now = datetime.now()
    stale_seen = (now - timedelta(days=5)).isoformat(timespec="seconds")
    past_pub = (now - timedelta(days=3)).isoformat(timespec="seconds")
    rows = [
        {
            "category": "agenda",
            "published_at": past_pub,
            "last_seen_at": stale_seen,
            "title": "Séance AN passée",
        },
    ]
    kept = _filter_stale_agenda_items(rows)
    assert len(kept) == 1


def test_filter_ignores_non_agenda():
    """Items non-agenda non affectés (le filtre est ciblé agenda)."""
    now = datetime.now()
    stale_seen = (now - timedelta(days=5)).isoformat(timespec="seconds")
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "category": "dossiers_legislatifs",
            "published_at": future_pub,
            "last_seen_at": stale_seen,
            "title": "Dossier législatif futur",
        },
        {
            "category": "questions",
            "published_at": future_pub,
            "last_seen_at": stale_seen,
            "title": "Question écrite",
        },
    ]
    kept = _filter_stale_agenda_items(rows)
    assert len(kept) == 2


def test_filter_safe_on_legacy_items_without_last_seen():
    """Items pré-R42-CY (sans last_seen_at) sont conservés (safe legacy)."""
    now = datetime.now()
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": None,  # pré-migration
            "title": "Séance legacy",
        },
        {
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": "",  # idem cas chaîne vide
            "title": "Séance legacy 2",
        },
    ]
    kept = _filter_stale_agenda_items(rows)
    assert len(kept) == 2
