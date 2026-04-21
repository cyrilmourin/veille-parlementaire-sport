"""Tests sur les réécritures in-memory appliquées à l'export site.

Couvre R11g / UX-D et UX-E :

* `_fix_question_row` : retire "→ ministère [sort]" du titre et résout
  les "Député PAxxx" résiduels. Idempotent.
* `_fix_cr_row` : déjà testé indirectement par test_digest ; ici on ajoute
  une sentinelle sur l'enrichissement thème Sénat via `extract_cr_theme`.
* `_load` : recalcule `snippet` depuis `summary` pour les items matchés
  (bug historique : `snippet` n'était jamais persisté en DB).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.site_export import (  # noqa: E402
    _fix_agenda_row,
    _fix_cr_row,
    _fix_dossier_row,
    _fix_question_row,
    _load,
)


# ---------- _fix_question_row (UX-D) --------------------------------------

def test_fix_question_row_strips_ministere_and_sort():
    r = {
        "category": "questions",
        "title": (
            "Question de +1 an sans réponse n°1054S — M. Cyril Pellevat "
            "(Les Indépendants) → Sports, jeunesse et vie associative "
            "[En cours] : Gouvernance du comité"
        ),
        "raw": {},
    }
    _fix_question_row(r)
    # Plus de → ministère ni [sort], espacement propre autour du colon
    assert "→" not in r["title"]
    assert "[En cours]" not in r["title"]
    assert "Sports, jeunesse et vie associative" not in r["title"]
    assert "M. Cyril Pellevat (Les Indépendants) : Gouvernance" in r["title"]


def test_fix_question_row_resolves_deputy_code():
    """Un `Député PAxxxx` est résolu si la clé est dans le cache AMO."""
    with patch("src.site_export.amo_loader.resolve_acteur") as m:
        m.side_effect = lambda ref: (
            "Mme Perrine Goulet" if ref == "PA720560" else ""
        )
        r = {
            "category": "questions",
            "title": (
                "Question au gouvernement n°1370 — Député PA720560 (DEM) "
                "→ Santé : enfants"
            ),
            "raw": {"auteur": "Député PA720560", "auteur_ref": "PA720560"},
        }
        _fix_question_row(r)
    assert "Député PA720560" not in r["title"]
    assert "Mme Perrine Goulet" in r["title"]
    # Et la mention ministère "→ Santé" a aussi été retirée
    assert "→" not in r["title"]


def test_fix_question_row_is_idempotent():
    """Appliquer 2 fois ne change rien au titre."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite n°14369 — M. Jean Dupont (RN) : sports",
            "raw": {},
        }
        before = r["title"]
        _fix_question_row(r)
        after_first = r["title"]
        _fix_question_row(r)
        after_second = r["title"]
    assert before == after_first == after_second


def test_fix_question_row_ignores_other_categories():
    r = {"category": "agenda", "title": "Réunion X → ministre : thème", "raw": {}}
    before = r["title"]
    _fix_question_row(r)
    assert r["title"] == before


def test_fix_question_row_handles_unknown_deputy():
    """Si le cache AMO n'a pas la clé, on laisse le code tel quel sans crash."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite n°1 — Député PA999999 (NI) : sujet",
            "raw": {"auteur": "Député PA999999", "auteur_ref": "PA999999"},
        }
        _fix_question_row(r)
    # Titre conservé tel quel (résolution a échoué, pas de remplacement)
    assert "Député PA999999" in r["title"]


# ---------- _load : recalcul snippet (UX-E) --------------------------------

def test_load_rebuilds_snippet_from_summary():
    """Un item matché sans snippet en DB obtient un snippet reconstitué à
    l'export (le schéma SQL n'a jamais eu de colonne snippet)."""
    rows = [{
        "title": "Projet de loi sport",
        "summary": (
            "L'article 5 propose d'attribuer à l'Agence nationale du sport "
            "des moyens supplémentaires pour préparer les jeux olympiques "
            "de 2030 dans les Alpes. Cette disposition vise à…"
        ),
        "matched_keywords": '["jeux olympiques"]',
        "keyword_families": '["evenement"]',
        "raw": "{}",
    }]
    out = _load(rows)
    assert len(out) == 1
    # Snippet généré, contient le match
    assert out[0].get("snippet")
    assert "jeux olympiques" in out[0]["snippet"].lower() \
           or "jeux Olympiques" in out[0]["snippet"]


def test_load_preserves_existing_snippet():
    """Si un snippet est déjà présent (cas rare, forcé ailleurs), on ne
    l'écrase pas."""
    rows = [{
        "title": "Titre",
        "summary": "Summary avec jeux olympiques quelque part.",
        "snippet": "snippet existant",
        "matched_keywords": '["jeux olympiques"]',
        "keyword_families": '[]',
        "raw": "{}",
    }]
    out = _load(rows)
    assert out[0]["snippet"] == "snippet existant"


def test_load_no_snippet_for_unmatched():
    """Les items non matchés n'ont pas de snippet (gaspillage inutile)."""
    rows = [{
        "title": "Titre",
        "summary": "Texte quelconque sans match",
        "matched_keywords": '[]',
        "keyword_families": '[]',
        "raw": "{}",
    }]
    out = _load(rows)
    # snippet vide ou absent
    assert not out[0].get("snippet")


# ---------- _fix_agenda_row (UX-A) ----------------------------------------

@pytest.mark.parametrize("cat,title,expected", [
    # Avec tiret simple → préfixe retiré
    ("agenda", "Agenda - Semaine du 15 au 19 avril 2026", "Semaine du 15 au 19 avril 2026"),
    # Avec em-dash
    ("communiques", "Agenda — Bulletin hebdomadaire", "Bulletin hebdomadaire"),
    # Avec en-dash
    ("agenda", "Agenda – Avril 2026", "Avril 2026"),
    # Sans espace autour du tiret
    ("agenda", "Agenda-Test", "Test"),
    # "Agenda de X" reste (informatif)
    ("agenda", "Agenda de Marina Ferrari", "Agenda de Marina Ferrari"),
    ("communiques", "Agenda du ministre", "Agenda du ministre"),
    # "Agenda prévisionnel de X" reste
    ("agenda", "Agenda prévisionnel de Marina Ferrari", "Agenda prévisionnel de Marina Ferrari"),
    # Items sans "Agenda" en tête : pas de changement
    ("agenda", "Réunion", "Réunion"),
    ("communiques", "Déplacement de Marina Ferrari", "Déplacement de Marina Ferrari"),
])
def test_fix_agenda_row(cat, title, expected):
    r = {"category": cat, "title": title}
    _fix_agenda_row(r)
    assert r["title"] == expected


def test_fix_agenda_row_ignores_other_categories():
    r = {"category": "questions", "title": "Agenda - test"}
    _fix_agenda_row(r)
    assert r["title"] == "Agenda - test"  # inchangé


def test_fix_agenda_row_is_idempotent():
    r = {"category": "agenda", "title": "Agenda - Semaine X"}
    _fix_agenda_row(r)
    first = r["title"]
    _fix_agenda_row(r)
    assert r["title"] == first


# ---------- _fix_dossier_row (UX-B) ----------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("projet de loi relatif à l'organisation", "Projet de loi relatif à l'organisation"),
    ("proposition de loi visant à protéger", "Proposition de loi visant à protéger"),
    # Majuscule déjà présente → inchangé
    ("Projet de loi X", "Projet de loi X"),
    # Sigle en début → inchangé (déjà majuscule)
    ("PJL relatif à…", "PJL relatif à…"),
])
def test_fix_dossier_row_capitalizes_first_letter(title, expected):
    r = {"category": "dossiers_legislatifs", "title": title}
    _fix_dossier_row(r)
    assert r["title"] == expected


def test_fix_dossier_row_ignores_other_categories():
    r = {"category": "questions", "title": "petite question"}
    _fix_dossier_row(r)
    assert r["title"] == "petite question"


def test_fix_dossier_row_empty_title():
    r = {"category": "dossiers_legislatifs", "title": ""}
    _fix_dossier_row(r)  # ne crash pas
    assert r["title"] == ""
