"""Tests R42-BI — filtre architectural famille nomination_event par
whitelist de sources.

Cyril : « les mots-clés pour les nominations 1/ ne fonctionnent que
pour les nominations 2/ et ne s'appliquent que pour les contenus
issus des publications [whitelistées] ». Les CR, dossiers législatifs,
questions, amendements, etc. ne doivent JAMAIS être visibles si leur
seul signal sport est nomination_event.
"""
from __future__ import annotations

import json

from src.site_export import (
    _NOMINATION_EVENT_SOURCES,
    _load_nomination_event_keyword_set,
    _strip_nomination_event_outside_whitelist,
)


# ---------------------------------------------------------------------------
# Whitelist : tous les ids attendus présents
# ---------------------------------------------------------------------------

def test_whitelist_contient_jorf():
    assert "dila_jorf" in _NOMINATION_EVENT_SOURCES


def test_whitelist_contient_presse_business_sport():
    for sid in ("olbia_conseil", "cafe_sport_business", "sport_buzz_business",
                "sport_business_club", "sport_strategies"):
        assert sid in _NOMINATION_EVENT_SOURCES, f"{sid} absent whitelist"


def test_whitelist_contient_mouvement_sportif():
    for sid in ("cnosf", "france_paralympique"):
        assert sid in _NOMINATION_EVENT_SOURCES, f"{sid} absent whitelist"


def test_whitelist_contient_min_sports():
    for sid in ("min_sports_actualites", "min_sports_presse",
                "min_sports_agenda"):
        assert sid in _NOMINATION_EVENT_SOURCES, f"{sid} absent whitelist"


def test_whitelist_contient_ans_fdsf():
    for sid in ("ans", "fdsf"):
        assert sid in _NOMINATION_EVENT_SOURCES, f"{sid} absent whitelist"


def test_whitelist_contient_federations():
    for sid in ("fff_actualites", "fft_actualites", "ffa_actualites"):
        assert sid in _NOMINATION_EVENT_SOURCES, f"{sid} absent whitelist"


def test_whitelist_exclut_parlement():
    """Aucune source parlementaire (AN/Sénat) dans la whitelist."""
    for sid in ("an_dossiers_legislatifs", "an_rapports",
                "an_rapports_information", "an_avis",
                "an_syceron", "an_cr_commissions",
                "an_amendements", "an_questions_ecrites",
                "senat_rapports", "senat_dosleg", "senat_debats",
                "senat_cri", "senat_amendements"):
        assert sid not in _NOMINATION_EVENT_SOURCES, f"{sid} présent à tort"


def test_whitelist_exclut_ministeres_hors_sport():
    """Min Education / Min Intérieur / Min Justice / etc. ne doivent
    PAS être autorisés à matcher nomination_event."""
    for sid in ("min_education", "min_interieur", "min_justice",
                "min_culture", "min_economie", "min_affaires_etrangeres",
                "min_enseignement_sup"):
        assert sid not in _NOMINATION_EVENT_SOURCES, f"{sid} présent à tort"


# ---------------------------------------------------------------------------
# Chargement du set des keywords nomination_event depuis le yaml
# ---------------------------------------------------------------------------

def test_load_keyword_set_non_vide():
    """Le yaml a bien des keywords nomination_event."""
    s = _load_nomination_event_keyword_set()
    assert len(s) > 10, f"Seulement {len(s)} keywords chargés"


def test_load_keyword_set_contient_termes_emblematiques():
    """Quelques termes connus de la famille."""
    s = _load_nomination_event_keyword_set()
    # Comparé en forme normalisée (lower + unidecode-like)
    from src.keywords import _normalize
    for raw in ("élu président", "nommé président",
                "succède à la présidence"):
        assert _normalize(raw) in s, f"{raw!r} absent du set"


# ---------------------------------------------------------------------------
# _strip_nomination_event_outside_whitelist
# ---------------------------------------------------------------------------

def _row(source_id: str, keywords: list[str], families: list[str]) -> dict:
    return {
        "source_id": source_id,
        "category": "communiques",
        "chamber": "AN",
        "keywords": list(keywords),
        "keyword_families": list(families),
    }


def test_strip_source_whitelistee_no_op():
    """Une source whitelistée garde ses keywords nomination_event."""
    rows = [_row("cnosf", ["Élu président"], ["nomination_event"])]
    out = _strip_nomination_event_outside_whitelist(rows)
    assert len(out) == 1
    assert out[0]["keywords"] == ["Élu président"]
    assert out[0]["keyword_families"] == ["nomination_event"]


def test_strip_source_hors_whitelist_avec_nomination_seul_drop():
    """Source hors whitelist + UNIQUEMENT nomination_event → drop."""
    rows = [_row("an_syceron", ["Élu président"], ["nomination_event"])]
    out = _strip_nomination_event_outside_whitelist(rows)
    assert len(out) == 0


def test_strip_source_hors_whitelist_avec_mix_garde_autres_familles():
    """Source hors whitelist + nomination_event + autre famille → strip
    nomination_event, garde les autres keywords."""
    rows = [_row("an_rapports",
                 ["Élu président", "Pass'Sport"],
                 ["nomination_event", "dispositif"])]
    out = _strip_nomination_event_outside_whitelist(rows)
    assert len(out) == 1
    assert out[0]["keywords"] == ["Pass'Sport"]
    assert out[0]["keyword_families"] == ["dispositif"]


def test_strip_source_hors_whitelist_sans_nomination_event_no_op():
    """Source hors whitelist sans nomination_event → no-op."""
    rows = [_row("an_rapports", ["Pass'Sport"], ["dispositif"])]
    out = _strip_nomination_event_outside_whitelist(rows)
    assert len(out) == 1
    assert out[0]["keywords"] == ["Pass'Sport"]
    assert out[0]["keyword_families"] == ["dispositif"]


def test_strip_couvre_toutes_categories():
    """Le strip s'applique quelle que soit la catégorie (CR, dosleg,
    questions, amendements, agenda, etc.) — pas seulement communiques."""
    for cat in ("comptes_rendus", "dossiers_legislatifs", "questions",
                "amendements", "agenda", "jorf"):
        rows = [{
            "source_id": "an_syceron",
            "category": cat,
            "chamber": "AN",
            "keywords": ["Élu président"],
            "keyword_families": ["nomination_event"],
        }]
        out = _strip_nomination_event_outside_whitelist(rows)
        assert len(out) == 0, f"cat={cat} pas dropé"


def test_strip_compare_keywords_normalises():
    """Le strip compare en forme normalisée (lower + accents) — les
    matched_keywords recapitalisés sont reconnus."""
    # Le yaml a « Loi democratiser le sport » et « Démocratiser le sport »
    # côté dispositif (pas nomination_event). On utilise « Élu président »
    # qui EST nomination_event.
    rows = [_row("an_syceron",
                 ["ÉLU PRÉSIDENT"],  # casse différente
                 ["nomination_event"])]
    out = _strip_nomination_event_outside_whitelist(rows)
    assert len(out) == 0


def test_strip_keyword_families_string_json():
    """Robustesse : keyword_families stocké comme string JSON (lecture
    brute DB) est parsé correctement."""
    rows = [{
        "source_id": "an_syceron",
        "category": "comptes_rendus",
        "chamber": "AN",
        "keywords": '["Élu président"]',
        "keyword_families": '["nomination_event"]',
    }]
    out = _strip_nomination_event_outside_whitelist(rows)
    assert len(out) == 0


def test_strip_idempotent():
    """2 passes successives donnent le même résultat (pas de boucle
    sale)."""
    rows = [_row("an_rapports",
                 ["Élu président", "Pass'Sport"],
                 ["nomination_event", "dispositif"])]
    out1 = _strip_nomination_event_outside_whitelist(rows)
    out2 = _strip_nomination_event_outside_whitelist(out1)
    assert len(out2) == 1
    assert out2[0]["keywords"] == ["Pass'Sport"]


def test_strip_preserve_les_rows_originaux_des_autres():
    """Si un row source whitelistée n'est pas touché, c'est l'objet
    original qui passe (pas une copie)."""
    src_row = _row("cnosf", ["Élu président"], ["nomination_event"])
    out = _strip_nomination_event_outside_whitelist([src_row])
    assert out[0] is src_row
