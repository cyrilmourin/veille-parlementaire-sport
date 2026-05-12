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

    def test_an_rapports_15j(self):
        assert _window_for("communiques", source_id="an_rapports") == 15

    def test_senat_rapports_15j(self):
        assert _window_for("communiques", source_id="senat_rapports") == 15

    def test_an_rapports_information_15j(self):
        """R42-AJ : extension scraper aux RINF couverte aussi par les
        fenêtres dynamiques."""
        assert _window_for("communiques", source_id="an_rapports_information") == 15

    def test_an_avis_15j(self):
        assert _window_for("communiques", source_id="an_avis") == 15

    def test_source_id_prime_sur_category(self):
        """an_rapports a source_id-dyn (15j), pas la fenêtre statique
        communiques (90j)."""
        assert _window_for("communiques", source_id="an_rapports") == 15

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

    def test_an_rapports_730j(self):
        assert _window_for("communiques", source_id="an_rapports") == 730

    def test_senat_rapports_730j(self):
        assert _window_for("communiques", source_id="senat_rapports") == 730


# ---------------------------------------------------------------------------
# RUN_MODE absent : default = nominal (cf. run_mode.is_full_mode)
# ---------------------------------------------------------------------------

def test_run_mode_absent_default_nominal(monkeypatch):
    monkeypatch.delenv("RUN_MODE", raising=False)
    assert _window_for("comptes_rendus") == 15
    assert _window_for("jorf") == 7
    assert _window_for("communiques", source_id="an_rapports") == 15


# ---------------------------------------------------------------------------
# Mappings cohérents
# ---------------------------------------------------------------------------

def test_dyn_categories_definies():
    assert "comptes_rendus" in _DYNAMIC_WINDOWS_BY_CATEGORY
    assert "jorf" in _DYNAMIC_WINDOWS_BY_CATEGORY
    # nominal < full pour toutes
    for cat, (n, f) in _DYNAMIC_WINDOWS_BY_CATEGORY.items():
        assert n < f, f"{cat} nominal={n} >= full={f}"


def test_dyn_sources_definies():
    """Tous les types de rapports parlementaires R42-AJ/AK couverts."""
    for sid in ("an_rapports", "an_rapports_information", "an_avis",
                "an_rapports_application_loi", "an_rapports_information_ce",
                "senat_rapports"):
        assert sid in _DYNAMIC_WINDOWS_BY_SOURCE_ID, f"{sid} absent dyn"
    # nominal < full
    for sid, (n, f) in _DYNAMIC_WINDOWS_BY_SOURCE_ID.items():
        assert n < f


# ---------------------------------------------------------------------------
# Non-régression : dosleg via R42-AD (dans assemblee.py, hors site_export)
# ---------------------------------------------------------------------------

def test_dossiers_legislatifs_pas_dans_dyn_site_export():
    """La fenêtre dosleg est gérée côté `assemblee._normalize_dosleg`
    (R42-AD), pas côté site_export. Cohérence : pas dans nos mappings
    dynamiques ici."""
    assert "dossiers_legislatifs" not in _DYNAMIC_WINDOWS_BY_CATEGORY
