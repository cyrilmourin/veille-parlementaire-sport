"""Tests unitaires du matcher de mots-clés."""
from pathlib import Path

import pytest

from src.keywords import KeywordMatcher, _normalize


CONFIG = Path(__file__).resolve().parent.parent / "config" / "keywords.yml"


@pytest.fixture(scope="module")
def m():
    return KeywordMatcher(CONFIG)


def test_normalize_accents():
    assert _normalize("Éducation physique et sportive") == "education physique et sportive"
    assert _normalize("  Pass'Sport  ") == "pass'sport"


def test_normalize_decodes_html_entities():
    """R41-AO (2026-05-10) : régression — les flux RSS WordPress (Olbia,
    FFF, FFT, FFA, sport_strategies…) injectent des entités numériques
    `&#160;` (espace insécable) et `&#8217;` (apostrophe typographique).
    Sans `html.unescape` avant `unidecode`, la chaîne « nommé&#160;président »
    ne se normalise pas en « nomme president » → la famille
    `nomination_event` ne taggait JAMAIS les items presse business → tous
    supprimés par `_filter_nominations_only_sources` → 0 nominations
    presse visibles sur le site.
    """
    # Espace insécable encodé (&#160;) doit devenir un espace simple.
    assert _normalize("nommé&#160;président") == "nomme president"
    # Apostrophe typographique encodée.
    assert _normalize("d&#8217;une") == "d'une"
    # &nbsp; nommé doit aussi être décodé.
    assert _normalize("Pass&nbsp;Sport") == "pass sport"
    # &amp; → & (test de robustesse)
    assert _normalize("CNOSF &amp; CPSF") == "cnosf & cpsf"


def test_match_through_html_entities(m):
    """R41-AO : le match doit fonctionner même sur du texte qui contient
    des entités HTML — sinon les items presse business des sources RSS
    WordPress (qui en injectent systématiquement) ne taggent pas.
    """
    # Cas observé en prod sur olbia : « M. Dupond a été nommé&#160;président
    # de la FFF »
    kws, fams = m.match("M. Dupond a été nommé&#160;président de la FFF")
    # Le keyword "FFF" doit être détecté malgré l'absence d'espace propre.
    assert "FFF" in kws
    # Le keyword "nommé président" (ou variante) doit déclencher la
    # famille nomination_event si elle existe dans le yaml.
    # On vérifie au minimum que la famille federation est bien là (FFF).
    assert "federation" in fams


def test_match_dispositif(m):
    kws, fams = m.match("Élargissement du dispositif Pass'Sport aux jeunes")
    assert "Pass'Sport" in kws
    assert "dispositif" in fams


def test_match_acteur(m):
    kws, fams = m.match("Audition du président du CNOSF")
    assert "CNOSF" in kws
    assert "acteur" in fams


def test_match_federation(m):
    kws, _ = m.match("Réforme de la FFR et de la LFP")
    assert "FFR" in kws and "LFP" in kws


def test_match_evenement(m):
    kws, fams = m.match("Projet Alpes 2030 et héritage Paris 2024")
    assert "Alpes 2030" in kws
    assert "evenement" in fams


def test_no_match_unrelated(m):
    kws, _ = m.match("Plan national biodiversité 2026")
    assert kws == []


def test_recapitalize_maps_legacy_lowercase_kws_to_yaml_form(m):
    """Les items pré-R13-B ont des kws stockés en minuscules non-accentuées.
    `recapitalize` les remappe sur la forme du yaml courant (capitalisée).
    """
    out = m.recapitalize(
        ["jeux olympiques", "activite physique adaptee", "cnosf"]
    )
    # Chaque élément retrouve sa forme canonique (capitalisée ou sigle).
    assert "Jeux olympiques" in out
    assert "Activité physique adaptée" in out
    assert "CNOSF" in out
    # Aucun doublon même si plusieurs variantes de casse sont passées.
    assert len(out) == len(set(out))


def test_recapitalize_preserves_order_and_dedupes(m):
    out = m.recapitalize(["CNOSF", "cnosf", "CNOSF"])
    assert out == ["CNOSF"]


def test_recapitalize_leaves_unknown_kws_untouched(m):
    """Un kw absent du yaml (ex. source externe, ancien yaml) reste tel quel."""
    out = m.recapitalize(["Mot-inconnu-XYZ", "CNOSF"])
    assert "Mot-inconnu-XYZ" in out
    assert "CNOSF" in out


def test_recapitalize_empty_input(m):
    assert m.recapitalize([]) == []
    assert m.recapitalize(None) == []
