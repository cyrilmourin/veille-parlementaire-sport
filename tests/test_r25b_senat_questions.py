"""Tests R25b-A / R25b-B / R25b-C (2026-04-23).

Contexte
--------
Trois correctifs sur les questions Sénat, appliqués à la fois au parser
(`src/sources/senat.py`) et au fixup d'export (`src/site_export._fix_question_row`)
pour couvrir les items déjà en DB (qui gardent leur ancien `title` figé
dans SQLite).

R25b-A : portraits sénateurs sur questions
    - L'ancien cache R23-N bâti uniquement depuis les amendements Sénat de
      la fenêtre couvrait trop peu de sénateurs (QAG typiquement non auteurs
      d'amendements). Ajout d'un index officiel `data/senat_slugs.json`
      (348 sénateurs en activité) consulté en fallback via `senat_slugs.py`.

R25b-B : retrait du « n°<uid> » dans les titres Sénat
    - Harmonisation avec le format AN épuré (« Question écrite : sujet »).
      Le numéro reste disponible via `raw.Numéro`.

R25b-C : détection question orale Sénat via colonne `Nature` du CSV
    - Le CSV `senat_questions_1an` mélange QE / QOSD / QG selon la colonne
      `Nature`. L'ancien mappage figé `senat_questions_1an → "Question
      écrite"` classait à tort des questions orales (cas 1054S).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.site_export import _fix_question_row  # noqa: E402
from src.sources.senat import _normalize_rows  # noqa: E402


# ---------------------------------------------------------------------------
# R25b-B + R25b-C : parser
# ---------------------------------------------------------------------------

def _row(**overrides):
    base = {
        "Numéro": "1054S",
        "Titre": "Gouvernance du comité d'organisation JOP hiver",
        "Texte": "Texte de la question.",
        "Rubrique": "sports",
        "Civilité": "Mme",
        "Prénom": "Cécile",
        "Nom": "Cukierman",
        "Groupe": "CRCE",
        "Nature": "QOSD",  # défaut : question orale sans débat
        "Ministère de dépôt": "Sports",
        "Date de publication JO": "2026-04-10",
        "URL Question": "http://www.senat.fr/questions/base/2023/qSEQ231054S.html",
    }
    base.update(overrides)
    return base


def test_parser_r25bc_qosd_labelled_correctly():
    """R25b-C : Nature='QOSD' doit produire 'Question orale sans débat'
    même si le sid dit `senat_questions_1an` (ancien mappage figé)."""
    src = {"id": "senat_questions_1an", "category": "questions"}
    items = list(_normalize_rows(src, [_row(Nature="QOSD")]))
    assert len(items) == 1
    assert items[0].title.startswith("Question orale sans débat")


def test_parser_r25bc_qe_labelled_correctly():
    """R25b-C : Nature='QE' → 'Question écrite' (quel que soit le sid)."""
    src = {"id": "senat_questions_1an", "category": "questions"}
    items = list(_normalize_rows(src, [_row(Nature="QE", Numéro="08141")]))
    assert items[0].title.startswith("Question écrite")


def test_parser_r25bc_qg_labelled_correctly():
    """R25b-C : Nature='QG' → 'Question au gouvernement'."""
    src = {"id": "senat_questions_1an", "category": "questions"}
    items = list(_normalize_rows(src, [_row(Nature="QG", Numéro="1079G")]))
    assert items[0].title.startswith("Question au gouvernement")


def test_parser_r25bc_nature_absent_fallback_sid():
    """Colonne Nature absente → fallback sur le label du sid."""
    src = {"id": "senat_qg", "category": "questions"}
    row = _row()
    row.pop("Nature")
    items = list(_normalize_rows(src, [row]))
    assert items[0].title.startswith("Question au gouvernement")


def test_parser_r25bb_strips_number():
    """R25b-B : le titre ne contient plus 'n°<uid>' (harmonisation AN)."""
    src = {"id": "senat_questions_1an", "category": "questions"}
    items = list(_normalize_rows(src, [_row(Nature="QE", Numéro="08141")]))
    assert "n°" not in items[0].title
    assert "08141" not in items[0].title
    # Le numéro reste dispo dans raw pour dédup.
    assert items[0].raw.get("Numéro") == "08141"


# ---------------------------------------------------------------------------
# R25b-B + R25b-C : fixup export (legacy DB items)
# ---------------------------------------------------------------------------

def test_fixup_r25bc_legacy_qosd_reclassed():
    """Item DB pré-R25b : titre 'Question de +1 an sans réponse n°1054S : …'
    avec raw.Nature='QOSD' doit être reclassé 'Question orale sans débat …'."""
    r = {
        "category": "questions",
        "title": "Question de +1 an sans réponse n°1054S : Gouvernance JOP hiver",
        "url": "https://www.senat.fr/questions/base/1054S.html",
        "raw": {
            "Nature": "QOSD",
            "Numéro": "1054S",
            "URL Question": "http://www.senat.fr/questions/base/2023/qSEQ231054S.html",
        },
        "source_id": "senat_questions_1an",
    }
    _fix_question_row(r)
    assert r["title"] == "Question orale sans débat : Gouvernance JOP hiver"


def test_fixup_r25bb_strips_numero_from_legacy_title():
    """R25b-B fixup : 'Question écrite n°08141 : Sujet' → 'Question écrite : Sujet'."""
    r = {
        "category": "questions",
        "title": "Question de +1 an sans réponse n°08141 : Hausse prélèvements",
        "url": "https://www.senat.fr/questions/base/08141.html",
        "raw": {"Nature": "QE", "Numéro": "08141"},
        "source_id": "senat_questions_1an",
    }
    _fix_question_row(r)
    assert r["title"] == "Question écrite : Hausse prélèvements"


def test_fixup_idempotent():
    """Appliquer _fix_question_row deux fois ne change rien après la 1re passe."""
    r = {
        "category": "questions",
        "title": "Question de +1 an sans réponse n°08141 : Hausse prélèvements",
        "url": "https://www.senat.fr/questions/base/08141.html",
        "raw": {"Nature": "QE", "Numéro": "08141"},
        "source_id": "senat_questions_1an",
    }
    _fix_question_row(r)
    first_pass = r["title"]
    _fix_question_row(r)
    assert r["title"] == first_pass


def test_fixup_preserves_an_titles():
    """R25b-B ne doit PAS toucher les titres AN déjà épurés (pas de
    Nature, pas de n° dans le titre)."""
    r = {
        "category": "questions",
        "title": "Question écrite : Financement des fédérations sportives",
        "url": "https://questions.assemblee-nationale.fr/q17/17-12345.htm",
        "raw": {"analyse": "Financement des fédérations sportives"},
        "source_id": "assemblee_questions",
    }
    before = r["title"]
    _fix_question_row(r)
    assert r["title"] == before


# ---------------------------------------------------------------------------
# R25b-A : senat_slugs + enrichissement photo
# ---------------------------------------------------------------------------

_FIXTURE_ENTRIES = [
    {
        "slug": "cukierman_cecile11056n",
        "nom_usuel": "CUKIERMAN",
        "prenom_usuel": "Cécile",
        "key": "cecile cukierman",
        "photo_url": "https://www.senat.fr/senimg/cukierman_cecile11056n_carre.jpg",
        "fiche_url": "https://www.senat.fr/senateur/cukierman_cecile11056n.html",
    },
    {
        "slug": "wattebled_dany19585h",
        "nom_usuel": "WATTEBLED",
        "prenom_usuel": "Dany",
        "key": "dany wattebled",
        "photo_url": "https://www.senat.fr/senimg/wattebled_dany19585h_carre.jpg",
        "fiche_url": "https://www.senat.fr/senateur/wattebled_dany19585h.html",
    },
    {
        "slug": "blanc_jean_baptiste20034v",
        "nom_usuel": "BLANC",
        "prenom_usuel": "Jean-Baptiste",
        "key": "blanc jean-baptiste",
        "photo_url": "https://www.senat.fr/senimg/blanc_jean_baptiste20034v_carre.jpg",
        "fiche_url": "https://www.senat.fr/senateur/blanc_jean_baptiste20034v.html",
    },
]


@pytest.fixture
def senat_slugs_fixture(tmp_path, monkeypatch):
    """Force `senat_slugs._JSON_PATH` à pointer sur un JSON de test, pour
    ne pas dépendre du fichier committed (qui peut muter quand on relance
    `scripts/build_senat_slugs.py`).
    """
    from src import senat_slugs
    payload = {
        "source_url": "test://",
        "count": len(_FIXTURE_ENTRIES),
        "entries": _FIXTURE_ENTRIES,
    }
    p = tmp_path / "senat_slugs.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(senat_slugs, "_JSON_PATH", p)
    senat_slugs.reset_cache_for_tests()
    yield senat_slugs
    senat_slugs.reset_cache_for_tests()


def test_senat_slugs_resolve_photo_basic(senat_slugs_fixture):
    """resolve_photo(civ, prenom, nom) retrouve le bon sénateur insensible
    à l'ordre / casse / accents / civilité."""
    mod = senat_slugs_fixture
    hit = mod.resolve_photo("Mme", "Cécile", "Cukierman")
    assert hit is not None
    photo, fiche = hit
    assert "cukierman_cecile11056n" in photo
    assert "cukierman_cecile11056n" in fiche


def test_senat_slugs_resolve_insensitive_to_case(senat_slugs_fixture):
    """MAJUSCULES dans le nom (CUKIERMAN) ou accents absents ne cassent
    pas le lookup."""
    mod = senat_slugs_fixture
    hit = mod.resolve_photo("MME", "cecile", "CUKIERMAN")
    assert hit is not None
    assert "cukierman_cecile11056n" in hit[0]


def test_senat_slugs_resolve_by_auteur_format(senat_slugs_fixture):
    """Entrée style `raw.auteur` amendement : `M. Jean-Baptiste BLANC` →
    même slug. Couvre le backfill R25b-A sur les amendements pré-R23-C5."""
    mod = senat_slugs_fixture
    hit = mod.resolve_by_auteur("M. Jean-Baptiste BLANC")
    assert hit is not None
    assert "blanc_jean_baptiste" in hit[0]


def test_senat_slugs_unknown_name_returns_none(senat_slugs_fixture):
    mod = senat_slugs_fixture
    assert mod.resolve_photo("M.", "Inconnu", "Personne") is None
    assert mod.resolve_by_auteur("") is None


def test_senat_slugs_missing_json_degrades_gracefully(tmp_path, monkeypatch):
    """Le JSON absent (fresh clone sans build) ne doit pas crasher : on
    renvoie None partout et le reste de la pipeline export continue."""
    from src import senat_slugs
    monkeypatch.setattr(senat_slugs, "_JSON_PATH", tmp_path / "missing.json")
    senat_slugs.reset_cache_for_tests()
    assert senat_slugs.resolve_photo("M.", "X", "Y") is None
    assert senat_slugs.resolve_by_auteur("M. X Y") is None
    senat_slugs.reset_cache_for_tests()


# ---------------------------------------------------------------------------
# R25b-A : intégration dans _build_senat_photo_cache + _enrich_senat_question_photo
# ---------------------------------------------------------------------------

def test_enrich_question_via_index_when_amdt_cache_empty(senat_slugs_fixture):
    """R25b-A : même si aucun amendement n'a de photo (cache R23-N vide),
    l'index officiel résout les questions."""
    from src.site_export import _build_senat_photo_cache, _enrich_senat_question_photo
    # 0 amendement avec photo : cache R23-N vide.
    cache = _build_senat_photo_cache([])
    assert cache == {}
    q = {
        "category": "questions",
        "chamber": "Senat",
        "title": "Question orale sans débat : Sujet",
        "raw": {
            "Civilité": "Mme",
            "Prénom": "Cécile",
            "Nom": "Cukierman",
        },
    }
    _enrich_senat_question_photo(q, cache)
    assert "cukierman_cecile11056n" in q["raw"].get("auteur_photo_url", "")


def test_build_cache_backfills_amdt_without_photo(senat_slugs_fixture):
    """Un amendement pré-R23-C5 (raw.auteur_photo_url vide) doit être
    backfillé dans le cache ET dans son propre raw via l'index officiel."""
    from src.site_export import _build_senat_photo_cache
    amdt = {
        "category": "amendements",
        "chamber": "Senat",
        "raw": {"auteur": "M. Dany WATTEBLED"},
    }
    cache = _build_senat_photo_cache([amdt])
    assert "dany wattebled" in cache
    # raw backfillé : le frontmatter d'export affichera aussi la photo
    # sur la fiche amendement elle-même.
    assert "wattebled_dany19585h" in amdt["raw"].get("auteur_photo_url", "")


def test_enrich_idempotent(senat_slugs_fixture):
    """Re-appliquer l'enrichissement ne remplace pas une photo déjà posée."""
    from src.site_export import _enrich_senat_question_photo
    q = {
        "category": "questions",
        "chamber": "Senat",
        "raw": {
            "Civilité": "Mme",
            "Prénom": "Cécile",
            "Nom": "Cukierman",
            "auteur_photo_url": "https://custom.example/deja_pose.jpg",
        },
    }
    _enrich_senat_question_photo(q, {})
    assert q["raw"]["auteur_photo_url"] == "https://custom.example/deja_pose.jpg"


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
