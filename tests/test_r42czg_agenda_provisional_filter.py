"""R42-CZG (2026-05-16) — Filtre agenda items non confirmés.

Cyril 2026-05-16 : « je vois encore la séance plénière le 18 mai alors
qu'elle n'est pas à l'agenda ». Diagnostic : la séance
RUANR5L17S2026IDC460094 EST encore dans Agenda.json.zip avec
`cycleDeVie.etat = "Eventuel"` (convocation prévisionnelle non
confirmée). R42-CY (last_seen_at) ne la masque pas car AN la republie.

Le filtre R42-CZG drop les agendas `raw.etat ∈ {Eventuel, Reportée,
Annulée}` (matching tolérant : "report", "annul", "eventuel").
"""
from __future__ import annotations

from src.site_export import _filter_provisional_agenda_items


def _row(category="agenda", etat="", **extra):
    r = {
        "category": category,
        "title": "test",
        "url": "https://x",
        "published_at": "2026-05-18T14:45:00",
        "raw": {"etat": etat},
    }
    r.update(extra)
    return r


def test_drops_eventuel():
    rows = [_row(etat="Eventuel")]
    kept = _filter_provisional_agenda_items(rows)
    assert kept == [], "etat=Eventuel doit être masqué"


def test_drops_reportee():
    rows = [_row(etat="Reportée")]
    kept = _filter_provisional_agenda_items(rows)
    assert kept == []


def test_drops_annulee():
    rows = [_row(etat="Annulée")]
    kept = _filter_provisional_agenda_items(rows)
    assert kept == []


def test_keeps_confirmee():
    rows = [_row(etat="Confirmée")]
    kept = _filter_provisional_agenda_items(rows)
    assert len(kept) == 1


def test_keeps_empty_etat():
    """État vide / absent → conservé (legacy / source AN sans champ etat)."""
    rows = [_row(etat="")]
    kept = _filter_provisional_agenda_items(rows)
    assert len(kept) == 1


def test_ignores_non_agenda():
    """Items non-agenda ne sont jamais touchés, même avec etat=Eventuel."""
    rows = [
        _row(category="dossiers_legislatifs", etat="Eventuel"),
        _row(category="questions", etat="Annulée"),
    ]
    kept = _filter_provisional_agenda_items(rows)
    assert len(kept) == 2


def test_case_insensitive():
    rows = [
        _row(etat="EVENTUEL"),
        _row(etat="REPORTÉE"),
        _row(etat="annulee"),
    ]
    kept = _filter_provisional_agenda_items(rows)
    assert kept == []


def test_real_an_18_mai_2026_case():
    """Cas concret PPL Sport pro 18/05/2026 — RUANR5L17S2026IDC460094 :
    cycleDeVie.etat = "Eventuel". Cet item doit être filtré.
    """
    row = {
        "category": "agenda",
        "title": "Examen art. 88 amendements PPL Sport pro",
        "url": "https://an.fr/RUANR5L17S2026IDC460094",
        "published_at": "2026-05-18T14:45:00",
        "raw": {
            "etat": "Eventuel",
            "xsi_type": "reunioncommission_type",
            "organe": "PO419604",
        },
    }
    kept = _filter_provisional_agenda_items([row])
    assert kept == [], (
        "La séance 18/05/2026 'Eventuel' doit disparaître du site"
    )
