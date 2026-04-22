"""Tests du connecteur `data_gouv_agenda` (R15, 2026-04-22).

Consume un endpoint JSON iCal-like (schéma OpenDataSoft) et normalise
en Item. Couvre :
- schéma nominal (uid, summary, dtstart, dtend, description, agenda)
- fenêtre glissante `since_days` (filtre events trop anciens)
- items invalides (uid ou summary manquants) → skippés
- endpoint en erreur (network / JSON invalide) → liste vide, pas de crash
- route via `normalize._dispatch` sur `format=data_gouv_agenda`
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src import normalize  # noqa: E402
from src.sources import data_gouv  # noqa: E402


def _now():
    return datetime.utcnow().replace(microsecond=0)


def _entry(uid: str, summary: str, dtstart: datetime,
           agenda: str = "Ministre Test",
           description: str = "") -> dict:
    return {
        "uid": uid,
        "agenda": agenda,
        "summary": summary,
        "dtstart": dtstart.isoformat(),
        "dtend": (dtstart + timedelta(hours=1)).isoformat(),
        "description": description,
        "dtstamp": _now().isoformat(),
    }


def _make_fetch(payload):
    def _fake(url: str) -> str:
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, str):
            return payload
        return json.dumps(payload)
    return _fake


def test_fetch_agenda_json_normalises_nominal_case(monkeypatch):
    now = _now()
    entries = [
        _entry("42", "Audition comité sport universitaire", now - timedelta(days=1),
               description="Rencontre avec les présidents d'universités"),
        _entry("43", "Conseil des ministres", now + timedelta(days=3)),
    ]
    monkeypatch.setattr(data_gouv, "fetch_text", _make_fetch(entries))
    src = {
        "id": "min_esr_agenda",
        "url": "https://x/y",
        "format": "data_gouv_agenda",
        "chamber": "MinESR",
        "since_days": 0,  # pas de filtre pour garder les 2 events
        "title_prefix": "MinESR —",
    }
    items = data_gouv.fetch_source(src)
    assert len(items) == 2

    # Item 1 : uid hashé, titre préfixé + owner injecté, summary = description
    it = items[0]
    assert it.source_id == "min_esr_agenda"
    assert it.category == "agenda"
    assert it.chamber == "MinESR"
    assert it.title.startswith("MinESR —")
    assert "Audition comité sport universitaire" in it.title
    assert "Ministre Test" in it.title  # owner ajouté (pas dans summary)
    assert it.summary == "Rencontre avec les présidents d'universités"
    assert it.raw["upstream_uid"] == "42"
    assert it.raw["agenda_owner"] == "Ministre Test"
    assert it.raw["path"] == "data_gouv:agenda"
    assert it.published_at is not None


def test_fetch_agenda_json_filters_old_events(monkeypatch):
    now = _now()
    entries = [
        _entry("old", "Vieux event", now - timedelta(days=200)),
        _entry("recent", "Event récent", now - timedelta(days=10)),
        _entry("future", "Event futur", now + timedelta(days=5)),
    ]
    monkeypatch.setattr(data_gouv, "fetch_text", _make_fetch(entries))
    src = {
        "id": "min_esr_agenda",
        "url": "https://x/y",
        "format": "data_gouv_agenda",
        "since_days": 90,
    }
    items = data_gouv.fetch_source(src)
    # "old" > 90j → filtré ; "recent" + "future" → gardés
    assert len(items) == 2
    uids = [it.raw["upstream_uid"] for it in items]
    assert "old" not in uids
    assert "recent" in uids
    assert "future" in uids


def test_fetch_agenda_json_skips_invalid_entries(monkeypatch):
    entries = [
        {"uid": "", "summary": "no uid", "dtstart": _now().isoformat()},
        {"uid": "ok", "summary": "", "dtstart": _now().isoformat()},
        "not a dict",
        _entry("good", "Valide", _now()),
    ]
    monkeypatch.setattr(data_gouv, "fetch_text", _make_fetch(entries))
    src = {"id": "x", "url": "y", "format": "data_gouv_agenda", "since_days": 0}
    items = data_gouv.fetch_source(src)
    assert len(items) == 1
    assert items[0].raw["upstream_uid"] == "good"


def test_fetch_agenda_json_handles_network_error(monkeypatch):
    monkeypatch.setattr(data_gouv, "fetch_text",
                        _make_fetch(TimeoutError("simulated")))
    src = {"id": "x", "url": "y", "format": "data_gouv_agenda"}
    assert data_gouv.fetch_source(src) == []


def test_fetch_agenda_json_handles_invalid_json(monkeypatch):
    monkeypatch.setattr(data_gouv, "fetch_text", _make_fetch("<html>not json</html>"))
    src = {"id": "x", "url": "y", "format": "data_gouv_agenda"}
    assert data_gouv.fetch_source(src) == []


def test_fetch_agenda_json_handles_non_list_schema(monkeypatch):
    # API renvoie un dict au lieu d'une liste (ex. erreur API + enveloppe)
    monkeypatch.setattr(data_gouv, "fetch_text",
                        _make_fetch({"error": "not found"}))
    src = {"id": "x", "url": "y", "format": "data_gouv_agenda"}
    assert data_gouv.fetch_source(src) == []


def test_dispatch_routes_data_gouv_format():
    """Le dispatcher principal doit router `format=data_gouv_*` vers
    `data_gouv.fetch_source`, même dans un groupe YAML arbitraire."""
    fn = normalize._dispatch("ministeres", {
        "id": "min_esr_agenda",
        "format": "data_gouv_agenda",
    })
    assert fn is data_gouv.fetch_source


def test_dispatch_preserves_other_formats():
    """Une source HTML classique (ministère) doit toujours aller chez
    `html_generic`, pas chez data_gouv."""
    from src.sources import html_generic
    fn = normalize._dispatch("ministeres", {
        "id": "min_sports_presse",
        "format": "html",
    })
    assert fn is html_generic.fetch_source


def test_fetch_agenda_unknown_format_returns_empty(monkeypatch):
    """Un format data_gouv_* non géré ne doit pas crasher."""
    src = {"id": "x", "format": "data_gouv_unknown", "url": "y"}
    assert data_gouv.fetch_source(src) == []


def test_fetch_agenda_accepts_description_only_schema(monkeypatch):
    """Schéma Éducation nationale : {uid, dtstart, dtend, description}
    sans `summary` ni `agenda`. Le handler doit accepter description
    comme contenu principal et produire un Item valide."""
    now = _now()
    entries = [
        {
            "uid": 13244,
            "dtstart": (now - timedelta(days=1)).isoformat(),
            "dtend": now.isoformat(),
            "description": "Déplacement en Île-de-France",
        },
        {
            "uid": 13241,
            "dtstart": now.isoformat(),
            "dtend": now.isoformat(),
            "description": "Entretien avec Anne Szymczak, directrice générale du CNED",
        },
    ]
    monkeypatch.setattr(data_gouv, "fetch_text", _make_fetch(entries))
    src = {
        "id": "min_educ_agenda",
        "url": "https://x/y",
        "format": "data_gouv_agenda",
        "chamber": "MinEduc",
        "since_days": 0,
        "title_prefix": "MinEduc —",
    }
    items = data_gouv.fetch_source(src)
    assert len(items) == 2

    it = items[0]
    assert it.chamber == "MinEduc"
    assert it.title.startswith("MinEduc —")
    assert "Déplacement en Île-de-France" in it.title
    # Pas de `(ministre)` entre parenthèses car pas de champ `agenda`
    assert "()" not in it.title
    # uid est un entier → doit être stringify-hashé sans erreur
    assert len(it.uid) == 16
    assert it.raw["upstream_uid"] == "13244"
    # agenda_owner vide quand le champ n'existe pas
    assert it.raw["agenda_owner"] == ""
    # summary = description (pas de champ summary disponible)
    assert "Déplacement" in it.summary


def test_fetch_agenda_prefers_summary_over_description(monkeypatch):
    """Si les deux champs existent (schéma ESR), `summary` est le titre
    et `description` va dans le summary de l'Item (plus riche)."""
    now = _now()
    entries = [{
        "uid": "42",
        "dtstart": now.isoformat(),
        "summary": "Court titre",
        "description": "Description longue et détaillée de l'événement",
        "agenda": "Ministre Test",
    }]
    monkeypatch.setattr(data_gouv, "fetch_text", _make_fetch(entries))
    src = {"id": "x", "url": "y", "format": "data_gouv_agenda", "since_days": 0}
    items = data_gouv.fetch_source(src)
    assert len(items) == 1
    # Titre = summary (court)
    assert "Court titre" in items[0].title
    # Summary Item = description (plus riche que summary)
    assert items[0].summary == "Description longue et détaillée de l'événement"
