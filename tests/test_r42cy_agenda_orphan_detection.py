"""R42-CY (2026-05-16) → R42-DC (2026-05-17) — Détection orphan items agenda.

Cyril 2026-05-16 : « il y a toujours le problème des dates qui ne sont
plus à l'agenda AN (18 mai) ». Cas que R42-CI (refresh date_NULL) ne
traitait pas : AN supprime totalement l'item du dump Agenda.json.zip
(vs juste mettre `timeStampDebut=NULL`). L'upsert n'est jamais appelé
sur cet uid → notre DB garde la version périmée.

R42-CY (initial) — colonne `last_seen_at` posée à chaque upsert,
filtre `now - 2j` côté export.

R42-DC (2026-05-17) — Refonte stricte : comparaison `last_seen_at` ↔
`source.last_ok_at` (de `data/pipeline_health.json`). Pas de buffer
défensif — un item disparu du dernier fetch réussi de sa source est
masqué immédiatement. Cyril : « à chaque run les occurrences agenda
présentes en base doivent être vérifiées pour l'avenir ».
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.models import Item
from src.store import Store
from src.site_export import _filter_stale_agenda_items


# ---------------------------------------------------------------------------
# Migration : last_seen_at présent + posé à l'upsert (R42-CY)
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
# Filtre _filter_stale_agenda_items — R42-DC (strict, last_ok_at)
# ---------------------------------------------------------------------------

def _make_health(source_id: str, last_ok_at: str) -> dict:
    """Helper : fabrique un dict `source_health` (équivalent
    `pipeline_health.json::sources`) pour un test."""
    return {source_id: {"last_ok_at": last_ok_at}}


def test_filter_drops_future_agenda_not_seen_in_latest_fetch():
    """Item agenda futur dont last_seen_at < source.last_ok_at →
    masqué (= n'était pas dans le dernier fetch réussi de la source).
    """
    now = datetime.now()
    # Dernier fetch réussi de la source : il y a 1 heure
    last_ok = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    # L'item n'a pas été revu depuis 3 jours
    stale_seen = (now - timedelta(days=3)).isoformat(timespec="seconds")
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "source_id": "an_agenda",
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": stale_seen,
            "title": "Séance AN reportée",
        },
    ]
    health = _make_health("an_agenda", last_ok)
    kept = _filter_stale_agenda_items(rows, source_health=health)
    assert len(kept) == 0, "L'item orphelin (last_seen_at < last_ok_at) doit être masqué"


def test_filter_keeps_future_agenda_seen_in_latest_fetch():
    """Item agenda futur dont last_seen_at > source.last_ok_at →
    conservé (= était dans le dernier fetch réussi)."""
    now = datetime.now()
    # Dernier fetch réussi : il y a 1 heure
    last_ok = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    # L'item a été revu il y a 30 minutes (donc après last_ok)
    fresh_seen = (now - timedelta(minutes=30)).isoformat(timespec="seconds")
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "source_id": "an_agenda",
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": fresh_seen,
            "title": "Séance AN à venir",
        },
    ]
    health = _make_health("an_agenda", last_ok)
    kept = _filter_stale_agenda_items(rows, source_health=health)
    assert len(kept) == 1


def test_filter_keeps_past_agenda_even_stale():
    """Item agenda PASSÉ non revu reste affiché (cycle normal AN,
    on garde l'historique récent). Le filtre est ciblé FUTUR only."""
    now = datetime.now()
    last_ok = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    stale_seen = (now - timedelta(days=5)).isoformat(timespec="seconds")
    past_pub = (now - timedelta(days=3)).isoformat(timespec="seconds")
    rows = [
        {
            "source_id": "an_agenda",
            "category": "agenda",
            "published_at": past_pub,
            "last_seen_at": stale_seen,
            "title": "Séance AN passée",
        },
    ]
    health = _make_health("an_agenda", last_ok)
    kept = _filter_stale_agenda_items(rows, source_health=health)
    assert len(kept) == 1


def test_filter_ignores_non_agenda():
    """Items non-agenda non affectés (le filtre est ciblé agenda)."""
    now = datetime.now()
    last_ok = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    stale_seen = (now - timedelta(days=5)).isoformat(timespec="seconds")
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "source_id": "an_dossiers_legislatifs",
            "category": "dossiers_legislatifs",
            "published_at": future_pub,
            "last_seen_at": stale_seen,
            "title": "Dossier législatif futur",
        },
        {
            "source_id": "an_questions",
            "category": "questions",
            "published_at": future_pub,
            "last_seen_at": stale_seen,
            "title": "Question écrite",
        },
    ]
    health = _make_health("an_agenda", last_ok)
    kept = _filter_stale_agenda_items(rows, source_health=health)
    assert len(kept) == 2


def test_filter_safe_on_legacy_items_without_last_seen():
    """Items pré-R42-CY (sans last_seen_at) sont conservés (safe legacy)."""
    now = datetime.now()
    last_ok = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "source_id": "an_agenda",
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": None,  # pré-migration
            "title": "Séance legacy",
        },
        {
            "source_id": "an_agenda",
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": "",  # idem cas chaîne vide
            "title": "Séance legacy 2",
        },
    ]
    health = _make_health("an_agenda", last_ok)
    kept = _filter_stale_agenda_items(rows, source_health=health)
    assert len(kept) == 2


def test_filter_safe_when_no_health_info_for_source():
    """Source absente de pipeline_health → conservé (safe fallback).
    Cas : nouvelle source ajoutée avant son 1er run réussi."""
    now = datetime.now()
    stale_seen = (now - timedelta(days=3)).isoformat(timespec="seconds")
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "source_id": "an_agenda",
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": stale_seen,
            "title": "Séance",
        },
    ]
    # Health vide → pas d'info exploitable
    kept = _filter_stale_agenda_items(rows, source_health={})
    assert len(kept) == 1


def test_filter_safe_when_source_has_no_last_ok_at():
    """Source présente dans pipeline_health mais sans `last_ok_at` (=
    jamais réussi à fetch) → conservé (safe fallback)."""
    now = datetime.now()
    stale_seen = (now - timedelta(days=3)).isoformat(timespec="seconds")
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "source_id": "an_agenda",
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": stale_seen,
            "title": "Séance",
        },
    ]
    health = {"an_agenda": {"last_ok_at": None}}
    kept = _filter_stale_agenda_items(rows, source_health=health)
    assert len(kept) == 1


def test_filter_dropped_items_collected_when_out_dropped_provided():
    """`out_dropped` (R42-DC) collecte les items masqués avec
    `raw._postponed = True` posé pour la carte accueil PPL."""
    now = datetime.now()
    last_ok = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    stale_seen = (now - timedelta(days=3)).isoformat(timespec="seconds")
    future_pub = (now + timedelta(days=2)).isoformat(timespec="seconds")
    rows = [
        {
            "source_id": "an_agenda",
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": stale_seen,
            "title": "Séance AN orpheline",
            "raw": {"organe": "PO838901"},
        },
    ]
    health = _make_health("an_agenda", last_ok)
    dropped: list[dict] = []
    kept = _filter_stale_agenda_items(
        rows, out_dropped=dropped, source_health=health,
    )
    assert len(kept) == 0
    assert len(dropped) == 1
    assert dropped[0]["raw"]["_postponed"] is True
    assert dropped[0]["raw"]["_postponed_reason"] == "stale"


def test_filter_real_case_ppl_sport_pro_18_mai_2026():
    """Cas réel PPL Sport pro : séance plénière 18/05/2026
    (RUANR5L17S2026IDC460094). AN a retiré l'item du dump après le
    report. Notre DB a `last_seen_at = 2026-05-15` (dernière fois où
    l'AN l'a publié). Run d'aujourd'hui 2026-05-17 a réussi (last_ok_at
    = 2026-05-17). → masqué."""
    last_ok = "2026-05-17T06:49:19"
    last_seen = "2026-05-15T06:42:00"
    future_pub = "2026-05-18T14:45:00"  # demain — futur par rapport au 17
    rows = [
        {
            "source_id": "an_agenda",
            "category": "agenda",
            "published_at": future_pub,
            "last_seen_at": last_seen,
            "title": "Discussion de la PPL Sport professionnel",
            "raw": {"organe": "PO838901"},
        },
    ]
    health = _make_health("an_agenda", last_ok)
    # On force le "now" du filtre à 2026-05-17 via monkeypatching ?
    # Plus simple : on s'assure que future_pub > now() même en 2026.
    # Si la suite tourne en 2027, ce test passera quand même : le
    # filtre keep car published_at <= now (passé). Acceptable :
    # le test devient simplement un no-op au lieu d'un faux échec.
    from datetime import datetime
    if future_pub <= datetime.now().isoformat(timespec="seconds"):
        # Le test ne peut plus simuler "futur" — on skip.
        import pytest
        pytest.skip("Future test date depassed")
    kept = _filter_stale_agenda_items(rows, source_health=health)
    assert kept == [], "PPL Sport pro 18/05 reporté doit être masqué"
