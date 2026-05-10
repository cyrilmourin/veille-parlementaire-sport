"""Tests R42-M (2026-05-10) — Date de dépôt vraie pour les dossiers
législatifs (au lieu de la date boostée par R41-K affichée à tort
comme « Dépôt au X le » sur les cards).

Vérifie qu'on émet `date_depot:` au frontmatter UNIQUEMENT quand le
boost R41-K a effectivement modifié la date (cad. quand
`raw.published_at_original` est posé) et différent de la nouvelle date.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def test_r42m_emet_date_depot_si_boost_a_eu_lieu():
    """Le code site_export.py doit ajouter `date_depot:` au frontmatter
    quand le row dosleg a `raw.published_at_original` ≠ published_at."""
    from src import site_export as sx
    src_path = sx.__file__
    with open(src_path, encoding="utf-8") as f:
        src_code = f.read()
    # Marqueur du fragment R42-M
    assert "R42-M (2026-05-10)" in src_code
    # Émet `date_depot:` au frontmatter
    assert 'fm.append(f"date_depot: {date_depot}")' in src_code, (
        "site_export doit émettre `date_depot:` au frontmatter pour les "
        "dosleg quand un boost R41-K a posé `published_at_original` (R42-M)"
    )


def test_r42m_template_utilise_date_depot():
    """Le template list.html doit lire `.Params.date_depot` en priorité
    avant de retomber sur `.Date`."""
    template = (_ROOT / "site/layouts/dossiers_legislatifs/list.html")
    code = template.read_text(encoding="utf-8")
    assert "R42-M" in code
    # Le template lit .Params.date_depot et l'affecte à $depotDate
    assert ".Params.date_depot" in code, (
        "Le template list.html doit utiliser .Params.date_depot pour "
        "afficher la VRAIE date de dépôt (R42-M)"
    )
    # On bascule sur $depotDate pour le rendu, pas .Date directement
    assert "$depotDate" in code
