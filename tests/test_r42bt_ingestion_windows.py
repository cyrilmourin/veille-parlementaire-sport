"""R42-BT — Fenêtres dynamiques INGESTION nominal/full.

Cyril 2026-05-13 : « je veux un site exhaustif sur les fenêtres fixées,
et dont l'actualisation est accélérée par le fait de ne pas étudier ce
qui a déjà été étudié ».

Déplacement de la logique nominal/full du côté EXPORT (filtre d'affichage)
au côté INGESTION (filtre de parsing). Conséquence :
- Cron quotidien (nominal) : ne parse que les items des 15 derniers jours.
- Reset (RUN_MODE=full) : rouvre la fenêtre 730j (sources lourdes AN/Sénat)
  ou 800j (corps PDF), ou 8 pages (pagination AN rapports).
- L'affichage reste exhaustif sur la fenêtre statique (180j CR, 90j JORF,
  730j rapports parlementaires…).

Ce fichier valide :
1. Les scrapers consomment bien `window_days(nominal=15, full=...)`.
2. La pagination AN rapports passe à 1 en nominal, max_pages YAML en full.
3. Le filtre `since_days` AN (zip) respecte le mode.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. Helper run_mode : nominal vs full
# ---------------------------------------------------------------------------

def test_window_days_nominal(monkeypatch):
    from src.run_mode import window_days
    monkeypatch.delenv("RUN_MODE", raising=False)
    assert window_days(nominal=15, full=730) == 15


def test_window_days_full(monkeypatch):
    from src.run_mode import window_days
    monkeypatch.setenv("RUN_MODE", "full")
    assert window_days(nominal=15, full=730) == 730


# ---------------------------------------------------------------------------
# 2. AN json_zip / xml_zip — fenêtre nominal 15 / full ≥ 730
# ---------------------------------------------------------------------------

def test_assemblee_uses_nominal_15_full_730():
    """Le code source assemblee.py doit appliquer window_days dans
    `fetch_source` (json_zip) ET `_fetch_xml_zip`. R42-CP (2026-05-15) :
    `fetch_source` utilise désormais un `nominal_days` dynamique selon
    la catégorie (365 pour les questions, 15 sinon)."""
    code = (_ROOT / "src/sources/assemblee.py").read_text(encoding="utf-8")
    # _fetch_xml_zip (compte rendus) garde nominal=15 fixe.
    assert code.count("window_days(nominal=15, full=max(yaml_full, 730))") >= 1
    # fetch_source (json_zip) utilise nominal_days variable (R42-CP).
    assert "window_days(nominal=nominal_days, full=max(yaml_full, 730))" in code
    assert 'nominal_days = 365 if cat_for_window == "questions" else 15' in code


def test_assemblee_nominal_appliqué(monkeypatch):
    """En nominal, le scraper AN cap la fenêtre à 15j peu importe le YAML."""
    monkeypatch.delenv("RUN_MODE", raising=False)
    from src.run_mode import window_days
    # YAML say 30 → nominal cap à 15
    yaml_full = 30
    assert window_days(nominal=15, full=max(yaml_full, 730)) == 15
    # YAML say 90 → nominal cap à 15
    yaml_full = 90
    assert window_days(nominal=15, full=max(yaml_full, 730)) == 15


def test_assemblee_full_rouvre_730(monkeypatch):
    """En full, on rouvre à 730j minimum (cap inférieur du full)."""
    monkeypatch.setenv("RUN_MODE", "full")
    from src.run_mode import window_days
    # YAML say 30 → full prend max(30, 730) = 730
    assert window_days(nominal=15, full=max(30, 730)) == 730
    # YAML say 1000 → full prend max(1000, 730) = 1000
    assert window_days(nominal=15, full=max(1000, 730)) == 1000


# ---------------------------------------------------------------------------
# 3. Sénat débats/CRI — même règle 15/730
# ---------------------------------------------------------------------------

def test_senat_debats_uses_dynamic_window():
    """senat.py::_fetch_debats_zip doit utiliser window_days nominal=15."""
    code = (_ROOT / "src/sources/senat.py").read_text(encoding="utf-8")
    assert "window_days(nominal=15, full=max(yaml_full, 730))" in code


# ---------------------------------------------------------------------------
# 4. Sénat amendements per-texte — même règle
# ---------------------------------------------------------------------------

def test_senat_amendements_uses_dynamic_window():
    code = (_ROOT / "src/sources/senat_amendements.py").read_text(encoding="utf-8")
    assert "window_days(nominal=15, full=max(yaml_full, 730))" in code


# ---------------------------------------------------------------------------
# 5. Sénat body fetch (rapports + dosleg) — nominal=15 (et plus 90)
# ---------------------------------------------------------------------------

def test_senat_body_fetch_nominal_aligned_15():
    """R42-BT : senat_dosleg body + senat_rapports body alignés sur 15/800."""
    code = (_ROOT / "src/sources/senat.py").read_text(encoding="utf-8")
    # Pas de reliquat de l'ancien 90/800
    assert "window_days(nominal=90, full=800)" not in code
    # Nouvelle fenêtre 15/800 présente 2× (dosleg + rapports)
    assert code.count("window_days(nominal=15, full=800)") >= 2


# ---------------------------------------------------------------------------
# 6. AN rapports HTML — pagination nominal 1 / full = YAML
# ---------------------------------------------------------------------------

def test_an_rapports_pagination_nominal_1_full_yaml():
    """Le scraper assemblee_rapports.py doit limiter à 1 page en nominal,
    et lire `max_pages` YAML uniquement en mode full."""
    code = (_ROOT / "src/sources/assemblee_rapports.py").read_text(encoding="utf-8")
    assert "is_full_mode()" in code
    # Le code doit avoir l'expression `yaml_max_pages if is_full_mode() else 1`
    assert "yaml_max_pages if is_full_mode() else 1" in code


def test_an_rapports_max_pages_nominal_1(monkeypatch):
    """En nominal, max_pages effectif = 1 même si YAML dit 8."""
    monkeypatch.delenv("RUN_MODE", raising=False)
    from src.run_mode import is_full_mode
    yaml_max_pages = 8
    max_pages = yaml_max_pages if is_full_mode() else 1
    assert max_pages == 1


def test_an_rapports_max_pages_full_yaml(monkeypatch):
    """En full, max_pages effectif = YAML."""
    monkeypatch.setenv("RUN_MODE", "full")
    from src.run_mode import is_full_mode
    yaml_max_pages = 8
    max_pages = yaml_max_pages if is_full_mode() else 1
    assert max_pages == 8


# ---------------------------------------------------------------------------
# 7. JORF (dila) — days_back nominal 30 / full ≥ 60
# ---------------------------------------------------------------------------

def test_dila_jorf_days_back_dynamic():
    code = (_ROOT / "src/sources/dila_jorf.py").read_text(encoding="utf-8")
    assert "window_days(nominal=30, full=max(yaml_full, 60))" in code


def test_dila_nominal_30_eq_15_jours(monkeypatch):
    """30 éditions JORF ≈ 15 jours (2 éditions/jour). Aligné avec la
    règle R42-BT pour les autres sources."""
    monkeypatch.delenv("RUN_MODE", raising=False)
    from src.run_mode import window_days
    yaml_full = 16  # ancienne valeur YAML
    assert window_days(nominal=30, full=max(yaml_full, 60)) == 30


def test_dila_full_rouvre_60(monkeypatch):
    monkeypatch.setenv("RUN_MODE", "full")
    from src.run_mode import window_days
    yaml_full = 16
    # full → max(16, 60) = 60 éditions ≈ 30 jours
    assert window_days(nominal=30, full=max(yaml_full, 60)) == 60
