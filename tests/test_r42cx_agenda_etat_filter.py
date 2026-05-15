"""R42-CX (2026-05-15) — Filtrage des réunions reportées/annulées.

Cyril 2026-05-15 : « Le problème structurel de maintien des dates
pourtant reportées de l'agenda parlementaire n'est pas réglé puisque
la date du 18 mai apparaît encore pour la séance publique de la PPL
sport pro ».

R42-CI (déployé ce matin) règle le cas où AN met `timeStampDebut=NULL`
au moment du report. R42-CX règle le cas symétrique : AN expose un
champ statut (`etat`/`confirmation`/`statutReunion`) avec « Reportée »
ou « Annulée ». Le parser AN extrait ce statut (best-effort,
`raw.etat`) ; deux consommateurs filtrent désormais :
  1. `_boost_dosleg_with_agenda` (site_export.py) — n'utilise PAS l'item
     pour booster `published_at` d'un dossier législatif.
  2. `collect_special_ppl` / `collect_special_equipements` — l'item
     ne remonte pas dans le bucket agenda de la page PPL spéciale.
"""
from __future__ import annotations

from src.special_ppl import AN_TEXTE_REF, collect_special_ppl


def _agenda_row(etat="", date="2026-05-18T15:00:00"):
    return {
        "title": "Discussion de la PPL relative à l'organisation, "
                 "à la gestion et au financement du sport professionnel",
        "url": "https://www.assemblee-nationale.fr/dyn/17/organes/PO838901",
        "category": "agenda",
        "chamber": "AN",
        "published_at": date,
        "raw": {
            "texte_ref": AN_TEXTE_REF,
            "etat": etat,
        },
    }


def test_agenda_reporte_filtre_du_bucket_special_ppl():
    """Une réunion `etat='Reportée'` n'apparaît pas dans le bucket
    agenda de la page PPL spéciale."""
    rows = [_agenda_row(etat="Reportée")]
    out = collect_special_ppl(rows)
    assert len(out["agenda"]) == 0


def test_agenda_annule_filtre_du_bucket_special_ppl():
    rows = [_agenda_row(etat="Annulée")]
    out = collect_special_ppl(rows)
    assert len(out["agenda"]) == 0


def test_agenda_confirme_garde_dans_bucket_special_ppl():
    """Une réunion confirmée OU sans champ etat reste dans le bucket
    (rétrocompat — la majorité des items legacy n'ont pas de etat)."""
    rows = [_agenda_row(etat="Confirmée")]
    out = collect_special_ppl(rows)
    assert len(out["agenda"]) == 1


def test_agenda_sans_etat_garde_dans_bucket():
    """Pas de etat → pas de filtre. Comportement legacy préservé."""
    rows = [_agenda_row(etat="")]
    out = collect_special_ppl(rows)
    assert len(out["agenda"]) == 1


def test_agenda_etat_match_partiel_report():
    """`etat='Séance reportée à une date ultérieure'` matche bien
    via le substring `report`."""
    rows = [_agenda_row(etat="Séance reportée à une date ultérieure")]
    out = collect_special_ppl(rows)
    assert len(out["agenda"]) == 0


def test_boost_dosleg_skip_agenda_reporte():
    """`_boost_dosleg_with_agenda` ne booste PAS un dosleg via une réunion
    reportée — la date n'est plus opérationnelle."""
    from src.site_export import _boost_dosleg_with_agenda
    rows = [
        # Dosleg PPL Sport pro (date initiale 18 mars 2025)
        {
            "title": "Proposition de loi relative à l'organisation, à la "
                     "gestion et au financement du sport professionnel",
            "category": "dossiers_legislatifs",
            "chamber": "AN",
            "published_at": "2025-03-18T00:00:00",
            "url": "https://www.assemblee-nationale.fr/dyn/17/textes/l17b1560_proposition-loi",
            "raw": {},
        },
        # Agenda reportée du 18 mai 2026
        _agenda_row(etat="Reportée", date="2026-05-18T15:00:00"),
    ]
    _boost_dosleg_with_agenda(rows)
    # La date initiale est PRÉSERVÉE (pas boostée à 2026-05-18)
    assert rows[0]["published_at"] == "2025-03-18T00:00:00"


def test_boost_dosleg_utilise_agenda_confirme():
    """Sanity : si la réunion N'est PAS reportée, le boost s'applique
    bien (rétrocompat R41-K)."""
    from src.site_export import _boost_dosleg_with_agenda
    rows = [
        {
            "title": "Proposition de loi relative à l'organisation, à la "
                     "gestion et au financement du sport professionnel",
            "category": "dossiers_legislatifs",
            "chamber": "AN",
            "published_at": "2025-03-18T00:00:00",
            "url": "https://www.assemblee-nationale.fr/dyn/17/textes/l17b1560_proposition-loi",
            "raw": {},
        },
        _agenda_row(etat="Confirmée", date="2026-05-12T09:00:00"),
    ]
    _boost_dosleg_with_agenda(rows)
    # La date a été boostée
    assert rows[0]["published_at"].startswith("2026-05-12")
