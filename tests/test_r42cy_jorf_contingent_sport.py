"""R42-CY (2026-05-15) — Décret JORF Ordre national du Mérite avec
contingent sport doit matcher la veille.

Cyril 2026-05-15 : « Il y a au JORF un décret de nomination à l'ordre
national du mérite qui n'apparaît pas alors qu'il y a un contingent
sport. Mets vite à jour le site ».

Cause racine identifiée :
  1. Le `haystack_body` extrait par `_parse_texte_version` était capé à
     3000 chars. Les décrets Ordre national / Légion d'honneur listent
     les nommés par ministère dans l'ordre alphabétique → le ministère
     des Sports apparaît typiquement après la 2000-3000e position. Avec
     un cap à 3000, le matcher ne voyait pas la section sport.
  2. R41-I (`_filter_jorf_nominations_hors_sport`) écarte les
     nominations JORF dont la seule famille est `nomination_event`. Sans
     match sur le ministère des sports dans le body, le décret n'avait
     que des keywords nomination_event → écarté.

Fix : cape 3000 → 8000 chars (`body_head` ET `articles_by_cid`), +
nouveaux keywords défensifs « Au titre du ministère des sports »,
« Contingent du ministère des sports », variantes secrétaire d'État.
"""
from __future__ import annotations

from src.keywords import KeywordMatcher


def _fake_decret_body_8k(sport_at_position: int) -> str:
    """Construit un faux corps de décret de promotion à l'ordre national
    du Mérite : préambule + ministères alphabétiques jusqu'à atteindre
    `sport_at_position`, puis section « Sports » avec keyword sport.
    """
    preamble = (
        "Décret du 12 mai 2026 portant promotion et nomination dans "
        "l'ordre national du Mérite. Le Président de la République, "
        "Sur le rapport du Premier ministre, Vu le code de la Légion "
        "d'honneur et de la Médaille militaire et de l'ordre national "
        "du Mérite, Vu le décret n° 63-1196 du 3 décembre 1963 modifié "
        "portant création d'un ordre national du Mérite, Décrète : "
        "Article 1 - Sont promus dans l'ordre national du Mérite. "
    )
    filler_ministries = (
        "Au titre du Premier ministre. Au grade de commandeur. "
        "M. X, président. M. Y, directeur. "
        "Au titre du ministère de l'agriculture. Au grade de chevalier. "
        "Mme A, ingénieur. M. B, directeur d'établissement. "
        "Au titre du ministère des armées. Au grade d'officier. "
        "M. C, général de division. Mme D, médecin militaire. "
        "Au titre du ministère de la culture. Au grade de chevalier. "
        "M. E, conservateur du patrimoine. Mme F, directrice de musée. "
        "Au titre du ministère de l'éducation nationale. Au grade de "
        "chevalier. M. G, professeur agrégé. Mme H, principale de collège. "
        "Au titre du ministère de la santé. Au grade d'officier. "
        "M. I, professeur de médecine. Mme J, infirmière coordinatrice. "
    )
    # Étire le filler jusqu'à `sport_at_position` chars
    while len(preamble + filler_ministries) < sport_at_position:
        filler_ministries += filler_ministries[:200]
    body_before_sport = (preamble + filler_ministries)[:sport_at_position]
    sport_section = (
        " Au titre du ministère des Sports. Au grade de chevalier. "
        "M. K, président de la fédération française de natation. "
        "Mme L, ancienne championne olympique. "
        "M. M, directeur technique national. "
    )
    return body_before_sport + sport_section


def test_keyword_au_titre_du_ministere_des_sports_matche():
    """Keyword défensif « Au titre du ministère des sports » présent."""
    matcher = KeywordMatcher("config/keywords.yml")
    kws, fams = matcher.match(
        "Décret du 12 mai 2026 portant promotion et nomination dans "
        "l'ordre national du Mérite",
        "",
        "Au titre du ministère des sports. Au grade de chevalier.",
    )
    assert any("sport" in k.lower() for k in kws), (
        f"Au moins un keyword sport attendu. Obtenu : {kws}"
    )
    # Doit matcher une famille sport-spécifique (pas seulement nomination_event)
    assert "acteur" in fams or "federation" in fams, (
        f"Famille acteur ou federation attendue. Obtenu : {fams}"
    )


def test_keyword_contingent_du_ministere_des_sports_matche():
    matcher = KeywordMatcher("config/keywords.yml")
    kws, fams = matcher.match(
        "Décret du 14 mai 2026 portant nomination ordre national du Mérite",
        "",
        "Contingent du ministère des sports. Au grade d'officier.",
    )
    assert "acteur" in fams


def test_decret_avec_sport_a_position_3500_matche():
    """Avec cape 8000c, un décret dont le contingent sport est en
    position 3500 (au-delà de l'ancienne cape 3000) matche."""
    body = _fake_decret_body_8k(sport_at_position=3500)
    matcher = KeywordMatcher("config/keywords.yml")
    # Cape simulée 8000c (alignement avec _parse_texte_version R42-CY)
    haystack = body[:8000]
    kws, fams = matcher.match(
        "Décret du 12 mai 2026 portant promotion ordre national du Mérite",
        "",
        haystack,
    )
    assert "acteur" in fams, (
        f"Avec haystack 8000c, le ministère des sports en position 3500 "
        f"doit matcher. Obtenu : {kws=} {fams=}"
    )


def test_decret_avec_sport_a_position_5000_matche():
    """Position 5000 (très tardive) — toujours dans la cape 8000c."""
    body = _fake_decret_body_8k(sport_at_position=5000)
    matcher = KeywordMatcher("config/keywords.yml")
    haystack = body[:8000]
    kws, fams = matcher.match(
        "Décret du 12 mai 2026 portant promotion ordre national du Mérite",
        "",
        haystack,
    )
    assert "acteur" in fams


def test_ancien_cap_3000_aurait_rate_position_3500():
    """Non-régression inversée : avec l'ancien cap 3000 chars, le keyword
    en position 3500 ne matchait PAS (preuve que R42-CY est nécessaire)."""
    body = _fake_decret_body_8k(sport_at_position=3500)
    matcher = KeywordMatcher("config/keywords.yml")
    haystack_old = body[:3000]
    kws, fams = matcher.match(
        "Décret du 12 mai 2026 portant promotion ordre national du Mérite",
        "",
        haystack_old,
    )
    # Pas de match acteur → le décret aurait été filtré par R41-I
    assert "acteur" not in fams, (
        "Test de cohérence : avec cape 3000c, la position 3500 reste "
        "invisible. Si ce test échoue, c'est que le body de test a "
        "remonté un keyword sport plus tôt par accident."
    )
