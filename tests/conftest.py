"""Conftest global pour les tests.

R42-AD (2026-05-11) : par défaut, on force `RUN_MODE=full` pour tous
les tests. Cela préserve la rétrocompatibilité avec les tests pré-R42-AD
qui utilisent des fixtures (dossiers, items) dont la date est > 90 jours
— en mode nominal (default code), ces items seraient filtrés.

Les tests qui veulent valider le comportement nominal (cf.
`tests/test_r42ad_run_mode.py`) doivent explicitement pop ou
surcharger `RUN_MODE` via leur propre fixture locale (la fixture
locale s'exécute APRÈS le conftest autouse, donc le pop l'emporte).
"""
import pytest


@pytest.fixture(autouse=True)
def _default_run_mode_full(monkeypatch):
    """Par défaut, RUN_MODE=full pour tous les tests."""
    monkeypatch.setenv("RUN_MODE", "full")
