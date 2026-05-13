"""Tests R42-AE → R42-BT (2026-05-13) — fenêtres dynamiques d'AFFICHAGE
sont désormais vides : la logique dynamique nominal/full a été déplacée
côté INGESTION (cf. test_r42bt_ingestion_windows.py).

Cyril 2026-05-13 : « je veux un site exhaustif sur les fenêtres fixées,
et dont l'actualisation est accélérée par le fait de ne pas étudier ce
qui a déjà été étudié ». Conséquence : les mappings dyn d'affichage
sont vidés, l'export retombe sur les fenêtres statiques par catégorie /
source_id (`WINDOW_DAYS_BY_CATEGORY` / `WINDOW_DAYS_BY_SOURCE_ID`).

Ce fichier vérifie l'INVARIANT post-R42-BT :
- Les deux dicts `_DYNAMIC_WINDOWS_*` sont vides.
- L'export retombe systématiquement sur la fenêtre statique.
"""
from __future__ import annotations

import pytest

from src.site_export import (
    _window_for,
    _DYNAMIC_WINDOWS_BY_CATEGORY,
    _DYNAMIC_WINDOWS_BY_SOURCE_ID,
    WINDOW_DAYS_BY_CATEGORY,
    WINDOW_DAYS_BY_SOURCE_ID,
)


# ---------------------------------------------------------------------------
# Invariant : les mappings dynamiques d'affichage sont vidés
# ---------------------------------------------------------------------------

def test_dyn_categories_vides():
    """R42-BT : la dynamique d'affichage n'est plus utilisée — la
    logique a migré côté ingestion."""
    assert _DYNAMIC_WINDOWS_BY_CATEGORY == {}


def test_dyn_sources_vides():
    assert _DYNAMIC_WINDOWS_BY_SOURCE_ID == {}


# ---------------------------------------------------------------------------
# Mode `nominal` — fenêtre statique appliquée (pas de réduction)
# ---------------------------------------------------------------------------

class TestNominalUseStatic:
    @pytest.fixture(autouse=True)
    def _nominal(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "nominal")

    def test_comptes_rendus_fallback_static_180j(self):
        """Plus de dyn 15j → on retombe sur WINDOW_DAYS_BY_CATEGORY=180j."""
        assert _window_for("comptes_rendus") == WINDOW_DAYS_BY_CATEGORY["comptes_rendus"]
        assert _window_for("comptes_rendus") == 180

    def test_jorf_fallback_static_90j(self):
        assert _window_for("jorf") == WINDOW_DAYS_BY_CATEGORY["jorf"]
        assert _window_for("jorf") == 90

    def test_an_rapports_static_730j(self):
        """Les rapports parlementaires gardent leur override statique 730j."""
        assert _window_for("communiques", source_id="an_rapports") == 730
        assert _window_for("communiques", source_id="senat_rapports") == 730


# ---------------------------------------------------------------------------
# Mode `full` — même comportement (statique aussi)
# ---------------------------------------------------------------------------

class TestFullSameAsNominal:
    @pytest.fixture(autouse=True)
    def _full(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "full")

    def test_comptes_rendus_same_static_180j(self):
        """L'affichage est identique en nominal et full : c'est l'ingestion
        qui change, pas l'export."""
        assert _window_for("comptes_rendus") == 180

    def test_jorf_same_static_90j(self):
        assert _window_for("jorf") == 90


# ---------------------------------------------------------------------------
# RUN_MODE absent → comme nominal, retombe sur fenêtre statique
# ---------------------------------------------------------------------------

def test_run_mode_absent_uses_static(monkeypatch):
    monkeypatch.delenv("RUN_MODE", raising=False)
    assert _window_for("comptes_rendus") == WINDOW_DAYS_BY_CATEGORY["comptes_rendus"]
    assert _window_for("jorf") == WINDOW_DAYS_BY_CATEGORY["jorf"]


# ---------------------------------------------------------------------------
# Non-régression : dosleg garde sa logique dans assemblee._normalize_dosleg
# ---------------------------------------------------------------------------

def test_dossiers_legislatifs_pas_dans_dyn_site_export():
    """La fenêtre dosleg est gérée côté ingestion (assemblee.py R42-BT),
    pas côté site_export."""
    assert "dossiers_legislatifs" not in _DYNAMIC_WINDOWS_BY_CATEGORY
