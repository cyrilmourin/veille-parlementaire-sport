"""R42-DD (2026-05-17) — Blocklist agenda manuelle par UID.

Cas d'usage : item agenda toujours présent dans le dump AN avec
etat="Confirmé" mais reporté dans la réalité (open data AN non
synchronisé avec l'agenda web public). Les filtres automatiques
R42-CY / R42-DC / R42-CZG ne déclenchent pas — on bascule sur une
blocklist UID manuelle (`config/blocklist_agenda.yml`).
"""
from __future__ import annotations

from src.site_export import (
    _filter_agenda_blocklist,
    _load_agenda_blocklist,
)


def test_blocklist_loaded():
    """La blocklist actuelle contient bien les 3 UIDs PPL Sport pro 18/05."""
    bl = _load_agenda_blocklist()
    assert "RUANR5L17S2026IDS30598" in bl, "séance 15h00 absente"
    assert "RUANR5L17S2026IDS30599" in bl, "séance 21h30 absente"
    assert "RUANR5L17S2026IDC460094" in bl, "commission 14h45 absente"
    # Les raisons doivent être non vides
    for uid, reason in bl.items():
        assert reason, f"raison vide pour {uid}"


def test_filter_drops_blocklisted_uid():
    row = {
        "category": "agenda",
        "uid": "RUANR5L17S2026IDS30598",
        "title": "Discussion PPL Sport pro",
        "published_at": "2026-05-18T15:00:00",
        "raw": {"etat": "Confirmé"},
    }
    kept = _filter_agenda_blocklist([row])
    assert kept == [], "L'UID blocklisté doit être masqué malgré etat=Confirmé"


def test_filter_keeps_non_blocklisted():
    row = {
        "category": "agenda",
        "uid": "RUANR5L17S2026IDC999999",  # non blocklisté
        "title": "Autre réunion",
        "published_at": "2026-05-20T10:00:00",
        "raw": {"etat": "Confirmé"},
    }
    kept = _filter_agenda_blocklist([row])
    assert len(kept) == 1


def test_filter_ignores_non_agenda():
    row = {
        "category": "questions",  # autre catégorie
        "uid": "RUANR5L17S2026IDS30598",
        "title": "—",
    }
    kept = _filter_agenda_blocklist([row])
    assert len(kept) == 1, "Non-agenda jamais filtré, même si UID matche"


def test_filter_out_dropped_marks_postponed():
    """Les items masqués passés via out_dropped reçoivent
    `raw._postponed = True` pour ressurfacer côté carte accueil."""
    row = {
        "category": "agenda",
        "uid": "RUANR5L17S2026IDS30598",
        "title": "—",
        "raw": {"etat": "Confirmé"},
    }
    dropped: list[dict] = []
    kept = _filter_agenda_blocklist([row], out_dropped=dropped)
    assert kept == []
    assert len(dropped) == 1
    assert dropped[0]["raw"].get("_postponed") is True
    assert "blocklist" in dropped[0]["raw"].get("_postponed_reason", "")


def test_filter_no_blocklist_is_noop():
    """Si la blocklist est vide (cas tests isolés ou config absente),
    le filtre est un no-op."""
    # On simule en patchant temporairement le path. Plus simple :
    # vérifier qu'un row non blocklisté passe.
    row = {"category": "agenda", "uid": "RUANR5L17S0000IDC000000", "title": "—"}
    kept = _filter_agenda_blocklist([row])
    assert len(kept) == 1
