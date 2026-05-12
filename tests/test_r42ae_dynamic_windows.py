"""Tests R42-AE — fenêtres dynamiques nominal/full selon RUN_MODE.

Cyril 2026-05-11 : « Comptes rendus mode nominal 15j / full 60j ;
JORF nominal 7j / full 90j ; rapports nominal 15j / full 730j ».

Symétrique R42-AD (dosleg).
"""
from __future__ import annotations

import pytest

from src.site_export import (
    _window_for,
    _DYNAMIC_WINDOWS_BY_CATEGORY,
    _DYNAMIC_WINDOWS_BY_SOURCE_ID,
    WINDOW_DAYS_BY_CATEGORY,
)


# ---------------------------------------------------------------------------
# Mode `nominal` — fenêtres courtes
# ---------------------------------------------------------------------------

class TestNominal:
    @pytest.fixture(autouse=True)
    def _nominal(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "nominal")

    def test_comptes_rendus_15j(self):
        assert _window_for("comptes_rendus") == 15

    def test_jorf_7j(self):
        assert _window_for("jorf") == 7

    # R42-BS (2026-05-13) — tests retirés : les rapports parlementaires
    # ont été sortis de la dynamique nominale 15j car le volume est trop
    # faible (Cyril a constaté la disparition quasi totale des
    # publications parlementaires). Ils repassent sur la fenêtre statique
    # 730j (cf. test_r42bs_chamber_bypass_window_excludes.py).

    def test_non_dynamic_categorie_garde_fenetre_statique(self):
        """Catégorie non listée (ex. communiques pour une source non
        dyn) → fenêtre statique."""
        assert _window_for("communiques", source_id="elysee_feed") == \
               WINDOW_DAYS_BY_CATEGORY["communiques"]


# ---------------------------------------------------------------------------
# Mode `full` — fenêtres larges
# ---------------------------------------------------------------------------

class TestFull:
    @pytest.fixture(autouse=True)
    def _full(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "full")

    def test_comptes_rendus_60j(self):
        assert _window_for("comptes_rendus") == 60

    def test_jorf_90j(self):
        assert _window_for("jorf") == 90

    # R42-BS : tests an_rapports/senat_rapports retirés (sortis de la
    # dynamique). Voir test_r42bs_chamber_bypass_window_excludes.py.


# ---------------------------------------------------------------------------
# RUN_MODE absent : default = nominal (cf. run_mode.is_full_mode)
# ---------------------------------------------------------------------------

def test_run_mode_absent_default_nominal(monkeypatch):
    monkeypatch.delenv("RUN_MODE", raising=False)
    assert _window_for("comptes_rendus") == 15
    assert _window_for("jorf") == 7


# ---------------------------------------------------------------------------
# Mappings cohérents
# ---------------------------------------------------------------------------

def test_dyn_categories_definies():
    assert "comptes_rendus" in _DYNAMIC_WINDOWS_BY_CATEGORY
    assert "jorf" in _DYNAMIC_WINDOWS_BY_CATEGORY
    # nominal < full pour toutes
    for cat, (n, f) in _DYNAMIC_WINDOWS_BY_CATEGORY.items():
        assert n < f, f"{cat} nominal={n} >= full={f}"


def test_dyn_sources_invariant():
    """R42-BS : le mapping `_DYNAMIC_WINDOWS_BY_SOURCE_ID` est vide
    désormais (les rapports parlementaires en sont sortis). On garde
    juste l'invariant nominal < full au cas où on en rajouterait."""
    for sid, (n, f) in _DYNAMIC_WINDOWS_BY_SOURCE_ID.items():
        assert n < f, f"{sid} nominal={n} >= full={f}"


# ---------------------------------------------------------------------------
# Non-régression : dosleg via R42-AD (dans assemblee.py, hors site_export)
# ---------------------------------------------------------------------------

def test_dossiers_legislatifs_pas_dans_dyn_site_export():
    """La fenêtre dosleg est gérée côté `assemblee._normalize_dosleg`
    (R42-AD), pas côté site_export. Cohérence : pas dans nos mappings
    dynamiques ici."""
    assert "dossiers_legislatifs" not in _DYNAMIC_WINDOWS_BY_CATEGORY
