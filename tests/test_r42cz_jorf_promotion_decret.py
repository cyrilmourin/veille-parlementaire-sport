"""R42-CZ (2026-05-16) — Cape JORF uniforme 8000c → 300 000c, fenêtre
nominale 15j → 48h (~4 éditions DILA).

Cyril 2026-05-15-16 :
1. Sur JORFTEXT000054103548 (« Décret du 15 mai 2026 portant promotion
   et nomination dans l'ordre national du Mérite »), le « Ministère des
   sports » apparaît à la position ~208 000c (26× la cape R42-CY=8000c).
2. Approche simple choisie : pas de détection conditionnelle par titre.
   Cape uniforme 300 000c (équivalent CR plénier AN), compensée par une
   fenêtre d'ingestion JORF réduite à 48h.
"""
from __future__ import annotations

from src.keywords import KeywordMatcher


def _build_long_decret(sport_at_position: int = 208_000) -> tuple[str, str]:
    """Reproduit la structure d'un décret ONM : titre + corps géant avec
    le ministère des sports en position cible."""
    title = (
        "Décret du 15 mai 2026 portant promotion et nomination dans "
        "l'ordre national du Mérite"
    )
    preamble = (
        "Par décret du Président de la République en date du 15 mai 2026, "
        "pris sur le rapport du Premier ministre et des ministres et visé "
        "pour son exécution par le chancelier de l'ordre national du Mérite. "
    )
    filler_unit = (
        "Au titre du ministère de l'intérieur. Au grade de chevalier. "
        "M. X, commissaire de police, 25 ans de services. "
        "Mme Y, commandante de police, 18 ans de services. "
        "M. Z, brigadier-chef, 21 ans de services. "
    )
    body = preamble
    while len(body) < sport_at_position:
        body += filler_unit
    body = body[:sport_at_position]
    body += (
        " Ministère des sports, de la jeunesse et de la vie associative. "
        "Au grade de chevalier. "
        "M. Mourin (Cyril, Pierre, Joël), fondateur d'une agence de "
        "conseil spécialisée dans le sport ; 18 ans de services. "
    )
    return title, body


def test_decret_promotion_position_208k_matche_avec_cape_300k():
    """Avec cape 300 000c (R42-CZ), un décret ONM dont le contingent
    sport est à 208 000c matche bien."""
    title, body = _build_long_decret(sport_at_position=208_000)
    haystack = body[:300_000]
    matcher = KeywordMatcher("config/keywords.yml")
    kws, fams = matcher.match(title, "", haystack)
    assert "acteur" in fams, (
        f"À position 208k avec cape 300k, le sport doit matcher. "
        f"kws={kws[:5]} fams={fams}"
    )


def test_decret_promotion_inverse_cape_8k_ratait():
    """Cohérence : avec l'ancienne cape 8000c (R42-CY), le sport à 208k
    était invisible — c'est pourquoi R42-CZ est nécessaire."""
    title, body = _build_long_decret(sport_at_position=208_000)
    haystack_old = body[:8000]
    matcher = KeywordMatcher("config/keywords.yml")
    kws, fams = matcher.match(title, "", haystack_old)
    assert "acteur" not in fams, (
        "Test de cohérence : avec cape 8000c, position 208k invisible."
    )


def test_dila_jorf_parse_texte_version_cape_300k(tmp_path):
    """Test d'intégration : `_parse_texte_version` retourne un body_head
    jusqu'à 300 000c, sans plus dépendre du titre."""
    from src.sources.dila_jorf import _parse_texte_version
    # XML stub avec un corps de 250k chars
    body_text = "Au titre du ministère des sports. " * 8000  # ~270 000c
    xml = (
        f"<TEXTE_VERSION>"
        f"<META><META_COMMUN>"
        f"<ID>JORFTEXT000054103548</ID>"
        f"<NATURE>DECRET</NATURE>"
        f"</META_COMMUN></META>"
        f"<META_SPEC><META_TEXTE_VERSION>"
        f"<TITREFULL>Décret du 15 mai 2026 portant promotion et "
        f"nomination dans l'ordre national du Mérite</TITREFULL>"
        f"<DATE_PUBLI>2026-05-15</DATE_PUBLI>"
        f"<DATE_SIGNATURE>2026-05-15</DATE_SIGNATURE>"
        f"</META_TEXTE_VERSION></META_SPEC>"
        f"<TEXTE>{body_text}</TEXTE>"
        f"</TEXTE_VERSION>"
    ).encode("utf-8")
    info = _parse_texte_version(xml)
    assert info is not None
    # Le body_head est désormais jusqu'à 300 000c (au lieu de 8000c)
    assert len(info["body_head"]) > 200_000, (
        f"Avec R42-CZ, body_head doit dépasser 200k chars pour un décret "
        f"long. Obtenu : {len(info['body_head'])} chars."
    )


def test_dila_jorf_fenetre_48h_nominal(monkeypatch):
    """En mode nominal, JORF utilise une fenêtre 48h ≈ 4 éditions DILA."""
    # Force le mode nominal explicitement
    monkeypatch.setenv("RUN_MODE", "nominal")
    from src.run_mode import window_days, is_full_mode
    assert is_full_mode() is False
    days_nominal = window_days(nominal=4, full=60)
    assert days_nominal == 4, (
        f"Fenêtre nominale JORF doit être 4 éditions (~48h). "
        f"Obtenu : {days_nominal}"
    )
