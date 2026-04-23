"""Tests unitaires pour ping_state (R24 — snapshot des UIDs matchés)."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src import ping_state


# ---------- load / save round-trip ----------

def test_load_file_absent_returns_default(tmp_path):
    state = ping_state.load(tmp_path / "nope.json")
    assert state == {"last_run_at": None, "last_ping_at": None, "pinged_uids": {}}


def test_load_corrupted_json_returns_default(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not-valid-json", encoding="utf-8")
    state = ping_state.load(p)
    assert state["pinged_uids"] == {}
    assert state["last_run_at"] is None


def test_load_non_dict_payload_returns_default(tmp_path):
    """Un JSON valide mais non-dict (ex. liste) → fallback vide."""
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    state = ping_state.load(p)
    assert state["pinged_uids"] == {}


def test_save_and_reload_round_trip(tmp_path):
    p = tmp_path / "ping_state.json"
    now = datetime(2026, 4, 23, 4, 0, 0, tzinfo=timezone.utc)
    buckets = {
        "dossiers_legislatifs": ["an_dossier::DL123", "senat_akn::DLS456"],
        "amendements": ["an_amendements::AM001"],
    }
    ping_state.save(p, last_run_at=now, pinged_uids=buckets)
    state = ping_state.load(p)
    assert state["last_run_at"] == "2026-04-23T04:00:00+00:00"
    assert state["last_ping_at"] is None
    assert state["pinged_uids"]["dossiers_legislatifs"] == [
        "an_dossier::DL123", "senat_akn::DLS456",
    ]
    assert state["pinged_uids"]["amendements"] == ["an_amendements::AM001"]


def test_save_serializes_sets_as_sorted_lists(tmp_path):
    p = tmp_path / "ping_state.json"
    # On passe un set non ordonné — sortie doit être triée et déterministe.
    ping_state.save(
        p,
        last_run_at=None,
        pinged_uids={"amendements": {"z", "a", "m"}},
    )
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["pinged_uids"]["amendements"] == ["a", "m", "z"]


def test_save_tolerates_missing_pinged_uids(tmp_path):
    """save sans pinged_uids → dict vide, pas d'exception."""
    p = tmp_path / "ping_state.json"
    ping_state.save(p, last_run_at=datetime.now(timezone.utc))
    state = ping_state.load(p)
    assert state["pinged_uids"] == {}


def test_save_naive_datetime_gets_utc_tz(tmp_path):
    """Un datetime naïf est interprété comme UTC (ne pas silencieusement dropper)."""
    p = tmp_path / "ping_state.json"
    naive = datetime(2026, 4, 23, 15, 30, 0)
    ping_state.save(p, last_run_at=naive, pinged_uids={})
    state = ping_state.load(p)
    assert state["last_run_at"].startswith("2026-04-23T15:30:00")
    assert "+00:00" in state["last_run_at"]


def test_save_is_atomic_no_tmp_leftover(tmp_path):
    """Après save, aucun .tmp ne reste dans le répertoire."""
    p = tmp_path / "ping_state.json"
    ping_state.save(p, last_run_at=None, pinged_uids={"amendements": ["a::1"]})
    leftovers = [f for f in tmp_path.iterdir() if f.name.startswith(".ping_state.")]
    assert leftovers == []


# ---------- snapshot_from_rows ----------

def _row(source_id, uid, category, matched=None, hash_key=None):
    """Helper : row DB-like avec matched_keywords en JSON string (comme Store)."""
    mk = matched if matched is not None else ["sport"]
    return {
        "source_id": source_id,
        "uid": uid,
        "category": category,
        "hash_key": hash_key or f"{source_id}::{uid}",
        "matched_keywords": json.dumps(mk) if not isinstance(mk, str) else mk,
    }


def test_snapshot_filters_by_category():
    rows = [
        _row("an_dossier", "DL1", "dossiers_legislatifs"),
        _row("an_agenda", "AG1", "agenda"),  # hors liste
        _row("an_amendements", "AM1", "amendements"),
        _row("senat_cri", "CR1", "comptes_rendus"),
        _row("jorf", "J1", "jorf"),  # hors liste
    ]
    snap = ping_state.snapshot_from_rows(rows)
    assert set(snap.keys()) == set(ping_state.PING_CATEGORIES)
    assert snap["dossiers_legislatifs"] == ["an_dossier::DL1"]
    assert snap["amendements"] == ["an_amendements::AM1"]
    assert snap["comptes_rendus"] == ["senat_cri::CR1"]
    assert snap["questions"] == []


def test_snapshot_skips_unmatched():
    """matched_keywords == '[]' → item ignoré."""
    rows = [
        _row("an_dossier", "DL1", "dossiers_legislatifs", matched=[]),
        _row("an_dossier", "DL2", "dossiers_legislatifs", matched=["sport"]),
    ]
    snap = ping_state.snapshot_from_rows(rows)
    assert snap["dossiers_legislatifs"] == ["an_dossier::DL2"]


def test_snapshot_uses_existing_hash_key_if_present():
    """Si la row a déjà un hash_key en colonne, on le prend tel quel."""
    rows = [
        _row("an_dossier", "DL1", "dossiers_legislatifs", hash_key="custom::abc"),
    ]
    snap = ping_state.snapshot_from_rows(rows)
    assert snap["dossiers_legislatifs"] == ["custom::abc"]


def test_snapshot_skips_rows_without_source_or_uid():
    rows = [
        {"source_id": "", "uid": "X", "category": "amendements",
         "matched_keywords": '["sport"]'},
        {"source_id": "an", "uid": "", "category": "amendements",
         "matched_keywords": '["sport"]'},
        {"source_id": "an", "uid": "A", "category": "amendements",
         "matched_keywords": '["sport"]'},
    ]
    snap = ping_state.snapshot_from_rows(rows)
    assert snap["amendements"] == ["an::A"]


def test_snapshot_accepts_list_matched_keywords():
    """Rows in-memory (non issus de la DB) peuvent avoir matched_keywords
    comme liste Python, pas comme JSON string. Les deux doivent marcher."""
    rows = [
        {"source_id": "an", "uid": "A", "category": "amendements",
         "matched_keywords": ["sport"]},
        {"source_id": "an", "uid": "B", "category": "amendements",
         "matched_keywords": []},
    ]
    snap = ping_state.snapshot_from_rows(rows)
    assert snap["amendements"] == ["an::A"]


def test_snapshot_output_is_sorted():
    """La sortie doit être triée pour diff git stable (z, a, m → a, m, z)."""
    rows = [
        _row("src", "z", "amendements"),
        _row("src", "a", "amendements"),
        _row("src", "m", "amendements"),
    ]
    snap = ping_state.snapshot_from_rows(rows)
    assert snap["amendements"] == ["src::a", "src::m", "src::z"]


def test_snapshot_custom_categories_tuple():
    rows = [
        _row("src", "1", "dossiers_legislatifs"),
        _row("src", "2", "agenda"),
    ]
    snap = ping_state.snapshot_from_rows(rows, categories=("agenda",))
    assert list(snap.keys()) == ["agenda"]
    assert snap["agenda"] == ["src::2"]


# ---------- diff_new ----------

def test_diff_new_detects_added_uids():
    baseline = {"amendements": ["src::1", "src::2"]}
    current = {"amendements": ["src::1", "src::2", "src::3"]}
    diff = ping_state.diff_new(current, baseline)
    assert diff == {"amendements": ["src::3"]}


def test_diff_new_empty_when_no_change():
    baseline = {"amendements": ["src::1"]}
    current = {"amendements": ["src::1"]}
    diff = ping_state.diff_new(current, baseline)
    assert diff == {}


def test_diff_new_ignores_removed_uids():
    """Un UID qui disparaît n'est PAS une nouveauté — diff ne le remonte pas."""
    baseline = {"amendements": ["src::1", "src::2", "src::3"]}
    current = {"amendements": ["src::1"]}
    diff = ping_state.diff_new(current, baseline)
    assert diff == {}


def test_diff_new_handles_new_category_without_baseline():
    baseline = {}
    current = {"amendements": ["src::1"], "questions": ["src::q1"]}
    diff = ping_state.diff_new(current, baseline)
    assert diff == {"amendements": ["src::1"], "questions": ["src::q1"]}


def test_diff_new_filters_by_categories_arg():
    baseline = {}
    current = {
        "amendements": ["src::1"],
        "jorf": ["src::j"],  # hors PING_CATEGORIES
    }
    diff = ping_state.diff_new(current, baseline, categories=("amendements",))
    assert diff == {"amendements": ["src::1"]}
    assert "jorf" not in diff


def test_diff_new_output_sorted():
    """Les UIDs du diff sont triés (déterminisme email + logs)."""
    baseline = {}
    current = {"amendements": ["src::z", "src::a", "src::m"]}
    diff = ping_state.diff_new(current, baseline)
    assert diff["amendements"] == ["src::a", "src::m", "src::z"]


# ---------- merge ----------

def test_merge_unions_values():
    base = {"amendements": ["a::1", "a::2"]}
    new = {"amendements": ["a::2", "a::3"]}
    merged = ping_state.merge(base, new)
    assert merged["amendements"] == ["a::1", "a::2", "a::3"]


def test_merge_preserves_baseline_categories_absent_from_new():
    base = {"amendements": ["a::1"], "questions": ["q::1"]}
    new = {"amendements": ["a::2"]}
    merged = ping_state.merge(base, new)
    assert merged["amendements"] == ["a::1", "a::2"]
    assert merged["questions"] == ["q::1"]


def test_merge_with_empty_baseline():
    merged = ping_state.merge({}, {"amendements": ["a::1"]})
    assert merged == {"amendements": ["a::1"]}


def test_merge_with_empty_new():
    merged = ping_state.merge({"amendements": ["a::1"]}, {})
    assert merged == {"amendements": ["a::1"]}


def test_merge_deterministic_sort():
    merged = ping_state.merge({}, {"amendements": {"z", "a", "m"}})
    assert merged["amendements"] == ["a", "m", "z"]


# ---------- PING_CATEGORIES ----------

def test_ping_categories_are_the_four_hot_ones():
    """Assertion explicite : les 4 catégories chaudes, pas agenda/jorf/etc."""
    assert set(ping_state.PING_CATEGORIES) == {
        "dossiers_legislatifs",
        "amendements",
        "questions",
        "comptes_rendus",
    }


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
