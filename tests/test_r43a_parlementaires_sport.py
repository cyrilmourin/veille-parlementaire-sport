"""R43-A (2026-05-17) — Script top parlementaires actifs sport.

On teste les briques du script unitairement (sans fetch réseau) :
- CompteurActeur.score() calcule bien le score composite
- CompteurActeur.taux_adoption_amdt() gère les divisions par zéro
- _text_of() extrait les strings depuis les structures XSD AN (dict {#text})
- _strip_html() retire les balises basiques
- _document_auteurs() distingue premier signataire / cosignataires /
  rapporteurs et ignore les cosignataires retirés
- _build_an_photo_url() construit l'URL portrait AN canonique
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.parlementaires_sport import (
    CompteurActeur,
    SCORE,
    _build_an_photo_url,
    _document_auteurs,
    _strip_html,
    _text_of,
)


# ---------------------------------------------------------------------------
# CompteurActeur — calcul du score
# ---------------------------------------------------------------------------

def test_score_vide():
    c = CompteurActeur()
    assert c.score() == 0


def test_score_questions():
    """R43-B : QE/QOSD = 2 pts, QAG = 5 pts."""
    c = CompteurActeur(qe=10, qag=2, qosd=3)
    expected = 10 * 2 + 2 * 5 + 3 * 2  # 20 + 10 + 6 = 36
    assert c.score() == expected


def test_score_amendements_auteur_principal_seul():
    """R43-B : amdt_depose = 0.5, amdt_adopte = 2. Cosignataires = 0 pt."""
    c = CompteurActeur(amdt_depose=10, amdt_adopte=3, amdt_cosigne=50)
    expected = 10 * 0.5 + 3 * 2  # 5 + 6 = 11
    assert c.score() == expected


def test_score_textes_legislatifs():
    """R43-B nouvelle pondération."""
    c = CompteurActeur(
        ppl_premier_signataire=1,           # 15
        ppl_signataire=2,                   # 10 (2x5)
        texte_adopte_premier_signataire=1,  # 10 bonus
        resolution_signataire=3,            # 9 (3x3)
    )
    expected = 15 + 10 + 10 + 9
    assert c.score() == expected


def test_score_rapporteur():
    """R43-B : rapporteur principal = 10, rapporteur avis/co = 5, rapport
    parlementaire = 10."""
    c = CompteurActeur(
        rapporteur_principal=1,             # 10
        rapporteur_avis_co=2,               # 10 (2x5)
        rapport_parlementaire_auteur=1,     # 10
    )
    expected = 10 + 10 + 10
    assert c.score() == expected


def test_score_appartenance():
    """R43-B : nouveaux critères d'appartenance."""
    c = CompteurActeur(
        membre_commission_culture=1,         # 5
        membre_groupe_etude_sport=1,         # 5
    )
    assert c.score() == 10


def test_score_composite_realiste_savin():
    """Simule Michel Savin Sénat — pondération R43-B."""
    c = CompteurActeur(
        membre_commission_culture=1,         # 5
        membre_groupe_etude_sport=1,         # 5
        rapporteur_principal=1,              # 10 (PPL Sport pro)
        rapport_parlementaire_auteur=1,      # 10 (Football-business)
    )
    expected = 5 + 5 + 10 + 10
    assert c.score() == expected


def test_taux_adoption_amdt():
    c = CompteurActeur(amdt_depose=10, amdt_adopte=3)
    assert c.taux_adoption_amdt() == 30.0

    c2 = CompteurActeur(amdt_depose=0, amdt_adopte=0)
    assert c2.taux_adoption_amdt() is None

    c3 = CompteurActeur(amdt_depose=7, amdt_adopte=7)
    assert c3.taux_adoption_amdt() == 100.0


# ---------------------------------------------------------------------------
# _text_of() — extraction robuste depuis structures XSD AN
# ---------------------------------------------------------------------------

def test_text_of_string():
    assert _text_of("hello") == "hello"


def test_text_of_dict_text():
    """Forme XSD AN : `{"@xsi:type": "...", "#text": "PA841605"}`."""
    assert _text_of({"@xsi:type": "IdActeur_type", "#text": "PA841605"}) == "PA841605"


def test_text_of_none():
    assert _text_of(None) == ""


def test_text_of_int():
    assert _text_of(42) == "42"


def test_text_of_dict_vide():
    assert _text_of({}) == ""


# ---------------------------------------------------------------------------
# _strip_html()
# ---------------------------------------------------------------------------

def test_strip_html_balises():
    """_strip_html remplace les balises par des espaces (les espaces
    multiples sont OK car le matcher normalise toujours)."""
    out = _strip_html("<p>Hello <strong>world</strong></p>")
    assert "Hello" in out
    assert "world" in out
    assert "<" not in out and ">" not in out


def test_strip_html_vide():
    assert _strip_html("") == ""
    assert _strip_html(None) == ""


# ---------------------------------------------------------------------------
# _document_auteurs() — premier sign. / cosig / rapporteurs
# ---------------------------------------------------------------------------

def test_document_auteurs_ppl_avec_cosigs():
    """Cas d'usage : PPL avec 1 auteur + N cosignataires."""
    doc = {
        "auteurs": {
            "auteur": {
                "acteur": {"acteurRef": "PA001", "qualite": "auteur"},
            },
        },
        "coSignataires": {
            "coSignataire": [
                {
                    "acteur": {"acteurRef": "PA002"},
                    "dateCosignature": "2024-11-07",
                    "dateRetraitCosignature": None,
                },
                {
                    "acteur": {"acteurRef": "PA003"},
                    "dateCosignature": "2024-11-07",
                    "dateRetraitCosignature": None,
                },
            ]
        },
    }
    premiers, cosig, rapp = _document_auteurs(doc)
    assert premiers == ["PA001"]
    assert cosig == ["PA002", "PA003"]
    assert rapp == []


def test_document_auteurs_rapport_avec_rapporteur():
    """Rapport de commission : qualité = 'rapporteur'."""
    doc = {
        "auteurs": {
            "auteur": [
                {"acteur": {"acteurRef": "PA100", "qualite": "rapporteur"}},
                {"acteur": {"acteurRef": "PA101", "qualite": "rapporteur"}},
            ]
        }
    }
    premiers, cosig, rapp = _document_auteurs(doc)
    assert premiers == []
    assert cosig == []
    assert rapp == ["PA100", "PA101"]


def test_document_auteurs_cosignataire_retire_ignore():
    """Cosignataire avec dateRetraitCosignature posé ne doit pas être
    compté."""
    doc = {
        "auteurs": {
            "auteur": {"acteur": {"acteurRef": "PA200", "qualite": "auteur"}}
        },
        "coSignataires": {
            "coSignataire": [
                {
                    "acteur": {"acteurRef": "PA201"},
                    "dateCosignature": "2024-11-07",
                    "dateRetraitCosignature": "2025-01-15+01:00",  # retiré
                },
                {
                    "acteur": {"acteurRef": "PA202"},
                    "dateCosignature": "2024-11-07",
                    "dateRetraitCosignature": None,
                },
            ]
        },
    }
    premiers, cosig, rapp = _document_auteurs(doc)
    assert premiers == ["PA200"]
    assert cosig == ["PA202"], "PA201 retiré doit être ignoré"


def test_document_auteurs_vide():
    """Document sans auteurs ni cosignataires."""
    premiers, cosig, rapp = _document_auteurs({})
    assert premiers == []
    assert cosig == []
    assert rapp == []


# ---------------------------------------------------------------------------
# _build_an_photo_url() — URL portrait AN
# ---------------------------------------------------------------------------

def test_photo_url_canonique():
    url = _build_an_photo_url("PA841605")
    assert url == "https://www.assemblee-nationale.fr/dyn/static/tribun/17/photos/carre/841605.jpg"


def test_photo_url_ref_invalide():
    assert _build_an_photo_url("") == ""
    assert _build_an_photo_url("XXX123") == ""
    assert _build_an_photo_url(None) == ""
