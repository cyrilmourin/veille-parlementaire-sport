"""Tests R42-BD — `reset_category.py` préserve les items des sources
actuellement KO (last_fetched=0) pour ne pas perdre l'historique.

Cyril 2026-05-11 : « ne plus perdre les Min Sports etc. lors d'un
reset_category=communiques quand leur scraper échoue temporairement
(WAF, ConnectTimeout) ».
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "reset_category.py"


def _seed_db(db_path: Path) -> None:
    """Crée une mini-DB avec 3 sources, une catégorie commune."""
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE items (
        hash_key TEXT PRIMARY KEY, source_id TEXT, uid TEXT, category TEXT,
        chamber TEXT, title TEXT, url TEXT, published_at TEXT,
        summary TEXT, matched_keywords TEXT, keyword_families TEXT,
        raw TEXT, inserted_at TEXT
    )""")
    rows = [
        # min_sports_presse : 5 items existants, source KO actuellement
        *[(f"hk_minsp_{i}", "min_sports_presse", f"uid{i}", "communiques",
           "MinSports", f"t{i}", f"u{i}", "2026-04-01",
           "", "[]", "[]", "{}", "2026-05-01") for i in range(5)],
        # senat_rapports : 10 items existants, source OK
        *[(f"hk_senat_{i}", "senat_rapports", f"uid{i}", "communiques",
           "Senat", f"t{i}", f"u{i}", "2026-04-01",
           "", "[]", "[]", "{}", "2026-05-01") for i in range(10)],
        # cnosf : 3 items, source OK
        *[(f"hk_cnosf_{i}", "cnosf", f"uid{i}", "communiques",
           "CNOSF", f"t{i}", f"u{i}", "2026-04-01",
           "", "[]", "[]", "{}", "2026-05-01") for i in range(3)],
    ]
    conn.executemany(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _seed_health(health_path: Path, failing: list[str]) -> None:
    """Crée un pipeline_health.json avec last_fetched=0 sur `failing`.
    Toutes les sources mentionnées (3 standards + failing) sont incluses."""
    fixed_sids = ("min_sports_presse", "senat_rapports", "cnosf")
    all_sids = set(fixed_sids) | set(failing)
    sources = {}
    for sid in all_sids:
        sources[sid] = {
            "last_fetched": 0 if sid in failing else 10,
            "last_ok_at": "2026-05-12T06:30:00",
            "consecutive_errors": 0,
            "last_error": None,
        }
    health_path.write_text(json.dumps({
        "last_run_at": "2026-05-12T06:37:18",
        "schema_version": 2,
        "sources": sources,
    }), encoding="utf-8")


def _run_script(db_path: Path, health_path: Path, *args: str) -> tuple[int, str]:
    """Exécute le script avec DB + HEALTH monkeypatchés via env path."""
    env_script = f"""
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import scripts.reset_category as r
r.DB = __import__('pathlib').Path({str(db_path)!r})
r.HEALTH = __import__('pathlib').Path({str(health_path)!r})
sys.argv = ['reset_category.py'] + {list(args)!r}
r.main()
"""
    result = subprocess.run(
        [sys.executable, "-c", env_script],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    return result.returncode, result.stdout + result.stderr


def _count(db_path: Path, where_sql: str = "") -> int:
    conn = sqlite3.connect(db_path)
    sql = "SELECT COUNT(*) FROM items"
    if where_sql:
        sql += " WHERE " + where_sql
    (n,) = conn.execute(sql).fetchone()
    conn.close()
    return n


# ---------------------------------------------------------------------------
# _load_failing_sources
# ---------------------------------------------------------------------------

def test_load_failing_sources_returns_set(tmp_path):
    """Lit `pipeline_health.json` et retourne les sources fetched=0."""
    sys.path.insert(0, str(REPO_ROOT))
    import scripts.reset_category as r
    r.HEALTH = tmp_path / "health.json"
    _seed_health(r.HEALTH, failing=["min_sports_presse", "info_gouv_actualites"])
    failing = r._load_failing_sources()
    assert failing == {"min_sports_presse", "info_gouv_actualites"}


def test_load_failing_sources_health_absent_returns_empty(tmp_path):
    """Pas de fichier → set vide (no-op compat)."""
    sys.path.insert(0, str(REPO_ROOT))
    import scripts.reset_category as r
    r.HEALTH = tmp_path / "absent.json"
    assert r._load_failing_sources() == set()


def test_load_failing_sources_invalid_json_returns_empty(tmp_path):
    """Fichier corrompu → set vide (soft-fail)."""
    sys.path.insert(0, str(REPO_ROOT))
    import scripts.reset_category as r
    r.HEALTH = tmp_path / "bad.json"
    r.HEALTH.write_text("not json", encoding="utf-8")
    assert r._load_failing_sources() == set()


# ---------------------------------------------------------------------------
# Comportement end-to-end via subprocess
# ---------------------------------------------------------------------------

def test_e2e_preserve_failing_source_par_defaut(tmp_path):
    """Reset communiques : min_sports_presse (KO) préservé, autres purgés."""
    db = tmp_path / "test.sqlite3"
    health = tmp_path / "health.json"
    _seed_db(db)
    _seed_health(health, failing=["min_sports_presse"])

    rc, out = _run_script(db, health, "communiques", "--yes")
    assert rc == 0, out
    assert "préservés" in out or "preserves" in out
    assert "min_sports_presse" in out
    # min_sports_presse conservé (5 items), senat_rapports + cnosf purgés
    assert _count(db, "source_id='min_sports_presse'") == 5
    assert _count(db, "source_id='senat_rapports'") == 0
    assert _count(db, "source_id='cnosf'") == 0


def test_e2e_force_purge_meme_sources_ko(tmp_path):
    """Flag --force : purge TOUT y compris les sources KO."""
    db = tmp_path / "test.sqlite3"
    health = tmp_path / "health.json"
    _seed_db(db)
    _seed_health(health, failing=["min_sports_presse"])

    rc, out = _run_script(db, health, "communiques", "--yes", "--force")
    assert rc == 0, out
    # Tout purgé
    assert _count(db) == 0


def test_e2e_source_id_explicite_bypass_protection(tmp_path):
    """--source-id ciblé → la protection R42-BD ne s'applique pas
    (l'utilisateur a explicitement demandé cette source précise)."""
    db = tmp_path / "test.sqlite3"
    health = tmp_path / "health.json"
    _seed_db(db)
    _seed_health(health, failing=["min_sports_presse"])

    rc, out = _run_script(db, health, "communiques",
                          "--source-id", "min_sports_presse", "--yes")
    assert rc == 0, out
    # min_sports_presse purgé malgré sa health KO
    assert _count(db, "source_id='min_sports_presse'") == 0
    # Les autres sources non touchées
    assert _count(db, "source_id='senat_rapports'") == 10


def test_e2e_pas_de_health_file_purge_tout(tmp_path):
    """Si pipeline_health.json absent : comportement legacy (purge tout)."""
    db = tmp_path / "test.sqlite3"
    health = tmp_path / "absent.json"  # n'existe pas
    _seed_db(db)
    rc, out = _run_script(db, health, "communiques", "--yes")
    assert rc == 0, out
    assert _count(db) == 0


def test_e2e_dry_run_naffiche_que_le_count(tmp_path):
    """--dry-run : count + préservation affichés, AUCUNE suppression."""
    db = tmp_path / "test.sqlite3"
    health = tmp_path / "health.json"
    _seed_db(db)
    _seed_health(health, failing=["min_sports_presse"])

    before = _count(db)
    rc, out = _run_script(db, health, "communiques", "--dry-run", "--yes")
    assert rc == 0, out
    assert "préservés" in out or "preserves" in out
    after = _count(db)
    assert before == after  # rien supprimé
