"""Conftest global pour les tests.

R42-AD (2026-05-11) : par défaut, on force `RUN_MODE=full` pour tous
les tests. Cela préserve la rétrocompatibilité avec les tests pré-R42-AD
qui utilisent des fixtures (dossiers, items) dont la date est > 90 jours
— en mode nominal (default code), ces items seraient filtrés.

Les tests qui veulent valider le comportement nominal (cf.
`tests/test_r42ad_run_mode.py`) doivent explicitement pop ou
surcharger `RUN_MODE` via leur propre fixture locale (la fixture
locale s'exécute APRÈS le conftest autouse, donc le pop l'emporte).

R42-AI (2026-05-11) : par défaut on désactive aussi le cache haystack
dosleg pour les tests. Sans ça, les tests R42-L/R42-X qui mockent
`fetch_text` polluent `data/veille.sqlite3` avec des entrées de test
(ex. `senat_dosleg/ppl25-566` posé à 100k chars par le test truncate).
Les tests R42-AI eux-mêmes activent le cache via `monkeypatch.delenv`.
"""
import pytest


@pytest.fixture(autouse=True)
def _default_run_mode_full(monkeypatch):
    """Par défaut, RUN_MODE=full pour tous les tests."""
    monkeypatch.setenv("RUN_MODE", "full")


@pytest.fixture(autouse=True)
def _disable_dosleg_text_cache(monkeypatch):
    """Par défaut, cache haystack dosleg coupé (évite pollution DB prod
    par les tests qui mockent fetch_text)."""
    monkeypatch.setenv("VEILLE_DOSLEG_TEXT_CACHE_DISABLE", "1")
