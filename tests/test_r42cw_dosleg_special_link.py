"""R42-CW (2026-05-15) — `special_page_url` posé en frontmatter pour
les items dosleg liés à une PPL spéciale.

Cyril 2026-05-15 : « Dans dosleg `Encourager les partenariats…` ne fait
pas de lien vers la page spéciale. Et il y a un lien `consulter le
dossier législatif` plutôt que `Consulter la page dédiée à la PPL` ».

Avant : seule la PPL Sport pro était redirigée (template list.html
hardcodait `DLR5L17N51732` en `in .Params.source_url`). La PPL
Équipements (DLR5L17N54138) restait sur le dossier AN.
Après : `_write_item_pages` détecte la PPL via `row_matches_*` et
expose `special_page_url` + `special_page_label`. Le template
list.html (et single.html) consomme ces champs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.special_ppl import row_matches_special_ppl, AN_TEXTE_REF
from src.special_equipements import (
    AN_DOSSIER_ID,
    AN_TEXTE_REF as EQUIP_TEXTE_REF,
    row_matches_special_equipements,
)


def test_match_ppl_sport_pro_via_dossier_id():
    """L'item dosleg PPL Sport pro est matché par row_matches_special_ppl
    (qui sert de base au choix du special_page_url)."""
    r = {
        "title": "Proposition de loi relative à l'organisation, à la "
                 "gestion et au financement du sport professionnel",
        "url": "https://www.assemblee-nationale.fr/dyn/17/textes/l17b1560_proposition-loi",
        "category": "dossiers_legislatifs",
        "chamber": "AN",
        "raw": {"texte_ref": AN_TEXTE_REF},
    }
    assert row_matches_special_ppl(r) is True


def test_match_ppl_equipements_via_dossier_id():
    """L'item dosleg PPL Équipements est matché par
    row_matches_special_equipements."""
    r = {
        "title": "Proposition de loi visant à encourager les partenariats "
                 "entre les collectivités territoriales et les personnes "
                 "morales de droit privé en matière d'acquisition, de "
                 "réalisation ou de rénovation d'équipements sportifs",
        "url": f"https://www.assemblee-nationale.fr/dyn/17/dossiers/{AN_DOSSIER_ID}",
        "category": "dossiers_legislatifs",
        "chamber": "AN",
        "raw": {"dossier_id": AN_DOSSIER_ID},
    }
    assert row_matches_special_equipements(r) is True


def test_write_item_pages_pose_special_page_url(tmp_path):
    """Vérifie que le frontmatter écrit par `_write_item_pages` contient
    `special_page_url` quand l'item matche une PPL spéciale."""
    from datetime import datetime
    from src.site_export import _write_item_pages

    rows = [
        {
            "title": "Proposition de loi relative à l'organisation, à la "
                     "gestion et au financement du sport professionnel",
            "url": "https://www.assemblee-nationale.fr/dyn/17/textes/l17b1560_proposition-loi",
            "category": "dossiers_legislatifs",
            "chamber": "AN",
            "source_id": "an_dossiers_legislatifs",
            "uid": "DLR5L17N51732",
            "published_at": "2026-05-12T09:00:00",
            "matched_keywords": ["sport"],
            "keyword_families": ["sport"],
            "raw": {"texte_ref": AN_TEXTE_REF},
            "summary": "...",
        },
        {
            "title": ("Proposition de loi visant à encourager les partenariats "
                      "entre les collectivités territoriales et les personnes "
                      "morales de droit privé en matière d'acquisition, de "
                      "réalisation ou de rénovation d'équipements sportifs"),
            "url": f"https://www.assemblee-nationale.fr/dyn/17/dossiers/{AN_DOSSIER_ID}",
            "category": "dossiers_legislatifs",
            "chamber": "AN",
            "source_id": "an_dossiers_legislatifs",
            "uid": AN_DOSSIER_ID,
            "published_at": "2026-05-10T09:00:00",
            "matched_keywords": ["sport"],
            "keyword_families": ["sport"],
            "raw": {"dossier_id": AN_DOSSIER_ID},
            "summary": "...",
        },
    ]
    items_dir = tmp_path / "items"
    _write_item_pages(items_dir, rows)
    # Lecture des md générés
    sport_dir = items_dir / "dossiers_legislatifs"
    files = list(sport_dir.glob("*.md"))
    assert len(files) == 2
    contents = [f.read_text(encoding="utf-8") for f in files]
    blob = "\n".join(contents)
    # PPL Sport pro
    assert 'special_page_url: "/ppl-sport-professionnel/"' in blob
    # PPL Équipements
    assert 'special_page_url: "/ppl-partenariats-equipements-sportifs/"' in blob
    # Libellé bouton
    assert 'special_page_label: "Consulter la page dédiée à la PPL"' in blob


def test_write_item_pages_pas_de_special_page_url_si_pas_special(tmp_path):
    """Un dosleg quelconque n'a PAS `special_page_url` (pas de régression)."""
    from src.site_export import _write_item_pages

    rows = [{
        "title": "PPL relative au numérique éducatif",
        "url": "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17NXX",
        "category": "dossiers_legislatifs",
        "chamber": "AN",
        "source_id": "an_dossiers_legislatifs",
        "uid": "DLR5L17NXX",
        "published_at": "2026-05-12T09:00:00",
        "matched_keywords": ["numérique"],
        "keyword_families": ["numérique"],
        "raw": {},
        "summary": "...",
    }]
    items_dir = tmp_path / "items"
    _write_item_pages(items_dir, rows)
    md_files = list((items_dir / "dossiers_legislatifs").glob("*.md"))
    assert len(md_files) == 1
    text = md_files[0].read_text(encoding="utf-8")
    assert "special_page_url" not in text
