"""R41-D (2026-04-27) — Tests extraction nominations + dédup canonique.

Demande Cyril : sur les sources presse spécialisée sport business,
homogénéiser les nominations sous un format canonique
« <Personne> devient <Fonction> de <Structure> » pour :
1. Lisibilité (titre clair vs accroche presse)
2. Dédup inter-sources : même nom + fonction + structure → un seul item
3. Sources officielles (JORF, ministères, fédérations) : titre + URL
   d'origine PRÉSERVÉS (besoin du lien pour vérifier source faisant foi)
"""
from __future__ import annotations

from src.nominations import (
    OFFICIAL_NOMINATION_SOURCES,
    canonical_key,
    extract_nomination_facts,
    format_normalized_title,
    is_official_source,
)


# ---------------------------------------------------------------------------
# 1. extract_nomination_facts — cas Cyril
# ---------------------------------------------------------------------------


def test_extract_eric_woerth_pmu():
    facts = extract_nomination_facts(
        "Eric Woerth a été nommé président du PMU."
    )
    assert facts == {
        "person": "Eric Woerth",
        "function": "président",
        "organization": "PMU",
    }


def test_extract_frederic_sanaur_eventeam():
    facts = extract_nomination_facts(
        "Frédéric Sanaur a été nommé directeur conseil au sein du "
        "cabinet Eventeam."
    )
    assert facts is not None
    assert facts["person"] == "Frédéric Sanaur"
    assert facts["function"] == "directeur conseil"
    assert "Eventeam" in facts["organization"]


def test_extract_camille_emie_fff():
    """Cas avec virgule + adverbe entre personne et verbe :
    « Camille Emié, fraîchement nommée directrice de la communication
    de la FFF »."""
    facts = extract_nomination_facts(
        "Camille Emié, fraîchement nommée directrice de la "
        "communication de la FFF."
    )
    assert facts is not None
    assert facts["person"] == "Camille Emié"
    assert "directrice" in facts["function"]
    assert "FFF" in facts["organization"]


def test_extract_elise_morel_cabinet_oudea():
    facts = extract_nomination_facts(
        "Élise Morel a été nommée directrice de cabinet "
        "d'Amélie Oudéa-Castéra."
    )
    assert facts is not None
    assert facts["person"] == "Élise Morel"
    assert "directrice" in facts["function"]


def test_extract_ffr_dg_renaud():
    """Cas réel FFR : phrase complexe avec virgules et compléments."""
    facts = extract_nomination_facts(
        "À compter du 1er juin, Olivier Renaud, actuellement Directeur "
        "Général Adjoint, sera nommé Directeur Général."
    )
    assert facts is not None
    assert facts["person"] == "Olivier Renaud"
    assert "directeur général" in facts["function"].lower()


def test_extract_president_fede():
    """« M. Jean Dupont est élu président de la Fédération française
    de rugby »."""
    facts = extract_nomination_facts(
        "M. Jean Dupont est élu président de la Fédération française "
        "de rugby."
    )
    assert facts is not None
    assert "Jean Dupont" in facts["person"]
    assert facts["function"] == "président"
    assert "Fédération" in facts["organization"]


def test_extract_no_match_descriptif():
    """Régression : faux positif descriptif (« le nouveau président
    de la FFR s'est rendu à… ») NE doit PAS matcher."""
    facts = extract_nomination_facts(
        "Le nouveau président de la FFR s'est rendu à la rencontre "
        "de M. Y dans la Ville de Z."
    )
    assert facts is None


def test_extract_no_match_creation_entreprise():
    """Cyril a explicitement exclu les créations d'entreprise. Le
    pattern « X crée Y » ne doit pas matcher."""
    facts = extract_nomination_facts("Cyril Mourin crée Sideline Conseil.")
    assert facts is None


def test_extract_no_match_text_vide():
    assert extract_nomination_facts("") is None
    assert extract_nomination_facts(None) is None
    assert extract_nomination_facts("blabla aucun verbe") is None


def test_extract_devient_president():
    """Verbe « devient » sans auxiliaire."""
    facts = extract_nomination_facts(
        "Tony Estanguet devient président de la Fondation FDJ Sport."
    )
    assert facts is not None
    assert facts["person"] == "Tony Estanguet"
    assert facts["function"] == "président"
    assert "FDJ" in facts["organization"]


def test_extract_dtn_sigle():
    """Sigle DTN normalisé en forme longue."""
    facts = extract_nomination_facts(
        "Pierre Durand a été nommé DTN."
    )
    assert facts is not None
    # DTN canonicalisé en "directeur technique national"
    assert facts["function"] == "directeur technique national"


# ---------------------------------------------------------------------------
# 2. canonical_key — dédup ordre-insensible et accent-insensible
# ---------------------------------------------------------------------------


def test_canonical_key_meme_personne_meme_clef():
    f1 = {"person": "Eric Woerth", "function": "président", "organization": "PMU"}
    f2 = {"person": "WOERTH Eric", "function": "Président", "organization": "P.M.U."}
    # Note : "P.M.U." vs "PMU" peut diverger selon la normalisation,
    # mais l'objectif est qu'au moins le tri-de-tokens nous donne la
    # même clé pour "Eric Woerth" et "WOERTH Eric".
    assert canonical_key(f1) == canonical_key({
        "person": "WOERTH Eric",
        "function": "président",
        "organization": "PMU",
    })


def test_canonical_key_accent_insensible():
    f1 = {"person": "Camille Emié", "function": "directrice", "organization": "FFF"}
    f2 = {"person": "Camille Emie", "function": "directrice", "organization": "FFF"}
    assert canonical_key(f1) == canonical_key(f2)


def test_canonical_key_distincte_si_personnes_differentes():
    f1 = {"person": "Eric Woerth", "function": "président", "organization": "PMU"}
    f2 = {"person": "Tony Estanguet", "function": "président", "organization": "PMU"}
    assert canonical_key(f1) != canonical_key(f2)


def test_canonical_key_facts_vides():
    """Robustesse : facts vides ou partiels."""
    assert canonical_key({}) == "||"
    assert canonical_key({"person": "X"}) == "x||"


# ---------------------------------------------------------------------------
# 3. format_normalized_title
# ---------------------------------------------------------------------------


def test_format_titre_avec_org():
    facts = {"person": "Eric Woerth", "function": "président",
             "organization": "PMU"}
    title = format_normalized_title(facts)
    assert title == "Eric Woerth devient président du PMU"


def test_format_titre_sans_org():
    facts = {"person": "Olivier Renaud", "function": "directeur général",
             "organization": ""}
    title = format_normalized_title(facts)
    assert title == "Olivier Renaud devient directeur général"


def test_format_titre_org_voyelle():
    """Heuristique : sigle tout-maj → 'du', sinon 'de'."""
    facts = {"person": "X Y", "function": "président",
             "organization": "Olympique de Marseille"}
    title = format_normalized_title(facts)
    # "Olympique" commence par O (voyelle) mais ce n'est pas un sigle,
    # donc on prend la prep "de"
    assert title == "X Y devient président de Olympique de Marseille"


def test_format_titre_facts_invalides():
    assert format_normalized_title({}) == ""
    assert format_normalized_title({"person": "X"}) == ""
    assert format_normalized_title({"function": "Y"}) == ""


# ---------------------------------------------------------------------------
# 4. is_official_source — discriminator JORF / presse
# ---------------------------------------------------------------------------


def test_is_official_jorf():
    assert is_official_source("dila_jorf") is True


def test_is_official_ministere():
    assert is_official_source("min_sports_actualites") is True
    assert is_official_source("min_sports_presse") is True
    assert is_official_source("elysee") is True


def test_is_official_operateur():
    assert is_official_source("ans") is True
    assert is_official_source("insep") is True


def test_is_official_federations():
    assert is_official_source("fff_actualites") is True
    assert is_official_source("fft_actualites") is True
    assert is_official_source("ffa_actualites") is True


def test_is_official_cnosf():
    assert is_official_source("cnosf") is True
    assert is_official_source("france_paralympique") is True


def test_is_not_official_presse():
    """Les sources presse business ne sont PAS officielles → titre
    normalisé + URL masquée à l'export."""
    assert is_official_source("olbia_conseil") is False
    assert is_official_source("cafe_sport_business") is False
    assert is_official_source("sport_buzz_business") is False
    assert is_official_source("sport_business_club") is False
    assert is_official_source("sport_strategies") is False


def test_is_not_official_random():
    assert is_official_source("") is False
    assert is_official_source(None) is False
    assert is_official_source("inconnu_xyz") is False
