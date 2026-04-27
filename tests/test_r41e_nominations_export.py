"""R41-E (2026-04-27) — Intégration export : titre normalisé + dédup
nominations.

Cyril : pour les sources presse business, homogénéiser le titre en
« X devient Y de Z » + masquer l'URL externe + dédup inter-sources.
Pour les sources officielles (JORF, ministères, fédérations…) :
préserver titre original + URL.
"""
from __future__ import annotations

from src.site_export import _normalize_and_dedup_nominations


def _row(*, source_id, category="nominations", title="",
         summary="", url="example.test/x", published_at="2026-04-20T00:00:00"):
    return {
        "source_id": source_id,
        "category": category,
        "title": title,
        "summary": summary,
        "url": url,
        "published_at": published_at,
        "matched_keywords": ["nommé président"],
        "keyword_families": ["nomination_event"],
    }


# ---------------------------------------------------------------------------
# 1. Comportement par type de source
# ---------------------------------------------------------------------------


def test_source_presse_normalise_titre_et_masque_url():
    """Olbia + accroche presse → titre canonique + URL masquée."""
    rows = [_row(
        source_id="olbia_conseil",
        title="Cette semaine, Olbia a appris que…",
        summary="Eric Woerth a été nommé président du PMU.",
        url="https://www.olbia-conseil.com/2026/04/27/cette-semaine-x/",
    )]
    out = _normalize_and_dedup_nominations(rows)
    assert len(out) == 1
    assert out[0]["title"] == "Eric Woerth devient président du PMU"
    assert out[0]["url"] == ""  # URL masquée


def test_source_officielle_preserve_titre_et_url():
    """JORF (dila_jorf) → titre + URL d'origine inchangés."""
    rows = [_row(
        source_id="dila_jorf",
        title="Décret du 24 avril 2026 portant nomination de M. Eric Woerth",
        summary="Eric Woerth a été nommé président du PMU.",
        url="https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000049XXX",
    )]
    out = _normalize_and_dedup_nominations(rows)
    assert len(out) == 1
    # Titre original préservé
    assert out[0]["title"].startswith("Décret du 24 avril 2026")
    # URL préservée
    assert out[0]["url"].startswith("https://www.legifrance.gouv.fr/")


def test_source_federation_preserve_titre_et_url():
    """fff_actualites (officielle) → préserve."""
    rows = [_row(
        source_id="fff_actualites",
        title="Camille Emié, fraîchement nommée directrice de la communication",
        summary="…",
        url="https://www.fff.fr/articles/2026/04/15/camille-emie-…",
    )]
    out = _normalize_and_dedup_nominations(rows)
    assert "Camille Emié" in out[0]["title"]
    assert out[0]["url"].startswith("https://www.fff.fr/")


# ---------------------------------------------------------------------------
# 2. Dédup inter-sources
# ---------------------------------------------------------------------------


def test_dedup_meme_nomination_3_sources_presse():
    """Même fact (Woerth président PMU) relayé par 3 sources presse →
    1 seul item conservé après dédup."""
    rows = [
        _row(source_id="olbia_conseil",
             summary="Eric Woerth a été nommé président du PMU.",
             published_at="2026-04-25T00:00:00"),
        _row(source_id="cafe_sport_business",
             summary="Eric Woerth a été nommé président du PMU.",
             published_at="2026-04-26T00:00:00"),
        _row(source_id="sport_strategies",
             summary="Eric Woerth a été nommé président du PMU.",
             published_at="2026-04-24T00:00:00"),
    ]
    out = _normalize_and_dedup_nominations(rows)
    nominations = [r for r in out if r.get("category") == "nominations"]
    assert len(nominations) == 1
    # Le plus récent (Café 2026-04-26) doit être gagnant
    assert nominations[0]["source_id"] == "cafe_sport_business"


def test_dedup_priorise_source_officielle():
    """Si une source officielle est dans le groupe, elle gagne (URL
    JORF préservée pour vérification)."""
    rows = [
        _row(source_id="olbia_conseil",
             summary="Eric Woerth a été nommé président du PMU.",
             published_at="2026-04-26T00:00:00"),
        _row(source_id="dila_jorf",
             title="Décret JORF nomination Eric Woerth",
             summary="Eric Woerth a été nommé président du PMU.",
             url="https://legifrance.gouv.fr/jorf/...",
             published_at="2026-04-25T00:00:00"),  # plus ancienne
    ]
    out = _normalize_and_dedup_nominations(rows)
    nominations = [r for r in out if r.get("category") == "nominations"]
    assert len(nominations) == 1
    # JORF gagne malgré sa date plus ancienne (priorité officielle)
    assert nominations[0]["source_id"] == "dila_jorf"
    assert nominations[0]["url"].startswith("https://legifrance")


def test_dedup_pas_de_collision_entre_personnes_differentes():
    """Régression : 2 nominations distinctes ne doivent pas être
    dédupliquées même si elles partagent un mot (ex. même fonction)."""
    rows = [
        _row(source_id="olbia_conseil",
             summary="Eric Woerth a été nommé président du PMU."),
        _row(source_id="cafe_sport_business",
             summary="Tony Estanguet devient président de la Fondation FDJ Sport."),
    ]
    out = _normalize_and_dedup_nominations(rows)
    nominations = [r for r in out if r.get("category") == "nominations"]
    assert len(nominations) == 2


def test_extraction_echec_no_dedup():
    """Items sans extraction réussie (pas de match nomination) restent
    dans le résultat sans dédup ni normalisation."""
    rows = [
        _row(source_id="olbia_conseil",
             title="Édito de la semaine",
             summary="Réflexions sur le sport business..."),
        _row(source_id="olbia_conseil",
             title="Autre édito",
             summary="..."),
    ]
    out = _normalize_and_dedup_nominations(rows)
    nominations = [r for r in out if r.get("category") == "nominations"]
    # Pas de dédup car pas d'extraction réussie
    assert len(nominations) == 2


# ---------------------------------------------------------------------------
# 3. Items hors catégorie nominations : no-op
# ---------------------------------------------------------------------------


def test_no_op_sur_communiques():
    """Items en catégorie `communiques` (pas reroutés) ne sont pas
    touchés par la normalisation."""
    rows = [_row(
        source_id="olbia_conseil",
        category="communiques",
        title="Sport business news",
        summary="Eric Woerth a été nommé président du PMU.",
        url="https://example.test/x",
    )]
    out = _normalize_and_dedup_nominations(rows)
    assert len(out) == 1
    # Titre et URL inchangés (pas dans nominations)
    assert out[0]["title"] == "Sport business news"
    assert out[0]["url"] == "https://example.test/x"


# ---------------------------------------------------------------------------
# 4. Idempotence
# ---------------------------------------------------------------------------


def test_idempotent_double_passage():
    """Un 2e passage donne le même résultat (item normalisé reste
    normalisé)."""
    rows = [_row(
        source_id="olbia_conseil",
        summary="Eric Woerth a été nommé président du PMU.",
    )]
    once = _normalize_and_dedup_nominations(rows)
    twice = _normalize_and_dedup_nominations(once)
    assert len(once) == len(twice) == 1
    assert once[0]["title"] == twice[0]["title"]
    assert once[0]["url"] == twice[0]["url"]


def test_robust_aux_champs_manquants():
    """Robustesse : rows sans summary ou sans title."""
    rows = [
        {"source_id": "olbia_conseil", "category": "nominations"},
        {"category": "nominations", "title": "x"},
        {"category": "nominations"},
    ]
    out = _normalize_and_dedup_nominations(rows)
    # Tous conservés (pas d'extraction donc pas de dédup)
    assert len(out) == 3
