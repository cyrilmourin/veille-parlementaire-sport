"""R42-AD (2026-05-11) — Fenêtres dynamiques nominal/full selon RUN_MODE.

Couvre :
- `run_mode.is_full_mode()` : détection env var
- `run_mode.window_days()` : helper de sélection
- Intégration : code source assemblee.py et senat.py utilise window_days
- Le workflow daily.yml set RUN_MODE selon reset_category / reset_db

Régression : permet aux runs nominaux quotidiens de skipper les ~2500
fetches `/dyn/opendata/` AN inutiles (les dossiers déjà en DB conservent
leur match via upsert_many hash_key constant).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _reset_env_run_mode():
    """Isole RUN_MODE entre tests."""
    saved = os.environ.pop("RUN_MODE", None)
    yield
    if saved is not None:
        os.environ["RUN_MODE"] = saved
    else:
        os.environ.pop("RUN_MODE", None)


# --------------------------- run_mode module
def test_is_full_mode_default_is_nominal():
    """Sans env var, on est en nominal."""
    from src.run_mode import is_full_mode
    assert is_full_mode() is False


def test_is_full_mode_env_full():
    os.environ["RUN_MODE"] = "full"
    from src.run_mode import is_full_mode
    assert is_full_mode() is True


def test_is_full_mode_env_full_case_insensitive():
    os.environ["RUN_MODE"] = "FULL"
    from src.run_mode import is_full_mode
    assert is_full_mode() is True


def test_is_full_mode_env_nominal_explicit():
    os.environ["RUN_MODE"] = "nominal"
    from src.run_mode import is_full_mode
    assert is_full_mode() is False


def test_is_full_mode_env_other_value():
    """Toute valeur autre que `full` est traitée comme nominal."""
    os.environ["RUN_MODE"] = "foo"
    from src.run_mode import is_full_mode
    assert is_full_mode() is False


def test_window_days_nominal_default():
    from src.run_mode import window_days
    assert window_days(nominal=90, full=1095) == 90


def test_window_days_full():
    os.environ["RUN_MODE"] = "full"
    from src.run_mode import window_days
    assert window_days(nominal=90, full=1095) == 1095


# --------------------------- assemblee.py intégration
def test_assemblee_dosleg_window_constants_exist():
    """Les constantes nominal/full doivent exister dans assemblee.py."""
    from src.sources import assemblee
    assert assemblee._DOSLEG_MAX_AGE_ACTIVE_DAYS_NOMINAL == 90
    assert assemblee._DOSLEG_MAX_AGE_ACTIVE_DAYS_FULL == 1095
    # Compat constant (lecture pure)
    assert assemblee._DOSLEG_MAX_AGE_ACTIVE_DAYS == 1095


def test_assemblee_normalize_dosleg_uses_window_days():
    """Le code source doit appeler window_days(nominal=..., full=...)."""
    from src.sources import assemblee as an_mod
    code = Path(an_mod.__file__).read_text(encoding="utf-8")
    assert "from ..run_mode import window_days" in code
    assert "_DOSLEG_MAX_AGE_ACTIVE_DAYS_NOMINAL" in code
    assert "_DOSLEG_MAX_AGE_ACTIVE_DAYS_FULL" in code


# --------------------------- senat.py intégration
def test_senat_body_window_dynamique():
    """Le code source senat.py doit utiliser window_days pour body_window_days."""
    from src.sources import senat as senat_mod
    code = Path(senat_mod.__file__).read_text(encoding="utf-8")
    # 2 usages : senat_dosleg body_window_days + senat_rapports rap_window_days
    assert code.count("window_days(nominal=90, full=800)") >= 2


# --------------------------- Workflow daily.yml
def test_workflow_sets_run_mode():
    """Le workflow daily.yml doit set RUN_MODE selon reset_*."""
    wf_path = _ROOT / ".github/workflows/daily.yml"
    code = wf_path.read_text(encoding="utf-8")
    # R42-AD marqueur
    assert "R42-AD" in code
    # Logique de set RUN_MODE
    assert "RUN_MODE=full" in code
    assert "RUN_MODE=nominal" in code
    # Vérifie qu'il y a la condition sur reset_category ou reset_db
    assert "reset_category" in code and "reset_db" in code
