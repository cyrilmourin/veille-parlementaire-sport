"""R42-DC (2026-05-17) — Badge « REP » carte accueil PPL Sport pro.

Cyril 2026-05-17 : « la date du 18 mai apparaît toujours pour la séance
plénière de la PPL Sport pro [...] pour le module spécial de la page
d'accueil remplacé manuellement 18/05 par REP (chacun comprendra que
ça veut dire reporté) [...] désormais un événement qui n'est plus
présent dans l'agenda futur doit être mis en invisibilité jusqu'à ce
qu'à nouvelle date apparaisse ».

Couvre :
- `collect_special_ppl(rows, postponed_agenda=...)` réinjecte les items
  agenda déjà masqués dans le bucket `agenda` (visibles UNIQUEMENT pour
  la carte accueil PPL — Hugo ne les voit pas dans /items/agenda/).
- `_row_to_payload(r)` expose `is_postponed: bool` et
  `postponed_reason: str`.
- `_render_special_ppl_card(payload)` substitue un badge « REP » à
  la date du badge `date-pill` quand `is_postponed`.
- `_mark_postponed(row, reason)` est idempotent.
"""
from __future__ import annotations

from src.site_export import _mark_postponed, _render_special_ppl_card
from src.special_ppl import (
    AN_TEXTE_REF, _row_to_payload, build_payload, collect_special_ppl,
)


# ---------------------------------------------------------------------------
# _mark_postponed
# ---------------------------------------------------------------------------

def test_mark_postponed_sets_flags():
    row = {"raw": {"organe": "PO838901"}}
    _mark_postponed(row, reason="stale")
    assert row["raw"]["_postponed"] is True
    assert row["raw"]["_postponed_reason"] == "stale"


def test_mark_postponed_idempotent():
    row = {"raw": {"organe": "PO838901"}}
    _mark_postponed(row, reason="stale")
    _mark_postponed(row, reason="etat:eventuel")
    # Dernière valeur gagne
    assert row["raw"]["_postponed_reason"] == "etat:eventuel"
    assert row["raw"]["_postponed"] is True


def test_mark_postponed_creates_raw_if_missing():
    row = {}  # pas de raw
    _mark_postponed(row, reason="stale")
    assert row["raw"]["_postponed"] is True


def test_mark_postponed_handles_non_dict_raw():
    row = {"raw": "not a dict"}
    _mark_postponed(row, reason="stale")
    assert isinstance(row["raw"], dict)
    assert row["raw"]["_postponed"] is True


# ---------------------------------------------------------------------------
# collect_special_ppl avec postponed_agenda
# ---------------------------------------------------------------------------

def _ppl_agenda_row(date="2026-05-18T14:45:00", title="Discussion PPL Sport pro"):
    """Helper : un row agenda PPL Sport pro identifiable."""
    return {
        "source_id": "an_agenda",
        "category": "agenda",
        "title": title,
        "url": "https://an.fr/RUANR5L17S2026IDC460094",
        "chamber": "AN",
        "published_at": date,
        "raw": {"texte_ref": AN_TEXTE_REF, "organe": "PO838901"},
    }


def test_collect_special_ppl_reinjects_postponed():
    """postponed_agenda items sont ajoutés au bucket `agenda`."""
    rows = []  # rien dans rows
    postponed = [_ppl_agenda_row()]
    _mark_postponed(postponed[0], reason="stale")
    buckets = collect_special_ppl(rows, postponed_agenda=postponed)
    assert len(buckets["agenda"]) == 1
    assert buckets["agenda"][0]["raw"]["_postponed"] is True


def test_collect_special_ppl_ignores_non_ppl_postponed():
    """postponed_agenda items qui ne matchent pas la PPL → ignorés."""
    rows = []
    postponed = [
        {
            "source_id": "an_agenda",
            "category": "agenda",
            "title": "Audition autre sujet",
            "url": "https://an.fr/x",
            "published_at": "2026-05-18T14:00:00",
            "raw": {"organe": "PO123456"},  # pas la PPL
        },
    ]
    buckets = collect_special_ppl(rows, postponed_agenda=postponed)
    assert buckets["agenda"] == []


def test_collect_special_ppl_postponed_default_empty():
    """`postponed_agenda=None` (defaut) → comportement legacy."""
    rows = []
    buckets = collect_special_ppl(rows)
    assert buckets["agenda"] == []


# ---------------------------------------------------------------------------
# _row_to_payload — flag is_postponed
# ---------------------------------------------------------------------------

def test_row_to_payload_exposes_is_postponed_true():
    row = _ppl_agenda_row()
    _mark_postponed(row, reason="etat:eventuel")
    payload = _row_to_payload(row)
    assert payload["is_postponed"] is True
    assert payload["postponed_reason"] == "etat:eventuel"


def test_row_to_payload_exposes_is_postponed_false_when_absent():
    row = _ppl_agenda_row()
    payload = _row_to_payload(row)
    assert payload["is_postponed"] is False
    assert payload["postponed_reason"] == ""


# ---------------------------------------------------------------------------
# _render_special_ppl_card — badge REP
# ---------------------------------------------------------------------------

def _make_card_payload(next_event_postponed: bool) -> dict:
    """Helper : construit un payload minimal pour la carte accueil
    avec un seul agenda futur dont on contrôle `is_postponed`."""
    return {
        "meta": {
            "key": "ppl-sport-professionnel",
            "title": "Spécial PPL Sport professionnel",
            "slug_path": "/ppl-sport-professionnel/",
            "url_an_texte": "https://an.fr/textes/1560",
        },
        "counts": {"agenda": 1, "amdt_commission": 0, "amdt_seance": 0},
        "agenda": [
            {
                "title": "Discussion PPL Sport pro",
                "date": "9999-12-31",  # futur lointain pour passer le filtre upcoming
                "meeting_kind": "Séance publique",
                "is_postponed": next_event_postponed,
                "url": "/items/agenda/",
                "chamber": "AN",
            },
        ],
    }


def test_render_card_shows_rep_badge_when_postponed():
    payload = _make_card_payload(next_event_postponed=True)
    html = "\n".join(_render_special_ppl_card(payload))
    assert "date-pill--postponed" in html, (
        "Badge REP attendu (classe date-pill--postponed)"
    )
    assert ">REP<" in html, "Le badge doit contenir le texte « REP »"
    # Vérification : pas de date affichée (31/12 ne doit pas apparaître)
    assert "31/12" not in html


def test_render_card_shows_date_when_not_postponed():
    payload = _make_card_payload(next_event_postponed=False)
    html = "\n".join(_render_special_ppl_card(payload))
    assert "date-pill--postponed" not in html
    assert "31/12" in html, "Date 31/12 doit apparaître normalement"


def test_build_payload_preserves_is_postponed_through_agenda_bucket():
    """build_payload sérialise les items agenda en payloads — le
    flag `is_postponed` doit traverser intact pour atteindre la carte."""
    row = _ppl_agenda_row()
    _mark_postponed(row, reason="stale")
    buckets = {
        "dosleg": [], "agenda": [row],
        "amdt_commission": [], "amdt_seance": [],
        "comptes_rendus": [], "communiques": [], "questions": [],
    }
    payload = build_payload(buckets)
    assert payload["agenda"][0]["is_postponed"] is True
    assert payload["agenda"][0]["postponed_reason"] == "stale"
