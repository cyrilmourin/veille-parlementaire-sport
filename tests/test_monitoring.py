"""Tests du module `src.monitoring` (R29, 2026-04-24).

On teste en isolation la fonction pure `compute_state_and_alerts` plus le
rendu HTML du bloc digest. Aucun test réseau — le monitoring repose sur
l'état J-1 persisté dans `data/pipeline_health.json`.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src import monitoring
from src.models import Item


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mk_item(source_id: str, published_at: str | None) -> Item:
    """Fabrique un Item minimal avec juste ce dont monitoring a besoin."""
    dt = None
    if published_at:
        dt = datetime.fromisoformat(published_at.replace("Z", ""))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
    return Item(
        uid=f"{source_id}-{published_at or 'none'}",
        source_id=source_id,
        category="communiques",
        title="t",
        url="https://example.test/",
        summary="",
        published_at=dt,
        chamber="",
        raw={},
    )


_NOW = datetime(2026, 4, 24, 6, 0, 0)  # naïf UTC, fixe pour les tests


# ---------------------------------------------------------------------------
# Tests utilitaires
# ---------------------------------------------------------------------------


def test_parse_iso_naive_tolerates_none():
    assert monitoring._parse_iso_naive(None) is None
    assert monitoring._parse_iso_naive("") is None
    assert monitoring._parse_iso_naive("pas une date") is None


def test_parse_iso_naive_strips_tz():
    # La fonction doit ramener en naïf UTC
    dt = monitoring._parse_iso_naive("2026-04-24T10:00:00+02:00")
    assert dt is not None
    assert dt.tzinfo is None
    # 10h +02:00 = 08h UTC
    assert dt.hour == 8


def test_max_published_at_ignores_none():
    items = [
        _mk_item("x", None),
        _mk_item("x", "2026-04-20T10:00:00"),
        _mk_item("x", "2026-04-22T10:00:00"),
        _mk_item("x", None),
    ]
    mx = monitoring._max_published_at(items)
    assert mx == datetime(2026, 4, 22, 10, 0, 0)


def test_max_published_at_empty_list():
    assert monitoring._max_published_at([]) is None


# ---------------------------------------------------------------------------
# load_state / save_state round-trip
# ---------------------------------------------------------------------------


def test_load_state_absent_returns_empty(tmp_path):
    p = tmp_path / "does_not_exist.json"
    state = monitoring.load_state(p)
    assert state["sources"] == {}
    assert state["schema_version"] == monitoring.SCHEMA_VERSION


def test_load_state_corrupt_json_returns_empty(tmp_path):
    p = tmp_path / "corrupt.json"
    p.write_text("not json at all{", encoding="utf-8")
    state = monitoring.load_state(p)
    assert state["sources"] == {}


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "health.json"
    state = {
        "schema_version": 1,
        "last_run_at": "2026-04-24T06:00:00",
        "sources": {
            "an_rapports": {
                "last_fetched": 12,
                "last_error": None,
                "consecutive_errors": 0,
                "last_ok_at": "2026-04-24T06:00:00",
                "last_max_published_at": "2026-04-23T15:30:00",
            }
        },
    }
    monitoring.save_state(p, state)
    reloaded = monitoring.load_state(p)
    assert reloaded == state


# ---------------------------------------------------------------------------
# compute_state_and_alerts — cas « 1er run, aucun état précédent »
# ---------------------------------------------------------------------------


def test_compute_first_run_no_alerts():
    """Premier run : pas de previous state → on initialise sans alerter."""
    previous = {"sources": {}}
    fetch_stats = {
        "an_rapports": {"fetched": 10, "error": None},
        "senat_rss": {"fetched": 0, "error": None},  # scope réduit, OK
    }
    items = [_mk_item("an_rapports", "2026-04-23T15:00:00")]

    new_state, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, items, now=_NOW,
    )
    assert alerts == []
    assert "an_rapports" in new_state["sources"]
    assert new_state["sources"]["an_rapports"]["last_fetched"] == 10
    assert new_state["sources"]["senat_rss"]["last_fetched"] == 0


# ---------------------------------------------------------------------------
# ERR_PERSIST
# ---------------------------------------------------------------------------


def test_err_persist_not_triggered_before_threshold():
    """2 runs en erreur de suite < seuil (3) → pas d'alerte."""
    previous = {
        "sources": {
            "afld": {
                "last_fetched": 0, "last_error": "502 Bad Gateway",
                "consecutive_errors": 1, "last_ok_at": None,
                "last_max_published_at": None,
            }
        }
    }
    fetch_stats = {"afld": {"fetched": 0, "error": "502 Bad Gateway"}}
    new_state, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, [], now=_NOW,
    )
    assert new_state["sources"]["afld"]["consecutive_errors"] == 2
    assert [a.kind for a in alerts] == []


def test_err_persist_triggers_at_threshold():
    """Au 3e run consécutif en erreur, ERR_PERSIST déclenche."""
    previous = {
        "sources": {
            "afld": {
                "last_fetched": 0, "last_error": "502",
                "consecutive_errors": 2, "last_ok_at": None,
                "last_max_published_at": None,
            }
        }
    }
    fetch_stats = {"afld": {"fetched": 0, "error": "503 Service Unavailable"}}
    new_state, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, [], now=_NOW,
    )
    assert new_state["sources"]["afld"]["consecutive_errors"] == 3
    kinds = [a.kind for a in alerts]
    assert "ERR_PERSIST" in kinds
    # Le message doit mentionner la source et l'erreur
    e = next(a for a in alerts if a.kind == "ERR_PERSIST")
    assert e.source_id == "afld"
    assert "503" in e.message


def test_err_persist_not_re_triggered_after_threshold():
    """Au 4e run consécutif en erreur on ne re-alerte pas (évite spam)."""
    previous = {
        "sources": {
            "afld": {
                "last_fetched": 0, "last_error": "502",
                "consecutive_errors": 3, "last_ok_at": None,
                "last_max_published_at": None,
            }
        }
    }
    fetch_stats = {"afld": {"fetched": 0, "error": "502"}}
    _, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, [], now=_NOW,
    )
    # consecutive_errors = 4, alerte ne se redéclenche pas
    assert "ERR_PERSIST" not in [a.kind for a in alerts]


def test_err_persist_resets_on_success():
    """Un succès remet `consecutive_errors` à 0."""
    previous = {
        "sources": {
            "afld": {
                "last_fetched": 0, "last_error": "502",
                "consecutive_errors": 2, "last_ok_at": None,
                "last_max_published_at": None,
            }
        }
    }
    fetch_stats = {"afld": {"fetched": 5, "error": None}}
    new_state, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, [_mk_item("afld", "2026-04-24T10:00:00")],
        now=_NOW,
    )
    assert new_state["sources"]["afld"]["consecutive_errors"] == 0
    assert "ERR_PERSIST" not in [a.kind for a in alerts]


# ---------------------------------------------------------------------------
# FORMAT_DRIFT
# ---------------------------------------------------------------------------


def test_format_drift_triggers_when_count_collapses():
    """Passage ≥ 5 items → 0 SANS erreur HTTP déclenche FORMAT_DRIFT."""
    previous = {
        "sources": {
            "an_rapports": {
                "last_fetched": 12, "last_error": None,
                "consecutive_errors": 0,
                "last_ok_at": "2026-04-23T06:00:00",
                "last_max_published_at": "2026-04-22T10:00:00",
            }
        }
    }
    fetch_stats = {"an_rapports": {"fetched": 0, "error": None}}
    _, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, [], now=_NOW,
    )
    drift = [a for a in alerts if a.kind == "FORMAT_DRIFT"]
    assert len(drift) == 1
    assert drift[0].source_id == "an_rapports"
    assert "12" in drift[0].message


def test_format_drift_not_triggered_below_min_prev_count():
    """Scope réduit : si J-1 avait < MIN_PREV_COUNT (5), pas d'alerte."""
    previous = {
        "sources": {
            "cnosf": {
                "last_fetched": 2, "last_error": None,
                "consecutive_errors": 0,
                "last_ok_at": "2026-04-23T06:00:00",
                "last_max_published_at": "2026-04-22T10:00:00",
            }
        }
    }
    fetch_stats = {"cnosf": {"fetched": 0, "error": None}}
    _, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, [], now=_NOW,
    )
    assert "FORMAT_DRIFT" not in [a.kind for a in alerts]


def test_format_drift_not_triggered_on_network_error():
    """Si on a une erreur HTTP, c'est ERR_PERSIST qui traite — pas FORMAT_DRIFT."""
    previous = {
        "sources": {
            "x": {
                "last_fetched": 20, "last_error": None,
                "consecutive_errors": 0,
                "last_ok_at": "2026-04-23T06:00:00",
                "last_max_published_at": "2026-04-22T10:00:00",
            }
        }
    }
    fetch_stats = {"x": {"fetched": 0, "error": "Connection refused"}}
    _, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, [], now=_NOW,
    )
    assert "FORMAT_DRIFT" not in [a.kind for a in alerts]


# ---------------------------------------------------------------------------
# FEED_STALE
# ---------------------------------------------------------------------------


def test_feed_stale_triggers_at_threshold_crossing():
    """Fraîcheur qui bascule < 60j → > 60j : alerte une fois."""
    # J-1 : dernier item il y a 58 jours (sous seuil)
    prev_max = (_NOW - timedelta(days=58)).isoformat(timespec="seconds")
    previous = {
        "sources": {
            "insep": {
                "last_fetched": 3, "last_error": None,
                "consecutive_errors": 0,
                "last_ok_at": "2026-04-23T06:00:00",
                "last_max_published_at": prev_max,
            }
        }
    }
    # Aujourd'hui : toujours le même max (rien de neuf), donc 59 jours.
    # 59 n'excède pas le seuil ; testons le cas 61 jours.
    # Simulation : previous stored 59 days ago, now stored 61 days ago
    prev_max_59 = (_NOW - timedelta(days=59)).isoformat(timespec="seconds")
    previous["sources"]["insep"]["last_max_published_at"] = prev_max_59
    fetch_stats = {"insep": {"fetched": 0, "error": None}}
    # On passe à un _NOW qui fait basculer : avance de 2 jours
    future_now = _NOW + timedelta(days=2)
    _, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, [], now=future_now,
    )
    stale = [a for a in alerts if a.kind == "FEED_STALE"]
    assert len(stale) == 1
    assert stale[0].source_id == "insep"


def test_feed_stale_not_triggered_when_already_stale():
    """Déjà figé J-1 (stale_alerted=True en état) : pas d'alerte aujourd'hui
    (évite le spam quotidien tant que la source reste figée)."""
    prev_max = (_NOW - timedelta(days=90)).isoformat(timespec="seconds")
    previous = {
        "sources": {
            "insep": {
                "last_fetched": 0, "last_error": None,
                "consecutive_errors": 0,
                "last_ok_at": "2026-04-23T06:00:00",
                "last_max_published_at": prev_max,
                "stale_alerted": True,
            }
        }
    }
    fetch_stats = {"insep": {"fetched": 0, "error": None}}
    _, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, [], now=_NOW,
    )
    assert "FEED_STALE" not in [a.kind for a in alerts]


def test_feed_stale_not_triggered_when_fresh():
    """Feed actif : pas d'alerte."""
    prev_max = (_NOW - timedelta(days=5)).isoformat(timespec="seconds")
    previous = {
        "sources": {
            "an_rapports": {
                "last_fetched": 10, "last_error": None,
                "consecutive_errors": 0,
                "last_ok_at": "2026-04-23T06:00:00",
                "last_max_published_at": prev_max,
            }
        }
    }
    fetch_stats = {"an_rapports": {"fetched": 10, "error": None}}
    items = [_mk_item("an_rapports", _NOW.isoformat(timespec="seconds"))]
    _, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, items, now=_NOW,
    )
    assert "FEED_STALE" not in [a.kind for a in alerts]


# ---------------------------------------------------------------------------
# Sources qui disparaissent (désactivées entre deux runs)
# ---------------------------------------------------------------------------


def test_disabled_source_kept_in_state_no_alert():
    """Source présente J-1 mais absente des fetch_stats (désactivée entre-temps) :
    conservée en état pour historique, pas d'alerte.
    """
    previous = {
        "sources": {
            "senat_agenda": {
                "last_fetched": 0, "last_error": "403 WAF",
                "consecutive_errors": 5, "last_ok_at": None,
                "last_max_published_at": None,
            }
        }
    }
    fetch_stats = {}  # plus fetchée
    new_state, alerts = monitoring.compute_state_and_alerts(
        previous, fetch_stats, [], now=_NOW,
    )
    assert "senat_agenda" in new_state["sources"]
    # Pas d'alerte puisque plus dans fetch_stats
    assert alerts == []


# ---------------------------------------------------------------------------
# Rendu digest
# ---------------------------------------------------------------------------


def test_render_digest_block_empty_when_no_alert():
    assert monitoring.render_digest_block([]) == ""


def test_render_digest_block_contains_source_ids():
    alerts = [
        monitoring.Alert("ERR_PERSIST", "afld", "afld en erreur depuis 3 runs"),
        monitoring.Alert("FORMAT_DRIFT", "an_rapports", "an_rapports : 0 items..."),
    ]
    html = monitoring.render_digest_block(alerts)
    assert "afld" in html
    assert "an_rapports" in html
    assert "Erreur persistante" in html
    assert "Format cassé" in html
    # Pluriel correct
    assert "2 alertes" in html


def test_render_digest_block_singular():
    alerts = [monitoring.Alert("FEED_STALE", "x", "x figé...")]
    html = monitoring.render_digest_block(alerts)
    assert "1 alerte" in html
    assert "alertes" not in html.replace("1 alerte", "")  # pas de pluriel parasite


# ---------------------------------------------------------------------------
# log_alerts (robustesse)
# ---------------------------------------------------------------------------


def test_log_alerts_empty_does_not_raise(caplog):
    monitoring.log_alerts([])
    # Pas d'assertion sur le contenu — juste que ça ne crashe pas.


def test_log_alerts_warns_each(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="src.monitoring"):
        monitoring.log_alerts([
            monitoring.Alert("ERR_PERSIST", "afld", "test err"),
            monitoring.Alert("FEED_STALE", "insep", "test stale"),
        ])
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    # 1 synthèse + 2 détails minimum
    assert len(warnings) >= 3
