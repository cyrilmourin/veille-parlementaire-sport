"""R41-H (2026-04-28) — Tests des deux corrections nominations :

1. Faux positif parlementaire : un rapport Sénat (senat_rapports,
   category=communiques, family_source=parlement) qui mentionne
   « réélu président » ne doit PAS être re-routé vers nominations.
   Cas concret : rapport APCE n°446.

2. Sources nominations-only : les sources presse sport business
   (olbia_conseil, cafe_sport_business, sport_buzz_business,
   sport_business_club, sport_strategies) et fédérations
   (fff_actualites, fft_actualites, ffa_actualites) ne doivent
   apparaître dans `communiques` (Publications) que si leurs items
   sont re-routés vers nominations. Ceux qui matchent d'autres
   keywords sans nomination_event sont supprimés des publications.
"""
from __future__ import annotations

import json

from src.site_export import (
    _filter_nominations_only_sources,
    _reroute_to_nominations,
    _NOMINATIONS_ONLY_SOURCES,
)


def _make_row(
    source_id: str,
    category: str = "communiques",
    chamber: str | None = None,
    families: list[str] | None = None,
    title: str = "Test",
) -> dict:
    return {
        "source_id": source_id,
        "category": category,
        "chamber": chamber or "",
        "keyword_families": json.dumps(families or []),
        "title": title,
        "uid": f"{source_id}::test",
        "url": "http://example.com",
        "published_at": "2026-04-19T00:00:00",
        "summary": "",
        "raw": "{}",
    }


# ---------------------------------------------------------------------------
# 1. Guard R41-H : rapports parlementaires exemptés du reroute
# ---------------------------------------------------------------------------


def test_senat_rapport_avec_nomination_event_pas_reroute():
    """Rapport APCE Sénat (senat_rapports) + nomination_event
    → NE doit PAS être re-routé vers nominations."""
    rows = [
        _make_row(
            "senat_rapports",
            families=["nomination_event"],
            title="Rapport n°446 — travaux délégation APCE ... réélu président",
        )
    ]
    out = _reroute_to_nominations(rows)
    assert out[0]["category"] == "communiques", (
        "senat_rapports (family_source=parlement) ne doit pas être re-routé "
        "vers nominations même avec nomination_event (R41-H)"
    )


def test_an_rapport_avec_nomination_event_pas_reroute():
    """Rapport AN (an_rapports) + nomination_event → non re-routé."""
    rows = [
        _make_row(
            "an_rapports",
            families=["nomination_event"],
            title="Rapport AN sur la gouvernance sportive — DG reconduit",
        )
    ]
    out = _reroute_to_nominations(rows)
    assert out[0]["category"] == "communiques"


def test_cnosf_avec_nomination_event_est_reroute():
    """CNOSF (family_source=mouvement_sportif) + nomination_event
    → DOIT être re-routé (comportement normal, pas parlementaire)."""
    rows = [
        _make_row(
            "cnosf",
            families=["nomination_event"],
            title="David Lappartient réélu président du CIO",
        )
    ]
    out = _reroute_to_nominations(rows)
    assert out[0]["category"] == "nominations"


def test_min_sports_avec_nomination_event_est_reroute():
    """Ministère sport (family_source=etat) + nomination_event → re-routé."""
    rows = [
        _make_row(
            "min_sports_actualites",
            families=["nomination_event"],
            title="Nomination : X devient DTN de la FFN",
        )
    ]
    out = _reroute_to_nominations(rows)
    assert out[0]["category"] == "nominations"


def test_olbia_avec_nomination_event_est_reroute():
    """Source presse sport business + nomination_event → re-routé."""
    rows = [
        _make_row(
            "olbia_conseil",
            families=["nomination_event"],
            title="Eric Woerth nommé président du PMU",
        )
    ]
    out = _reroute_to_nominations(rows)
    assert out[0]["category"] == "nominations"


# ---------------------------------------------------------------------------
# 2. _filter_nominations_only_sources
# ---------------------------------------------------------------------------


def test_nominations_only_source_sans_reroute_supprime():
    """Item sport_buzz_business en communiques (pas rerouted)
    → supprimé des publications."""
    rows = [
        _make_row(
            "sport_buzz_business",
            category="communiques",
            families=["dispositif"],  # pas nomination_event → pas rerouted
            title="Nouveau stade de 50M€ pour le club de rugby",
        )
    ]
    out = _filter_nominations_only_sources(rows)
    assert out == [], (
        "sport_buzz_business en communiques (non re-routé) "
        "doit être supprimé des publications (R41-H)"
    )


def test_nominations_only_source_reroute_conserve():
    """Item olbia_conseil déjà re-routé en nominations
    → conservé (filtre ne touche que communiques)."""
    rows = [
        _make_row(
            "olbia_conseil",
            category="nominations",  # déjà re-routé
            title="X devient Y de Z",
        )
    ]
    out = _filter_nominations_only_sources(rows)
    assert len(out) == 1
    assert out[0]["category"] == "nominations"


def test_cnosf_communiques_conserve():
    """CNOSF n'est PAS dans _NOMINATIONS_ONLY_SOURCES →
    ses items communiques restent dans publications."""
    assert "cnosf" not in _NOMINATIONS_ONLY_SOURCES
    rows = [
        _make_row("cnosf", category="communiques",
                  title="CNOSF — réunion du conseil d'administration")
    ]
    out = _filter_nominations_only_sources(rows)
    assert len(out) == 1


def test_france_paralympique_communiques_conserve():
    """france_paralympique n'est pas nominations-only → conservé."""
    assert "france_paralympique" not in _NOMINATIONS_ONLY_SOURCES
    rows = [
        _make_row("france_paralympique", category="communiques",
                  title="Comité Paralympique — rapport annuel 2025")
    ]
    out = _filter_nominations_only_sources(rows)
    assert len(out) == 1


def test_toutes_sources_nominations_only_couvertes():
    """Vérifie que les 8 sources attendues sont dans le set."""
    expected = {
        "olbia_conseil",
        "cafe_sport_business",
        "sport_buzz_business",
        "sport_business_club",
        "sport_strategies",
        "fff_actualites",
        "fft_actualites",
        "ffa_actualites",
    }
    assert expected <= _NOMINATIONS_ONLY_SOURCES


def test_fff_communiques_non_nomination_supprime():
    """FFF actualité sport (pas nomination) → ne doit pas apparaître
    dans publications."""
    rows = [
        _make_row(
            "fff_actualites",
            category="communiques",
            families=["federation"],
            title="FFF — Résultats de l'équipe de France féminine",
        )
    ]
    out = _filter_nominations_only_sources(rows)
    assert out == []


def test_pipeline_integration_senat_rapport_faux_positif(monkeypatch):
    """Intégration bout-en-bout : le rapport APCE Sénat ne doit PAS
    apparaître en nominations après reroute + filter."""
    rapport = _make_row(
        "senat_rapports",
        families=["nomination_event"],
        title="Rapport n°446 — Délégation APCE ... réélu président",
    )
    rows = _reroute_to_nominations([rapport])
    # Doit encore être en communiques (guard parlement)
    assert rows[0]["category"] == "communiques"
    # Le filter_nominations_only ne le supprime pas (senat_rapports
    # n'est pas dans _NOMINATIONS_ONLY_SOURCES)
    rows2 = _filter_nominations_only_sources(rows)
    assert len(rows2) == 1
    assert rows2[0]["category"] == "communiques"


def test_pipeline_integration_presse_nomination_reroute_puis_conserve():
    """Intégration : item sport_strategies + nomination_event →
    re-routé → conservé par le filtre nominations-only."""
    item = _make_row(
        "sport_strategies",
        families=["nomination_event"],
        title="Cyril Linette reconduit à la tête de Roland-Garros",
    )
    rows = _reroute_to_nominations([item])
    assert rows[0]["category"] == "nominations"
    rows2 = _filter_nominations_only_sources(rows)
    assert len(rows2) == 1
    assert rows2[0]["category"] == "nominations"
