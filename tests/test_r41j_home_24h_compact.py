"""R41-J (2026-05-07) — Tests des nouveaux paramètres `_fmt_item_line` pour
l'affichage compact « Actualité des dernières 24 h » sur l'accueil.

Demandes Cyril :
1. Badge AN/Sénat AVANT le titre (chamber_first=True)
2. Pas de date affichée (with_date=False)
3. Liens en nouvel onglet (target_blank=True)
4. Limite 5 visibles + repli (testé sur le rendu home, voir test e2e)
"""
from __future__ import annotations

from src.site_export import _fmt_item_line


def _row(**kw) -> dict:
    base = {
        "title": "Test PPL sport",
        "url": "https://example.test/ppl",
        "chamber": "AN",
        "published_at": "2026-05-07T10:00:00",
        "matched_keywords": ["sport"],
        "keyword_families": ["acteur"],
        "raw": {},
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# chamber_first
# ---------------------------------------------------------------------------


def test_chamber_first_place_badge_avant_titre():
    line = _fmt_item_line(_row(), chamber_first=True, with_date=False,
                          with_tags=False)
    # Le badge chambre doit apparaître avant le titre
    badge_idx = line.find('data-chamber="AN"')
    title_idx = line.find("Test PPL sport")
    assert badge_idx > 0 and title_idx > 0
    assert badge_idx < title_idx, (
        "chamber_first=True : le badge AN/Sénat doit être avant le titre"
    )


def test_chamber_first_pas_doublon_dans_meta():
    """Avec chamber_first=True, le badge ne doit pas réapparaître dans .meta-main."""
    line = _fmt_item_line(_row(), chamber_first=True, with_date=False,
                          with_tags=False)
    # Une seule occurrence de data-chamber
    assert line.count('data-chamber="AN"') == 1


def test_chamber_first_false_garde_comportement_legacy():
    """chamber_first=False (défaut) : badge dans .meta-main comme avant."""
    line = _fmt_item_line(_row(), with_tags=False)
    # Le badge doit être dans la zone meta (après le titre)
    title_idx = line.find("Test PPL sport")
    badge_idx = line.find('data-chamber="AN"')
    assert badge_idx > title_idx


# ---------------------------------------------------------------------------
# with_date
# ---------------------------------------------------------------------------


def test_with_date_false_omet_date():
    line = _fmt_item_line(_row(), with_date=False, with_tags=False)
    assert "<time" not in line
    assert "2026-05-07" not in line


def test_with_date_true_default_inclut_date():
    line = _fmt_item_line(_row(), with_tags=False)
    assert "2026-05-07" in line


# ---------------------------------------------------------------------------
# target_blank
# ---------------------------------------------------------------------------


def test_target_blank_genere_anchor_html_avec_target_et_rel():
    line = _fmt_item_line(_row(), target_blank=True, with_tags=False)
    assert 'target="_blank"' in line
    assert 'rel="noopener"' in line
    # Et pas le format Markdown [..](..) pour le titre
    assert "[Test PPL sport]" not in line


def test_target_blank_false_garde_markdown_link():
    line = _fmt_item_line(_row(), with_tags=False)
    # Format Markdown standard
    assert "[Test PPL sport](https://example.test/ppl)" in line
    assert 'target="_blank"' not in line


def test_target_blank_sans_url_garde_titre_simple():
    """Pas d'URL → titre en gras simple, ignore target_blank."""
    line = _fmt_item_line(_row(url=""), target_blank=True, with_tags=False)
    assert "**Test PPL sport**" in line
    assert 'target="_blank"' not in line


# ---------------------------------------------------------------------------
# Combinaison : tous les paramètres home 24h actifs
# ---------------------------------------------------------------------------


def test_combo_home_24h():
    """Cas réel home : chamber_first + no date + target_blank."""
    line = _fmt_item_line(
        _row(),
        chamber_first=True, with_date=False,
        target_blank=True, with_tags=False,
    )
    # Badge AN devant le titre
    assert line.index('data-chamber="AN"') < line.index("Test PPL sport")
    # Pas de date
    assert "2026-05-07" not in line
    # Lien nouvel onglet
    assert 'target="_blank"' in line
    assert 'rel="noopener"' in line
    # Pas de tags
    assert 'kw-tag' not in line


def test_combo_senat_chamber_correctement_rendue():
    line = _fmt_item_line(
        _row(chamber="Senat"),
        chamber_first=True, with_date=False,
        target_blank=True, with_tags=False,
    )
    assert 'data-chamber="Senat"' in line
    assert line.index('data-chamber="Senat"') < line.index("Test PPL sport")
