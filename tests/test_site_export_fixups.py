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
    _amendement_chip,
    _build_senat_photo_cache,
    _enrich_senat_question_photo,
    _fix_agenda_row,
    _fix_amendement_row,
    _fix_chamber_row,
    _fix_cr_row,
    _fix_dossier_row,
    _fix_question_row,
    _load,
    _normalize_auteur_name_senat,
    _strip_cr_an_preamble,
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
    # Plus de → ministère ni [sort], espacement propre autour du colon.
    # R13-L (2026-04-21) : l'auteur + groupe sont aussi retirés du titre
    # (affichés maintenant via .auteur-inline, barre verticale séparateur).
    assert "→" not in r["title"]
    assert "[En cours]" not in r["title"]
    assert "Sports, jeunesse et vie associative" not in r["title"]
    assert "Gouvernance du comité" in r["title"]
    # R23-D (2026-04-23) : le préfixe "Question de +1 an sans réponse" est
    # réécrit en "Question écrite" (étiquette trompeuse vu les re-dépôts
    # automatiques côté Sénat). Le sid source reste préservé pour les
    # compteurs digest.
    # R25b-B (2026-04-23) : le n°<uid> est aussi retiré du titre pour
    # harmoniser avec l'AN (« Question écrite : sujet »).
    assert r["title"].startswith("Question écrite")
    assert "n°" not in r["title"]
    assert "Question de +1 an" not in r["title"]
    assert "Cyril Pellevat" not in r["title"]
    assert "(Les Indépendants)" not in r["title"]


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
    """Appliquer 2 fois ne change rien au titre (hors 1re normalisation R13-L)."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite n°14369 — M. Jean Dupont (RN) : sports",
            "raw": {},
        }
        _fix_question_row(r)
        after_first = r["title"]
        _fix_question_row(r)
        after_second = r["title"]
    # R13-L : 1re passe retire "— M. Jean Dupont (RN)", 2e passe doit être no-op.
    assert after_first == after_second
    assert "M. Jean Dupont" not in after_first
    assert "sports" in after_first


def test_fix_question_row_ignores_other_categories():
    r = {"category": "agenda", "title": "Réunion X → ministre : thème", "raw": {}}
    before = r["title"]
    _fix_question_row(r)
    assert r["title"] == before


def test_fix_question_row_handles_unknown_deputy():
    """Si le cache AMO n'a pas la clé et R13-L retire auteur/groupe, le titre
    résultant ne contient plus le code — on garde juste le sujet."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite n°1 — Député PA999999 (NI) : sujet",
            "raw": {"auteur": "Député PA999999", "auteur_ref": "PA999999"},
        }
        _fix_question_row(r)
    # R13-L : l'auteur (incluant Député PAxxx) est retiré du titre.
    # R25b-B : n°1 aussi retiré.
    assert "Député PA999999" not in r["title"]
    assert r["title"].startswith("Question écrite")
    assert "n°" not in r["title"]
    assert "sujet" in r["title"]


# ---------- _fix_question_row (R22i : URL legacy Sénat questions) ----------

def test_fix_question_row_rewrites_broken_senat_url_from_raw():
    """R22i : les items `senat_qg` / `senat_questions_1an` ingérés avant R22i
    ont une URL construite au format `.../base/{uid}.html` qui renvoie 404.
    La vraie URL est livrée dans la colonne CSV `URL Question` (stockée en
    raw) et suit le pattern `.../base/YYYY/qSEQ…<num>.html`. Le fixup doit
    la réécrire et forcer https://."""
    r = {
        "category": "questions",
        "source_id": "senat_questions_1an",
        "title": "Question de +1 an sans réponse n°1054S : Gouvernance …",
        "url": "https://www.senat.fr/questions/base/1054S.html",
        "raw": {
            "Numéro": "1054S",
            "Référence": "SEQ26041054S",
            "URL Question": "http://www.senat.fr/questions/base/2026/qSEQ26041054S.html",
        },
    }
    _fix_question_row(r)
    assert r["url"] == "https://www.senat.fr/questions/base/2026/qSEQ26041054S.html"


def test_fix_question_row_rewrites_broken_senat_url_for_senat_qg():
    """Même fix, source_id = senat_qg."""
    r = {
        "category": "questions",
        "source_id": "senat_qg",
        "title": "Question au gouvernement n°0001G : …",
        "url": "https://www.senat.fr/questions/base/0001G.html",
        "raw": {
            "URL Question": "http://www.senat.fr/questions/base/2024/qSEQ24100001G.html",
        },
    }
    _fix_question_row(r)
    assert r["url"] == "https://www.senat.fr/questions/base/2024/qSEQ24100001G.html"


def test_fix_question_row_keeps_url_when_raw_url_missing():
    """Si raw["URL Question"] est absent, on laisse l'URL legacy telle
    quelle (pas de pire que ce qu'on avait, et surtout pas d'erreur)."""
    r = {
        "category": "questions",
        "source_id": "senat_qg",
        "title": "Question au gouvernement n°0001G : …",
        "url": "https://www.senat.fr/questions/base/0001G.html",
        "raw": {},
    }
    _fix_question_row(r)
    assert r["url"] == "https://www.senat.fr/questions/base/0001G.html"


def test_fix_question_row_noop_when_url_already_correct():
    """Idempotent : si l'URL est déjà au format canonique, pas de
    réécriture."""
    good = "https://www.senat.fr/questions/base/2026/qSEQ26041054S.html"
    r = {
        "category": "questions",
        "source_id": "senat_questions_1an",
        "title": "Question …",
        "url": good,
        "raw": {"URL Question": "http://www.senat.fr/questions/base/2026/qSEQ26041054S.html"},
    }
    _fix_question_row(r)
    assert r["url"] == good


def test_fix_question_row_noop_for_an_questions():
    """Le fixup URL R22i ne s'applique pas aux questions AN (source_id
    différent, URLs assemblee-nationale.fr)."""
    an_url = "https://questions.assemblee-nationale.fr/q17/17-12345QE.htm"
    r = {
        "category": "questions",
        "source_id": "an_questions",
        "title": "Question écrite n°12345 : sujet",
        "url": an_url,
        "raw": {},
    }
    _fix_question_row(r)
    assert r["url"] == an_url


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
    # R13-O : auteur retiré du titre (affiché via .auteur-inline). On
    # vérifie juste que le code PA ne réapparaît plus, pas que le nom y est.
    assert "Amdt n°57" in r["title"]
    assert "Amendement" not in r["title"]
    assert "art. ARTICLE 5" in r["title"]


def test_fix_amendement_row_falls_back_to_captured_code_if_no_raw_ref():
    """R13-O : l'auteur est toujours retiré du titre. Le code PAxxxx
    disparaît aussi via l'étape "retire — Auteur" du fixup."""
    with patch("src.site_export.amo_loader.resolve_acteur") as m:
        m.return_value = ""
        r = {
            "category": "amendements",
            "title": "Amendement n°52 [Discuté] — Député PA795746 · art. 5",
            "raw": {"numero": "52"},
        }
        _fix_amendement_row(r)
    assert "Député PA795746" not in r["title"]
    assert "[Discuté]" not in r["title"]
    assert "Amdt n°52" in r["title"]


def test_fix_amendement_row_removes_author_r13o():
    """R13-O : l'auteur et le statut inline sont retirés du titre."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "amendements",
            "title": "Amendement n°10 [Discuté] — Mme Sandrine Rousseau · art. 3",
            "raw": {"auteur_ref": "PA720892"},
        }
        _fix_amendement_row(r)
    assert r["title"] == "Amdt n°10 · art. 3"


def test_fix_amendement_row_idempotent_r13o():
    """Appliquer 2 fois laisse le titre stable."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "amendements",
            "title": "Amendement n°1 — M. Dupont · art. 2",
            "raw": {"auteur_ref": "PA111"},
        }
        _fix_amendement_row(r)
        after_first = r["title"]
        _fix_amendement_row(r)
        after_second = r["title"]
    assert after_first == "Amdt n°1 · art. 2"
    assert after_second == after_first


def test_fix_amendement_row_amdt_alone_noop():
    """Un titre déjà nettoyé (Amdt sans auteur/statut) n'est pas modifié."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "amendements",
            "title": "Amdt n°1 · art. 2",
            "raw": {"auteur_ref": "PA111"},
        }
        _fix_amendement_row(r)
    assert r["title"] == "Amdt n°1 · art. 2"


def test_fix_amendement_row_ignores_other_categories():
    r = {
        "category": "questions",
        "title": "Question — Député PA123",
        "raw": {"auteur_ref": "PA123"},
    }
    _fix_amendement_row(r)
    assert r["title"] == "Question — Député PA123"  # inchangé


# ---------- _amendement_chip (R23-A — sort prime sous_etat > etat) ----------

def test_amendement_chip_prefers_sort_over_sous_etat_and_etat():
    """R23-A : `sort` non vide gagne toujours, peu importe le reste."""
    label, slug = _amendement_chip({
        "sort": "Adopté",
        "sous_etat": "Tombé",
        "etat": "Discuté",
        "statut": "Légacy",
    })
    assert label == "Adopté"
    assert slug == "adopte"


def test_amendement_chip_falls_back_to_sous_etat_when_sort_empty():
    """R23-A : regression directe — avant R23-A, on tombait sur "Discuté"
    (etat) alors que sousEtat était "Tombé". On prouve que sous_etat passe
    devant etat quand sort est vide."""
    label, slug = _amendement_chip({
        "sort": "",
        "sous_etat": "Tombé",
        "etat": "Discuté",
    })
    assert label == "Tombé"
    assert slug == "tombe"


def test_amendement_chip_falls_back_to_etat_when_sort_and_sous_etat_empty():
    label, slug = _amendement_chip({
        "sort": "",
        "sous_etat": "",
        "etat": "En traitement",
    })
    assert label == "En traitement"
    assert slug == "en-traitement"


def test_amendement_chip_falls_back_to_statut_legacy():
    """Items ingérés avant la séparation sort/etat (pre-R13-J) n'ont que
    `raw.statut`. On garde le fallback final sur ce champ."""
    label, slug = _amendement_chip({"statut": "Adopté"})
    assert label == "Adopté"
    assert slug == "adopte"


def test_amendement_chip_empty_when_no_field_set():
    assert _amendement_chip({}) == ("", "")
    assert _amendement_chip({"sort": "", "etat": ""}) == ("", "")


def test_amendement_chip_slug_handles_accents_and_spaces():
    label, slug = _amendement_chip({"sort": "Adopté sans modif."})
    assert label == "Adopté sans modif."
    assert slug == "adopte-sans-modif"


def test_amendement_chip_noop_on_non_dict():
    assert _amendement_chip(None) == ("", "")  # type: ignore[arg-type]
    assert _amendement_chip("rejeté") == ("", "")  # type: ignore[arg-type]


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
    # R13-L : auteur + groupe retirés du titre aussi (affichés via .auteur-inline).
    assert r["title"].startswith("Question écrite : ")
    assert "12/04/2026" not in r["title"]
    assert "Mme X" not in r["title"]


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
    """R13-J patch 3 + R13-L : la date dupliquée ET l'auteur sont retirés
    du titre (affichés séparément en meta-line + .auteur-inline)."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite · 12/04/2026 — Mme Y (LFI) : sport scolaire",
            "raw": {"auteur": "Mme Y", "auteur_ref": "PA1"},
        }
        _fix_question_row(r)
    assert "12/04/2026" not in r["title"]
    assert "Question écrite" in r["title"]
    # R13-L : auteur retiré du titre (affiché via .auteur-inline).
    assert "Mme Y" not in r["title"]
    assert "sport scolaire" in r["title"]


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


# ---------- _fix_question_row (R23-D : retire préfixe "+1 an sans réponse") -

def test_fix_question_row_rewrites_1an_prefix_to_question_ecrite():
    """R23-D (2026-04-23) : le préfixe 'Question de +1 an sans réponse'
    devient 'Question écrite' (l'étiquette était trompeuse vu les
    re-dépôts automatiques). Le sid source reste intact côté `source_id`.
    """
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "source_id": "senat_questions_1an",
            "title": "Question de +1 an sans réponse n°12345S : sport scolaire",
            "raw": {},
        }
        _fix_question_row(r)
    # R25b-B : le n°<uid> est retiré du titre pour harmoniser avec l'AN.
    assert r["title"].startswith("Question écrite")
    assert "n°" not in r["title"]
    assert "+1 an" not in r["title"]
    assert "sans réponse" not in r["title"]


def test_fix_question_row_1an_prefix_idempotent():
    """Idempotent : la réécriture ne s'applique pas deux fois."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "source_id": "senat_questions_1an",
            "title": "Question écrite n°12345S : sport scolaire",
            "raw": {},
        }
        _fix_question_row(r)
        first = r["title"]
        _fix_question_row(r)
    assert r["title"] == first


def test_fix_question_row_1an_prefix_preserves_source_id():
    """Le `source_id` reste `senat_questions_1an` (utile pour compter les
    questions longues sans réponse côté digest, même si le titre affiché
    devient neutre)."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "source_id": "senat_questions_1an",
            "title": "Question de +1 an sans réponse n°999S : dopage",
            "raw": {},
        }
        _fix_question_row(r)
    # R25b-B : le n°<uid> est retiré du titre (harmonisation AN).
    assert r["source_id"] == "senat_questions_1an"
    assert r["title"].startswith("Question écrite")
    assert "n°" not in r["title"]


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


# ---------- _strip_cr_an_preamble (R23-F : CR AN sans préambule Syceron) ---

def test_strip_cr_an_preamble_cuts_at_presidence_marker():
    """Préambule Syceron réaliste (CRSANR5L17…, timestamps, numéros isolés,
    `valide complet public avant_JO PROD`) → la coupe doit se faire à
    « Présidence » et ne garder que le corps."""
    raw = (
        "CRSANR5L17S2026O1N130 RUANR5L17S2026IDS30183 SCR5A2026O1 "
        "20260128140000000 mercredi 28 janvier 2026 1 130 AN 17 "
        "Session ordinaire 2025 -2026 20260130 valide complet public "
        "avant_JO PROD 2026-02-05T18:15:24.000+01:00 Présidence de "
        "Mme Yaël Braun-Pivet La séance est ouverte à quinze heures."
    )
    out = _strip_cr_an_preamble(raw)
    assert out.startswith("Présidence de Mme Yaël Braun-Pivet")
    assert "CRSANR5L17" not in out
    assert "avant_JO PROD" not in out


def test_strip_cr_an_preamble_no_marker_returns_unchanged():
    """Pas de marqueur dans les 600 premiers caractères → haystack inchangé
    (cas d'un summary déjà propre ou d'un format inattendu)."""
    raw = (
        "Texte déjà propre sans préambule technique, compte rendu court "
        "évoquant le financement du sport amateur."
    )
    assert _strip_cr_an_preamble(raw) == raw


def test_strip_cr_an_preamble_picks_earliest_marker():
    """Plusieurs marqueurs présents → on coupe au PLUS TOT (le premier
    trouvé dans le haystack), pas à l'ordre dans _CR_AN_BODY_MARKERS."""
    raw = (
        "CRSANR5L17S2026 PROD 2026-02-05 "
        "La séance est ouverte à neuf heures. "
        "Présidence de M. Président. "
        "Questions au gouvernement suivent."
    )
    out = _strip_cr_an_preamble(raw)
    assert out.startswith("La séance est ouverte")
    assert "CRSANR5L17" not in out


def test_strip_cr_an_preamble_is_idempotent():
    """Appliquer deux fois = appliquer une fois (le résultat ne recommence
    pas à couper sur un marqueur déjà rapproché du début)."""
    raw = (
        "CRSANR5L17S2026O1N130 PROD 2026-02-05 Présidence de Mme "
        "Braun-Pivet La séance est ouverte."
    )
    once = _strip_cr_an_preamble(raw)
    twice = _strip_cr_an_preamble(once)
    assert once == twice
    assert once.startswith("Présidence")


def test_strip_cr_an_preamble_empty_input():
    """Chaîne vide ou None-like → renvoyée telle quelle sans exception."""
    assert _strip_cr_an_preamble("") == ""


def test_strip_cr_an_preamble_marker_beyond_max_prefix_ignored():
    """Marqueur au-delà de `max_prefix` caractères → pas de coupe (évite
    de massacrer un summary long où « Présidence » n'est mentionné qu'en
    plein milieu d'une phrase)."""
    prefix = "x" * 700
    raw = prefix + "Présidence de M. X."
    assert _strip_cr_an_preamble(raw) == raw


# ---------- R23-N : cache photo Sénat depuis amendements -------------------

def test_normalize_auteur_name_senat_basic():
    """Ordre nom/prénom indifférent, civilité retirée."""
    k1 = _normalize_auteur_name_senat("M. Dany WATTEBLED")
    k2 = _normalize_auteur_name_senat("WATTEBLED Dany")
    k3 = _normalize_auteur_name_senat("Dany Wattebled")
    assert k1 == k2 == k3 == "dany wattebled"


def test_normalize_auteur_name_senat_strips_accents():
    """Les accents ne bloquent pas le matching (Mélanie Vogel/Vogel Mélanie)."""
    k1 = _normalize_auteur_name_senat("Mme Mélanie VOGEL")
    k2 = _normalize_auteur_name_senat("Vogel Mélanie")
    assert k1 == k2 == "melanie vogel"


def test_normalize_auteur_name_senat_empty():
    assert _normalize_auteur_name_senat("") == ""
    assert _normalize_auteur_name_senat("M.") == ""


def test_build_senat_photo_cache_indexes_amendements_only():
    """Le cache ne prend que les rows amendements Sénat avec photo non vide."""
    rows = [
        {
            "category": "amendements",
            "chamber": "Senat",
            "raw": {
                "auteur": "WATTEBLED Dany",
                "auteur_url": "https://www.senat.fr/senfic/wattebled_dany19585h.html",
                "auteur_photo_url": "https://www.senat.fr/senimg/wattebled_dany19585h_carre.jpg",
            },
        },
        # Row amendement AN : ignoré (chamber != Senat).
        {
            "category": "amendements",
            "chamber": "AN",
            "raw": {
                "auteur": "WATTEBLED Dany",
                "auteur_photo_url": "autre.jpg",
            },
        },
        # Row Sénat question : ignoré (pas amendement).
        {
            "category": "questions",
            "chamber": "Senat",
            "raw": {"auteur": "WATTEBLED Dany", "auteur_photo_url": "x.jpg"},
        },
    ]
    cache = _build_senat_photo_cache(rows)
    assert "dany wattebled" in cache
    assert cache["dany wattebled"][0].endswith("_carre.jpg")
    # Une seule entrée — ni AN, ni question n'ont alimenté le cache.
    assert len(cache) == 1


def test_build_senat_photo_cache_skips_rows_without_photo():
    rows = [{
        "category": "amendements",
        "chamber": "Senat",
        "raw": {"auteur": "DUPONT Jean", "auteur_photo_url": "", "auteur_url": ""},
    }]
    assert _build_senat_photo_cache(rows) == {}


def test_enrich_senat_question_photo_injects_from_cache():
    """Une question Sénat avec nom en cache reçoit photo + fiche."""
    cache = {
        "dany wattebled": (
            "https://www.senat.fr/senimg/wattebled_dany19585h_carre.jpg",
            "https://www.senat.fr/senfic/wattebled_dany19585h.html",
        )
    }
    r = {
        "category": "questions",
        "chamber": "Senat",
        "raw": {
            "Civilité": "M.",
            "Prénom": "Dany",
            "Nom": "WATTEBLED",
        },
    }
    _enrich_senat_question_photo(r, cache)
    assert r["raw"]["auteur_photo_url"].endswith("_carre.jpg")
    assert r["raw"]["auteur_url"].endswith(".html")


def test_enrich_senat_question_photo_noop_if_already_populated():
    """Idempotent : si la photo est déjà présente, on ne touche à rien."""
    cache = {"dany wattebled": ("nouveau.jpg", "nouvelle_fiche.html")}
    r = {
        "category": "questions",
        "chamber": "Senat",
        "raw": {
            "Prénom": "Dany",
            "Nom": "WATTEBLED",
            "auteur_photo_url": "existant.jpg",
            "auteur_url": "existant.html",
        },
    }
    _enrich_senat_question_photo(r, cache)
    assert r["raw"]["auteur_photo_url"] == "existant.jpg"
    assert r["raw"]["auteur_url"] == "existant.html"


def test_enrich_senat_question_photo_ignores_non_senat():
    """Les questions AN ne sont pas enrichies depuis le cache Sénat."""
    cache = {"jean dupont": ("senat.jpg", "senat.html")}
    r = {
        "category": "questions",
        "chamber": "AN",
        "raw": {"Prénom": "Jean", "Nom": "DUPONT"},
    }
    _enrich_senat_question_photo(r, cache)
    assert "auteur_photo_url" not in r["raw"]


def test_enrich_senat_question_photo_uses_auteur_fallback():
    """Si Civilité/Prénom/Nom sont absents, on tombe sur raw["auteur"]."""
    cache = {"dany wattebled": ("p.jpg", "f.html")}
    r = {
        "category": "questions",
        "chamber": "Senat",
        "raw": {"auteur": "M. Dany WATTEBLED"},
    }
    _enrich_senat_question_photo(r, cache)
    assert r["raw"]["auteur_photo_url"] == "p.jpg"


def test_enrich_senat_question_photo_miss_returns_silently():
    """Nom inconnu du cache R23-N ET de l'index senat_slugs → aucun changement,
    pas d'exception. R25b-A : on mocke `resolve_by_auteur` pour isoler le test
    du contenu réel de data/senat_slugs.json (qui contient les 348 sénateurs
    en activité, donc un vrai nom comme WATTEBLED y serait trouvé)."""
    cache = {"autre personne": ("x", "y")}
    r = {
        "category": "questions",
        "chamber": "Senat",
        "raw": {"Prénom": "Zzinconnu", "Nom": "PERSONNEINEXISTANTE"},
    }
    with patch("src.senat_slugs.resolve_by_auteur", return_value=None):
        _enrich_senat_question_photo(r, cache)
    assert "auteur_photo_url" not in r["raw"]


# ---------- R25-C : dédup QAG vs question écrite (numéro en « G ») ----------

def test_fix_question_row_rewrites_question_ecrite_G_to_qag():
    """n°0701G + label "Question écrite" → "Question au gouvernement".
    R25b-B (2026-04-23) : le n°0701G est ensuite strippé du titre."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite n°0701G : Financement du sport",
            "raw": {},
        }
        _fix_question_row(r)
    assert r["title"].startswith("Question au gouvernement")
    assert "Question écrite" not in r["title"]
    assert "n°" not in r["title"]


def test_fix_question_row_rewrites_1an_prefix_G_to_qag():
    """Legacy préfixe "Question de +1 an sans réponse" sur numéro en G :
    R23-D le réécrit d'abord en "Question écrite", puis R25-C le remappe
    en "Question au gouvernement" vu le suffixe G, enfin R25b-B strippe
    le n°."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question de +1 an sans réponse n°0701G : sujet",
            "raw": {},
        }
        _fix_question_row(r)
    assert r["title"].startswith("Question au gouvernement")
    assert "+1 an" not in r["title"]
    assert "Question écrite" not in r["title"]
    assert "n°" not in r["title"]


def test_fix_question_row_keeps_question_ecrite_S_suffix():
    """Numéro en « S » (question écrite canonique) : pas de remap R25-C.
    R25b-B strippe tout de même le n°."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite n°1054S : sujet",
            "raw": {},
        }
        _fix_question_row(r)
    assert r["title"].startswith("Question écrite")
    assert "n°" not in r["title"]


def test_fix_question_row_r25c_idempotent():
    """Un titre déjà étiqueté "Question au gouvernement n°xxxG" ne change pas
    après la 1re passe (R25b-B strippe le n°, 2e passe no-op)."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question au gouvernement n°0701G : sujet",
            "raw": {},
        }
        _fix_question_row(r)
        after_first = r["title"]
        _fix_question_row(r)
        after_second = r["title"]
    assert after_first == after_second
    assert after_first.startswith("Question au gouvernement")
    assert "n°" not in after_first


def test_fix_question_row_r25c_ignores_numeric_only_numero():
    """Numéro purement numérique (AN questions écrites) : pas de remap R25-C.
    R25b-B strippe tout de même le n° pour harmoniser avec le format AN
    épuré « Question écrite : sujet »."""
    with patch("src.site_export.amo_loader.resolve_acteur", return_value=""):
        r = {
            "category": "questions",
            "title": "Question écrite n°14369 : sujet",
            "raw": {},
        }
        _fix_question_row(r)
    assert r["title"].startswith("Question écrite")
    assert "n°" not in r["title"]
