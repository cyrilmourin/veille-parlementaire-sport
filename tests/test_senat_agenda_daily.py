"""Tests du scraper `senat_agenda_daily` (R15, 2026-04-22).

Stratégie : on mocke `fetch_text` dans `src.sources.senat` via
monkeypatch pour servir un HTML synthétique qui reproduit la structure
attendue (`<div id="content">…<section class="event">…</section></div>`).

On vérifie :
- URL construite selon le format officiel `aglDDMMYYYY[Print].html`.
- Items extraits avec `chamber="Senat"`, date/heure correcte,
  `raw.section` + `raw.lieu` peuplés.
- Mode `printable=True` : 1 fetch par jour (toutes sections agrégées).
- Graceful degradation : page "Accès restreint" → 0 item silencieux.
- Fenêtre partielle : une erreur réseau sur 1 jour n'annule pas le run.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.sources import senat  # noqa: E402


# ---------- fixtures HTML ----------

_OK_PAGE = """
<!doctype html>
<html><body>
  <div id="content">
    <section class="event">
      <h3>Commission des affaires sociales — dopage dans le sport</h3>
      <span class="lieu">Salle Clemenceau</span>
      <p>Audition à 14h30 de la DNCG.</p>
    </section>
    <section class="event">
      <h4>Délégation aux droits des femmes — sport féminin</h4>
      <span class="lieu">Salle Médicis</span>
      <p>Réunion 10:00</p>
    </section>
    <div class="not-an-event">
      <h3>Bandeau de navigation à ignorer</h3>
    </div>
  </div>
</body></html>
"""

_ACCES_RESTREINT = "<html><body>Accès restreint - veuillez vous authentifier</body></html>"


def _make_fetch(mapping: dict[str, str | Exception]):
    """Renvoie une callable qui simule `fetch_text` à partir d'un mapping.

    `mapping` maps URL → body (str) ou Exception (levée).
    URL absente → renvoie `_ACCES_RESTREINT` (404 silencieux Sénat).
    """
    def _fake(url: str) -> str:
        val = mapping.get(url, _ACCES_RESTREINT)
        if isinstance(val, Exception):
            raise val
        return val
    return _fake


# ---------- tests URL builder ----------

def test_senat_agenda_url_printable():
    dt = datetime(2026, 4, 22, 10, 0)
    url = senat._senat_agenda_url("", dt, printable=True)
    assert url == "https://www.senat.fr/agenda/Global/agl22042026Print.html"


def test_senat_agenda_url_section():
    dt = datetime(2026, 4, 22, 10, 0)
    url = senat._senat_agenda_url("Commissions", dt, printable=False)
    assert url == "https://www.senat.fr/agenda/Commissions/agl22042026.html"


# ---------- tests parse ----------

def test_parse_senat_agenda_page_extracts_events():
    day = datetime(2026, 4, 22)
    events = senat._parse_senat_agenda_page(_OK_PAGE, day, "Global")
    # On a 2 `section.event` valides + 1 bandeau filtré
    assert len(events) == 2

    ev1, ev2 = events
    assert "dopage" in ev1["title"].lower()
    assert ev1["lieu"] == "Salle Clemenceau"
    assert ev1["heure"] == "14h30"
    assert ev1["event_dt"] == day.replace(hour=14, minute=30)

    assert "sport féminin" in ev2["title"].lower()
    assert ev2["lieu"] == "Salle Médicis"
    assert ev2["heure"] == "10h00"


def test_parse_senat_agenda_page_empty_when_no_content_div():
    # Pas de div#content → page "dégradée" → 0 item
    events = senat._parse_senat_agenda_page(
        "<html><body>rien</body></html>", datetime(2026, 4, 22), "Global",
    )
    assert events == []


# ---------- tests handler end-to-end (avec monkeypatch) ----------

def test_fetch_agenda_daily_printable_one_fetch_per_day(monkeypatch):
    """Mode printable : 1 seule URL/jour, agrégation sections."""
    day = datetime(2026, 4, 22)
    url_ok = senat._senat_agenda_url("", day, printable=True)
    calls = []

    def _fake_fetch(url: str) -> str:
        calls.append(url)
        if url == url_ok:
            return _OK_PAGE
        return _ACCES_RESTREINT

    monkeypatch.setattr(senat, "fetch_text", _fake_fetch)
    # Fenêtre réduite à [J, J] (before=0, after=0) pour isoler un seul jour
    # → on patch `_iter_date_window` pour ne yield QUE `day`.
    monkeypatch.setattr(senat, "_iter_date_window",
                        lambda b, a: iter([day]))

    src = {
        "id": "senat_agenda",
        "category": "agenda",
        "before_days": 0,
        "after_days": 0,
        "printable": True,
    }
    items = senat._fetch_agenda_daily(src)
    assert len(items) == 2
    # 1 seul fetch (printable = 1 URL par jour)
    assert calls == [url_ok]

    # Vérifier que les champs Item sont bien populés
    it = items[0]
    assert it.source_id == "senat_agenda"
    assert it.category == "agenda"
    assert it.chamber == "Senat"
    assert it.published_at == day.replace(hour=14, minute=30)
    assert it.raw["section"] == "Global"
    assert it.raw["lieu"] == "Salle Clemenceau"
    assert it.raw["day"] == "2026-04-22"


def test_fetch_agenda_daily_handles_acces_restreint(monkeypatch):
    """Page "Accès restreint" → 0 item, pas de crash."""
    day = datetime(2026, 4, 22)
    monkeypatch.setattr(senat, "fetch_text", lambda u: _ACCES_RESTREINT)
    monkeypatch.setattr(senat, "_iter_date_window",
                        lambda b, a: iter([day]))
    src = {"id": "senat_agenda", "printable": True}
    items = senat._fetch_agenda_daily(src)
    assert items == []


def test_fetch_agenda_daily_handles_http_failure(monkeypatch):
    """Exception réseau sur un jour → continue avec les autres jours."""
    day_ko = datetime(2026, 4, 22)
    day_ok = datetime(2026, 4, 23)
    url_ok = senat._senat_agenda_url("", day_ok, printable=True)
    url_ko = senat._senat_agenda_url("", day_ko, printable=True)

    def _fake_fetch(url: str) -> str:
        if url == url_ko:
            raise TimeoutError("simulated")
        if url == url_ok:
            return _OK_PAGE
        return _ACCES_RESTREINT

    monkeypatch.setattr(senat, "fetch_text", _fake_fetch)
    monkeypatch.setattr(senat, "_iter_date_window",
                        lambda b, a: iter([day_ko, day_ok]))
    src = {"id": "senat_agenda", "printable": True}
    items = senat._fetch_agenda_daily(src)
    # 2 events le jour OK, 0 le jour KO → window partielle mais non-vide
    assert len(items) == 2
    assert all(it.raw["day"] == "2026-04-23" for it in items)


def test_fetch_agenda_daily_sections_mode_requests_each_section(monkeypatch):
    """Mode printable=False : 1 fetch par section par jour."""
    day = datetime(2026, 4, 22)
    calls: list[str] = []
    monkeypatch.setattr(
        senat, "fetch_text",
        lambda u: (calls.append(u), _ACCES_RESTREINT)[1],
    )
    monkeypatch.setattr(senat, "_iter_date_window",
                        lambda b, a: iter([day]))
    src = {
        "id": "senat_agenda",
        "printable": False,
        "sections": ["Seance", "Commissions"],
    }
    senat._fetch_agenda_daily(src)
    # 2 sections × 1 jour = 2 fetches
    assert len(calls) == 2
    assert "Seance/agl22042026" in calls[0]
    assert "Commissions/agl22042026" in calls[1]


def test_fetch_source_routes_senat_agenda_daily(monkeypatch):
    """`fetch_source` doit router `format=senat_agenda_daily`
    vers `_fetch_agenda_daily` (pas vers `html`/`csv`)."""
    called = {}

    def _spy(src):
        called["yes"] = src
        return []

    monkeypatch.setattr(senat, "_fetch_agenda_daily", _spy)
    senat.fetch_source({
        "id": "senat_agenda",
        "format": "senat_agenda_daily",
        "url": "https://www.senat.fr/agenda/",
    })
    assert "yes" in called
