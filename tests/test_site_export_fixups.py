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
    _fix_amendement_row,
    _fix_chamber_row,
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


# ---------- _fix_amendement_row (R13-A backfill) ---------------------------

def test_fix_amendement_row_resolves_deputy_code_from_raw_ref():
    """Priorité à `raw.auteur_ref` pour résoudre PAxxxx, pas au code du title."""
    with patch("src.site_export.amo_loader.resolve_acteur") as m:
        m.side_effect = lambda ref: (
            "Mme Clémence Guetté" if ref == "PA794130" else ""
        )
        r = {
            "category": "amendements",
            "title": (
                "Amendement n°57 [Discuté] — Député PA794130 · art. ARTICLE 5 "
                "· sur « Renforcer la sécurité »"
            ),
            "raw": {"auteur_ref": "PA794130", "numero": "57"},
        }
        _fix_amendement_row(r)
    assert "Député PA794130" not in r["title"]
    assert "Mme Clémence Guetté" in r["title"]
    # R13-G : "Amendement n°" → "Amdt n°" en sortie du fixup.
    assert "Amdt n°57" in r["title"]
    assert "Amendement" not in r["title"]
    assert "art. ARTICLE 5" in r["title"]


def test_fix_amendement_row_falls_back_to_captured_code_if_no_raw_ref():
    """Sans raw.auteur_ref, on retombe sur le code PAxxxx capturé dans le title."""
    with patch("src.site_export.amo_loader.resolve_acteur") as m:
        m.side_effect = lambda ref: (
            "M. Hadrien Clouet" if ref == "PA795746" else ""
        )
        r = {
            "category": "amendements",
            "title": "Amendement n°52 [Discuté] — Député PA795746 · art. 5",
            "raw": {"numero": "52"},  # pas d'auteur_ref
        }
        _fix_amendement_row(r)
    assert "Député PA795746" not in r["title"]
    assert "M. Hadrien Clouet" in r["title"]


def test_fix_amendement_row_noop_when_already_resolved():
    """Si le titre ne contient pas "Député PAxxxx", AMO n'est pas appelé.
    R13-G : le fixup applique quand même "Amendement n°" → "Amdt n°".
    """
    with patch("src.site_export.amo_loader.resolve_acteur") as m:
        r = {
            "category": "amendements",
            "title": "Amendement n°10 [Discuté] — Mme Sandrine Rousseau · art. 3",
            "raw": {"auteur_ref": "PA720892"},
        }
        _fix_amendement_row(r)
    m.assert_not_called()
    # R13-G : "Amendement n°" est renommé en "Amdt n°" même sans résolution AMO.
    assert r["title"] == "Amdt n°10 [Discuté] — Mme Sandrine Rousseau · art. 3"


def test_fix_amendement_row_keeps_title_if_cache_miss():
    """Si le cache AMO ne connaît pas la clé, on garde le code brut (pas un None).
    R13-G : "Amendement n°" devient "Amdt n°" en sortie.
    """
    with patch("src.site_export.amo_loader.resolve_acteur") as m:
        m.return_value = ""  # miss
        r = {
            "category": "amendements",
            "title": "Amendement n°99 — Député PA999999 · art. 1",
            "raw": {"auteur_ref": "PA999999"},
        }
        _fix_amendement_row(r)
    assert "Député PA999999" in r["title"]  # code AMO inchangé (pas résolu)
    assert "Amdt n°99" in r["title"]  # préfixe renommé
    assert "Amendement" not in r["title"]


def test_fix_amendement_row_renames_amendement_to_amdt_idempotent():
    """R13-G : "Amendement n°" → "Amdt n°" même sans "Député PAxxxx".
    Appliquer 2 fois ne double pas le renommage.
    """
    with patch("src.site_export.amo_loader.resolve_acteur") as m:
        r = {
            "category": "amendements",
            "title": "Amendement n°1 — M. Dupont · art. 2",
            "raw": {"auteur_ref": "PA111"},
        }
        _fix_amendement_row(r)
        after_first = r["title"]
        _fix_amendement_row(r)
        after_second = r["title"]
    m.assert_not_called()
    assert after_first == "Amdt n°1 — M. Dupont · art. 2"
    assert after_second == after_first  # idempotent


def test_fix_amendement_row_amdt_alone_noop():
    """Un titre déjà en "Amdt n°" n'est pas doublement renommé."""
    with patch("src.site_export.amo_loader.resolve_acteur"):
        r = {
            "category": "amendements",
            "title": "Amdt n°1 — M. Dupont · art. 2",
            "raw": {"auteur_ref": "PA111"},
        }
        _fix_amendement_row(r)
    assert r["title"] == "Amdt n°1 — M. Dupont · art. 2"


def test_fix_amendement_row_ignores_other_categories():
    r = {
        "category": "questions",
        "title": "Question — Député PA123",
        "raw": {"auteur_ref": "PA123"},
    }
    _fix_amendement_row(r)
    assert r["title"] == "Question — Député PA123"  # inchangé


# ---------- _load : recapitalize matched_keywords (R13-B backfill) ----------

def test_load_recapitalizes_legacy_matched_keywords():
    """Les items pré-R13-B ont des kws en minuscules unidecodées → remappés."""
    import json
    row = {
        "category": "questions",
        "title": "Question sport",
        "summary": "",
        "matched_keywords": json.dumps(
            ["jeux olympiques", "activite physique adaptee"]
        ),
        "keyword_families": json.dumps(["evenement", "dispositif"]),
        "raw": "{}",
    }
    out = list(_load([row]))
    assert len(out) == 1
    kws = out[0]["matched_keywords"]
    # Les deux kws ont récupéré leur forme canonique du yaml.
    assert "Jeux olympiques" in kws
    assert "Activité physique adaptée" in kws
    # Aucun résidu en minuscule.
    assert "jeux olympiques" not in kws
    assert "activite physique adaptee" not in kws


# ---------- _fix_chamber_row (R13-G : Www → MinXXX) ------------------------

@pytest.mark.parametrize("source_id,expected", [
    ("min_armees", "MinARMEES"),
    ("min_justice", "MinJUSTICE"),
    ("min_interieur", "MinINTERIEUR"),
    ("min_culture", "MinCULTURE"),
    ("min_affaires_etrangeres", "MinAFFAIRES"),
    ("min_transition_ecologique", "MinECOLOGIE"),
])
def test_fix_chamber_row_maps_www_by_source_id(source_id, expected):
    r = {"chamber": "Www", "source_id": source_id}
    _fix_chamber_row(r)
    assert r["chamber"] == expected


def test_fix_chamber_row_ignores_non_www():
    """Un chamber déjà bon (AN, Senat, MinSports) n'est pas modifié."""
    for ch in ["AN", "Senat", "MinSports", "Elysee", "JORF"]:
        r = {"chamber": ch, "source_id": "any"}
        _fix_chamber_row(r)
        assert r["chamber"] == ch


def test_fix_chamber_row_leaves_unknown_source_id_alone():
    """Un source_id inconnu reste 'Www' — on ne suppose rien."""
    r = {"chamber": "Www", "source_id": "inconnu"}
    _fix_chamber_row(r)
    assert r["chamber"] == "Www"


# ---------- _fix_question_row (R13-G : priorité analyse > rubrique) --------

def test_fix_question_row_swaps_rubrique_for_analyse():
    """Si raw.analyse est présent, il remplace le suffixe rubrique du titre."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite · 12/04/2026 — Mme X (LFI) : sports",
            "raw": {
                "auteur": "Mme X", "auteur_ref": "PA1",
                "rubrique": "sports",
                "analyse": "Financement des équipements sportifs scolaires",
            },
        }
        _fix_question_row(r)
    assert "Financement des équipements sportifs scolaires" in r["title"]
    # R13-J : date retirée du titre — préfixe = type + auteur + groupe + ":".
    assert r["title"].startswith("Question écrite — Mme X (LFI) : ")
    assert "12/04/2026" not in r["title"]


def test_fix_question_row_uses_tete_analyse_fallback():
    """Si analyse absent, on retombe sur tete_analyse."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite · 01/01/2026 — M. Y : rubrique courte",
            "raw": {
                "auteur": "M. Y", "auteur_ref": "PA2",
                "rubrique": "rubrique courte",
                "analyse": "",
                "tete_analyse": "Tête d'analyse plus descriptive",
            },
        }
        _fix_question_row(r)
    assert "Tête d'analyse plus descriptive" in r["title"]


def test_fix_question_row_removes_duplicate_date_r13j():
    """R13-J patch 3 : la date dupliquée "· DD/MM/YYYY" est retirée du titre
    (la date reste affichée par le template dans la meta-line)."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite · 12/04/2026 — Mme Y (LFI) : sport scolaire",
            "raw": {"auteur": "Mme Y", "auteur_ref": "PA1"},
        }
        _fix_question_row(r)
    assert "12/04/2026" not in r["title"]
    assert "Question écrite" in r["title"]
    assert "Mme Y" in r["title"]


def test_fix_question_row_noop_if_no_analyse():
    """Sans analyse ni tete_analyse, on ne touche pas au suffixe."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite · 01/01/2026 — M. Z : sport scolaire",
            "raw": {"auteur": "M. Z", "auteur_ref": "PA3", "rubrique": "sports"},
        }
        _fix_question_row(r)
    # Titre inchangé (pas d'analyse à substituer).
    assert r["title"].endswith(": sport scolaire")


# ---------- _fix_cr_row (R13-G : "Séance AN du" → "Séance du") ------------

def test_fix_cr_row_renames_seance_an_du_to_seance_du():
    """Pour l'AN, le préfixe 'Séance AN du ...' devient 'Séance du ...'.
    La chambre est déjà affichée via le badge .chamber[data-chamber=AN].
    """
    r = {
        "category": "comptes_rendus",
        "chamber": "AN",
        "title": "Séance AN du 20/04/2026 — Examen du PLF 2027",
        "raw": {"seance_date_iso": "2026-04-20"},
        "url": "https://www.assemblee-nationale.fr/dyn/17/comptes-rendus/seance/CRSANXY",
    }
    _fix_cr_row(r)
    assert r["title"].startswith("Séance du 20/04/2026")
    assert "Séance AN du" not in r["title"]
    assert "Examen du PLF 2027" in r["title"]


def test_fix_cr_row_keeps_seance_senat_du():
    """Pour le Sénat, on garde 'Séance Sénat du ...' (pas de renommage)."""
    r = {
        "category": "comptes_rendus",
        "chamber": "Senat",
        "title": "Séance Sénat du 18/04/2026 — Questions au Gouvernement",
        "raw": {"seance_date_iso": "2026-04-18"},
        "url": "https://www.senat.fr/seances/s202604/s20260418/",
    }
    _fix_cr_row(r)
    assert r["title"].startswith("Séance Sénat du 18/04/2026")


# ---------- _fix_agenda_row R13-H : "Réunion (POxxx)" ---------------------

def test_fix_agenda_row_resolves_organe_when_cache_knows_po():
    """Si le cache AMO connaît le PO, on affiche le libellé résolu."""
    with patch("src.site_export.amo_loader.resolve_organe") as m:
        m.side_effect = lambda po: (
            "Commission des affaires économiques" if po == "PO59048" else ""
        )
        r = {
            "category": "agenda",
            "title": "Réunion (PO59048)",
            "published_at": "2026-04-21T10:00:00",
        }
        _fix_agenda_row(r)
    assert r["title"] == "Réunion — Commission des affaires économiques"


def test_fix_agenda_row_falls_back_to_date_when_po_unknown():
    """Si le PO est inconnu du cache, on utilise la date de séance."""
    with patch("src.site_export.amo_loader.resolve_organe", return_value=""):
        r = {
            "category": "agenda",
            "title": "Réunion (PO878768)",
            "published_at": "2026-04-25T09:30:00",
        }
        _fix_agenda_row(r)
    assert r["title"] == "Réunion AN du 25/04/2026"
    assert "PO878768" not in r["title"]


def test_fix_agenda_row_de_commission_falls_back_to_date():
    """'Réunion de commission (POxxx)' se comporte pareil."""
    with patch("src.site_export.amo_loader.resolve_organe", return_value=""):
        r = {
            "category": "agenda",
            "title": "Réunion de commission (PO873096)",
            "published_at": "2026-05-02T14:00:00",
        }
        _fix_agenda_row(r)
    assert r["title"] == "Réunion de commission AN du 02/05/2026"


def test_fix_agenda_row_last_resort_without_date_or_po_resolution():
    """Sans date ni résolution AMO, on affiche 'Réunion parlementaire'."""
    with patch("src.site_export.amo_loader.resolve_organe", return_value=""):
        r = {
            "category": "agenda",
            "title": "Réunion (PO999999)",
            "published_at": "",
        }
        _fix_agenda_row(r)
    assert r["title"] == "Réunion parlementaire"


def test_fix_agenda_row_preserves_good_titles():
    """Un titre déjà informatif (hors pattern 'Réunion (POxxx)') est laissé tel quel."""
    with patch("src.site_export.amo_loader.resolve_organe") as m:
        r = {
            "category": "agenda",
            "title": "Audition de M. Durand sur le financement du sport",
            "published_at": "2026-04-25T10:00:00",
        }
        _fix_agenda_row(r)
    m.assert_not_called()
    assert r["title"] == "Audition de M. Durand sur le financement du sport"
