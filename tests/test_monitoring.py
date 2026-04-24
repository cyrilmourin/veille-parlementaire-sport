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
    # R34 : `load_state` upgrade les vieux states en ajoutant les clés
    # manquantes (`volumetry_history`, `last_run_at`). On persiste donc un
    # state R34-complete pour ne pas comparer à un dict muté par l'upgrade.
    state = {
        "schema_version": monitoring.SCHEMA_VERSION,
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
        "volumetry_history": [],
    }
    monitoring.save_state(p, state)
    reloaded = monitoring.load_state(p)
    assert reloaded == state


def test_load_state_upgrades_r29_schema(tmp_path):
    """R34 : un state écrit par R29 (sans `volumetry_history`) est
    upgradable à l'ouverture sans perdre les sources existantes."""
    p = tmp_path / "health_r29.json"
    r29_state = {
        "schema_version": 1,
        "last_run_at": "2026-04-24T06:00:00",
        "sources": {
            "afld": {
                "last_fetched": 5, "last_error": None,
                "consecutive_errors": 0, "last_ok_at": "2026-04-24T06:00:00",
                "last_max_published_at": "2026-04-23T10:00:00",
            }
        },
    }
    p.write_text(json.dumps(r29_state), encoding="utf-8")
    reloaded = monitoring.load_state(p)
    assert reloaded["sources"] == r29_state["sources"]
    assert reloaded["volumetry_history"] == []


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


# ===========================================================================
# R34 (2026-04-24) — Volumétrie, freshness snapshot, should_fail_ci
# ===========================================================================


class TestVolumetryHistory:
    """Le ring buffer `volumetry_history` est alimenté automatiquement par
    `compute_state_and_alerts`. On vérifie l'ajout, le plafonnement et le
    contenu de l'entrée."""

    def test_history_gets_appended_each_run(self):
        previous = {"sources": {}, "volumetry_history": []}
        fetch_stats = {
            "a": {"fetched": 10, "error": None},
            "b": {"fetched": 5, "error": None},
        }
        new_state, _ = monitoring.compute_state_and_alerts(
            previous, fetch_stats, [], now=_NOW,
        )
        assert len(new_state["volumetry_history"]) == 1
        entry = new_state["volumetry_history"][0]
        assert entry["total_fetched"] == 15
        assert entry["date"] == _NOW.isoformat(timespec="seconds")

    def test_history_caps_at_max(self):
        # On pré-remplit avec VOLUMETRY_HISTORY_MAX entrées fictives, puis
        # on pousse un run — la plus vieille doit être jetée.
        old_entries = [
            {"date": f"2026-03-{i:02d}T06:00:00", "total_fetched": i}
            for i in range(1, monitoring.VOLUMETRY_HISTORY_MAX + 1)
        ]
        previous = {
            "sources": {},
            "volumetry_history": old_entries,
        }
        fetch_stats = {"a": {"fetched": 999, "error": None}}
        new_state, _ = monitoring.compute_state_and_alerts(
            previous, fetch_stats, [], now=_NOW,
        )
        hist = new_state["volumetry_history"]
        assert len(hist) == monitoring.VOLUMETRY_HISTORY_MAX
        # Le dernier est le run courant, les suivants sont les plus récents
        # des précédents (donc l'entrée i=1 a été jetée).
        assert hist[-1]["total_fetched"] == 999
        assert hist[0]["total_fetched"] == 2

    def test_history_ignores_none_fetched(self):
        """Un fetch_stats avec `fetched: None` (bug parser) ne doit pas
        planter le calcul — coerce en 0."""
        previous = {"sources": {}, "volumetry_history": []}
        fetch_stats = {
            "a": {"fetched": 10, "error": None},
            "b": {"fetched": None, "error": None},
        }
        new_state, _ = monitoring.compute_state_and_alerts(
            previous, fetch_stats, [], now=_NOW,
        )
        assert new_state["volumetry_history"][0]["total_fetched"] == 10


class TestVolumetryCollapse:
    """Alerte `VOLUMETRY_COLLAPSE` : volume J < 50 % moyenne 7 derniers runs."""

    def _history_runs(self, totals: list[int]) -> list[dict]:
        return [
            {"date": f"2026-04-{i+1:02d}T06:00:00", "total_fetched": t}
            for i, t in enumerate(totals)
        ]

    def test_no_alert_if_not_enough_samples(self):
        """< VOLUMETRY_MIN_SAMPLES échantillons passés : on ne peut pas
        conclure — pas d'alerte (faux-positif DB fraîche)."""
        previous = {
            "sources": {},
            "volumetry_history": self._history_runs([100, 100]),
        }
        fetch_stats = {"a": {"fetched": 5, "error": None}}  # 5 vs ~100
        _, alerts = monitoring.compute_state_and_alerts(
            previous, fetch_stats, [], now=_NOW,
        )
        assert "VOLUMETRY_COLLAPSE" not in [a.kind for a in alerts]

    def test_triggers_below_ratio(self):
        """5 runs à 100 items puis 20 items aujourd'hui : ratio 20% < 50%."""
        previous = {
            "sources": {},
            "volumetry_history": self._history_runs([100] * 5),
        }
        fetch_stats = {"a": {"fetched": 20, "error": None}}
        _, alerts = monitoring.compute_state_and_alerts(
            previous, fetch_stats, [], now=_NOW,
        )
        collapse = [a for a in alerts if a.kind == "VOLUMETRY_COLLAPSE"]
        assert len(collapse) == 1
        assert collapse[0].source_id == "*"
        # Le message doit contenir les volumes pour diagnostic
        assert "20" in collapse[0].message
        assert "100" in collapse[0].message

    def test_does_not_trigger_when_above_ratio(self):
        """Run J à 60 items sur moyenne 100 : ratio 60% ≥ 50% → pas d'alerte."""
        previous = {
            "sources": {},
            "volumetry_history": self._history_runs([100] * 5),
        }
        fetch_stats = {"a": {"fetched": 60, "error": None}}
        _, alerts = monitoring.compute_state_and_alerts(
            previous, fetch_stats, [], now=_NOW,
        )
        assert "VOLUMETRY_COLLAPSE" not in [a.kind for a in alerts]

    def test_does_not_trigger_when_previous_mean_zero(self):
        """Moyenne passée à 0 (DB vide historiquement) : pas de référence,
        pas d'alerte (division par 0 évitée)."""
        previous = {
            "sources": {},
            "volumetry_history": self._history_runs([0] * 5),
        }
        fetch_stats = {"a": {"fetched": 0, "error": None}}
        _, alerts = monitoring.compute_state_and_alerts(
            previous, fetch_stats, [], now=_NOW,
        )
        assert "VOLUMETRY_COLLAPSE" not in [a.kind for a in alerts]


class TestComputeFreshnessSnapshot:
    """`compute_freshness_snapshot` lit `state["sources"]` et retourne
    l'âge en jours du dernier item par source."""

    def test_empty_state(self):
        assert monitoring.compute_freshness_snapshot({}, now=_NOW) == []

    def test_sorted_by_age_desc(self):
        state = {
            "sources": {
                "fresh": {
                    "last_max_published_at": (
                        _NOW - timedelta(days=2)
                    ).isoformat(timespec="seconds"),
                },
                "stale": {
                    "last_max_published_at": (
                        _NOW - timedelta(days=50)
                    ).isoformat(timespec="seconds"),
                },
            }
        }
        snap = monitoring.compute_freshness_snapshot(state, now=_NOW)
        assert snap == [("stale", 50), ("fresh", 2)]

    def test_skips_sources_without_max_pub(self):
        state = {
            "sources": {
                "never_fetched": {"last_max_published_at": None},
                "ok": {
                    "last_max_published_at": (
                        _NOW - timedelta(days=1)
                    ).isoformat(timespec="seconds"),
                },
            }
        }
        snap = monitoring.compute_freshness_snapshot(state, now=_NOW)
        assert snap == [("ok", 1)]


class TestComputeVolumetryAverages:
    """Agrégats pour affichage digest : current, avg_7d, avg_30d."""

    def test_empty_history(self):
        result = monitoring.compute_volumetry_averages({})
        assert result == {"current": 0, "avg_7d": None, "avg_30d": None, "samples": 0}

    def test_single_run_no_averages(self):
        """1 run seul : current OK mais les moyennes restent None
        (on ne fait pas de moyenne sur 0 run antérieur)."""
        state = {"volumetry_history": [
            {"date": "2026-04-24T06:00:00", "total_fetched": 100}
        ]}
        r = monitoring.compute_volumetry_averages(state)
        assert r["current"] == 100
        assert r["avg_7d"] is None
        assert r["avg_30d"] is None
        assert r["samples"] == 1

    def test_multiple_runs_averages(self):
        """10 runs : avg_7d calcule sur 7 derniers avant courant,
        avg_30d sur les 9 avant courant."""
        history = [
            {"date": f"2026-04-{i:02d}T06:00:00", "total_fetched": 100}
            for i in range(1, 11)
        ]
        # Dernier run différent pour vérifier qu'il n'est PAS dans la moyenne
        history[-1]["total_fetched"] = 20
        state = {"volumetry_history": history}
        r = monitoring.compute_volumetry_averages(state)
        assert r["current"] == 20
        assert r["avg_7d"] == 100.0  # les 7 avant sont tous à 100
        assert r["avg_30d"] == 100.0
        assert r["samples"] == 10


class TestShouldFailCi:
    """`should_fail_ci` opt-in via env var, ne compte que les alertes
    strictes (ERR_PERSIST + VOLUMETRY_COLLAPSE)."""

    def test_returns_false_without_env_var(self, monkeypatch):
        monkeypatch.delenv("STRICT_MONITORING", raising=False)
        alerts = [
            monitoring.Alert("ERR_PERSIST", "a", "m"),
            monitoring.Alert("ERR_PERSIST", "b", "m"),
            monitoring.Alert("ERR_PERSIST", "c", "m"),
        ]
        assert monitoring.should_fail_ci(alerts) is False

    def test_returns_true_above_threshold_with_env_var(self, monkeypatch):
        monkeypatch.setenv("STRICT_MONITORING", "1")
        alerts = [
            monitoring.Alert("ERR_PERSIST", "a", "m"),
            monitoring.Alert("ERR_PERSIST", "b", "m"),
            monitoring.Alert("VOLUMETRY_COLLAPSE", "*", "m"),
        ]
        assert monitoring.should_fail_ci(alerts) is True

    def test_returns_false_below_threshold(self, monkeypatch):
        monkeypatch.setenv("STRICT_MONITORING", "1")
        alerts = [
            monitoring.Alert("ERR_PERSIST", "a", "m"),
            monitoring.Alert("ERR_PERSIST", "b", "m"),
        ]
        assert monitoring.should_fail_ci(alerts) is False

    def test_ignores_non_strict_kinds(self, monkeypatch):
        """FEED_STALE + FORMAT_DRIFT ne comptent pas (cascade CMS)."""
        monkeypatch.setenv("STRICT_MONITORING", "1")
        alerts = [
            monitoring.Alert("FEED_STALE", "a", "m"),
            monitoring.Alert("FEED_STALE", "b", "m"),
            monitoring.Alert("FORMAT_DRIFT", "c", "m"),
            monitoring.Alert("FORMAT_DRIFT", "d", "m"),
        ]
        assert monitoring.should_fail_ci(alerts) is False

    def test_custom_env_var(self, monkeypatch):
        monkeypatch.delenv("STRICT_MONITORING", raising=False)
        monkeypatch.setenv("MY_STRICT", "yes")
        alerts = [
            monitoring.Alert("ERR_PERSIST", "a", "m"),
            monitoring.Alert("ERR_PERSIST", "b", "m"),
            monitoring.Alert("ERR_PERSIST", "c", "m"),
        ]
        assert monitoring.should_fail_ci(alerts, env_var="MY_STRICT") is True


class TestDigestBlockVolumetryLabel:
    """Le rendu doit savoir traduire VOLUMETRY_COLLAPSE en label humain."""

    def test_volumetry_label_in_block(self):
        alerts = [monitoring.Alert(
            "VOLUMETRY_COLLAPSE", "*",
            "Volume en chute : 20 items contre 100",
        )]
        html = monitoring.render_digest_block(alerts)
        assert "Volume en chute" in html
