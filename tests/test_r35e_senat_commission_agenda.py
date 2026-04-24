"""R35-E (2026-04-24) — Tests du scraper d'agenda commission Sénat.

Tous offline : `fetch_text` monkeypatché. Fixtures HTML construites à
partir du vrai rendu TYPO3 Sénat observé le 2026-04-24 sur la page
/travaux-parlementaires/commissions/commission-des-finances/agenda-de-la-commission.html
(3 réunions : 28 et 29 avril 2026 — voir audit R35-E).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.sources import senat_commission_agenda as mod


# ---------------------------------------------------------------------------
# Fixtures HTML
# ---------------------------------------------------------------------------

def _li(*, day: int, month: str, title: str, salle: str, heure: str) -> str:
    """Fabrique un <li.list-group-item> identique au rendu Sénat."""
    return (
        f'<li class="list-group-item">'
        f'<div class="row">'
        f'<div class="col-2"><div class="d-flex flex-column">'
        f'<span class="display-4 ff-alt lh-1">{day}</span>'
        f'<span class="mt-n1 fw-semibold lh-1">{month}</span>'
        f'</div></div>'
        f'<div class="col-10 d-flex flex-column">'
        f'<h4 class="list-group-title line-clamp-3" title="{title}">{title}</h4>'
        f'<p class="list-group-subtitle">{salle}</p>'
        f'<time datetime="{heure}"><i class="bi bi-clock"></i> {heure}h</time>'
        f'</div></div></li>'
    )


_HTML_EMPTY = (
    '<html><body>'
    '<h3 class="mt-md-1 mt-lg-2">Prochaines réunions</h3>'
    '<p>Aucun événement n\'est actuellement inscrit à l\'agenda.</p>'
    '</body></html>'
)


def _html_with_events(*lis: str) -> str:
    return (
        '<html><body>'
        '<h3 class="mt-md-1 mt-lg-2">Prochaines réunions</h3>'
        '<ul class="list-group list-group-flush">'
        + "".join(lis)
        + '</ul>'
        '</body></html>'
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_parse_mois_tolerant():
    assert mod._parse_mois("avril") == 4
    assert mod._parse_mois("AVRIL") == 4
    assert mod._parse_mois("Août") == 8
    assert mod._parse_mois("aout") == 8  # sans accent
    assert mod._parse_mois("décembre") == 12
    assert mod._parse_mois("") is None
    assert mod._parse_mois(None) is None


def test_resolve_date_future_same_year():
    """Le 28 avril 2026 vu le 24 avril 2026 → 2026-04-28."""
    now = datetime(2026, 4, 24, 10, 0)
    out = mod._resolve_date(28, 4, "09:00", now)
    assert out == datetime(2026, 4, 28, 9, 0)


def test_resolve_date_past_within_30_days():
    """Le 24 avril vu le 28 avril 2026 → 2026-04-24 (pas 2027)."""
    now = datetime(2026, 4, 28, 10, 0)
    out = mod._resolve_date(24, 4, "09:00", now)
    assert out == datetime(2026, 4, 24, 9, 0)


def test_resolve_date_past_further_flips_year():
    """Le 10 janvier vu le 20 décembre 2026 → 2027-01-10."""
    now = datetime(2026, 12, 20, 10, 0)
    out = mod._resolve_date(10, 1, "09:00", now)
    assert out == datetime(2027, 1, 10, 9, 0)


def test_resolve_date_invalid_day_returns_none():
    now = datetime(2026, 4, 24)
    assert mod._resolve_date(32, 4, "09:00", now) is None
    # Février 30 jours n'existe pas
    assert mod._resolve_date(30, 2, "09:00", now) is None


def test_resolve_date_no_time_defaults_midnight():
    now = datetime(2026, 4, 24)
    out = mod._resolve_date(28, 4, None, now)
    assert out == datetime(2026, 4, 28, 0, 0)


# ---------------------------------------------------------------------------
# _parse_page
# ---------------------------------------------------------------------------

def test_parse_page_returns_empty_on_no_events():
    """Page 'Aucun événement' : 0 event, pas d'exception."""
    now = datetime(2026, 4, 24)
    assert mod._parse_page(_HTML_EMPTY, now=now) == []


def test_parse_page_returns_empty_on_missing_block():
    """Page sans <h3>Prochaines réunions</h3> (autre commission, template exotique)."""
    now = datetime(2026, 4, 24)
    assert mod._parse_page("<html><body>pas d'agenda</body></html>", now=now) == []


def test_parse_page_parses_three_events():
    """Fixture réelle : 3 réunions Commission finances Sénat (24 avril 2026)."""
    html = _html_with_events(
        _li(
            day=28, month="avril",
            title="Communication de M. Jean-François Husson, rapporteur général",
            salle="Salle A131 - 1er étage Ouest",
            heure="9:00",
        ),
        _li(
            day=29, month="avril",
            title="PPL Garantie à l'accès au compte bancaire",
            salle="Salle A131 - 1er étage Ouest",
            heure="10:00",
        ),
        _li(
            day=29, month="avril",
            title="Audition de M. David Amiel, ministre",
            salle="Salle A131 - 1er étage Ouest",
            heure="16:30",
        ),
    )
    now = datetime(2026, 4, 24, 10, 0)
    events = mod._parse_page(html, now=now)
    assert len(events) == 3
    assert events[0]["event_dt"] == datetime(2026, 4, 28, 9, 0)
    assert events[1]["event_dt"] == datetime(2026, 4, 29, 10, 0)
    assert events[2]["event_dt"] == datetime(2026, 4, 29, 16, 30)
    assert events[0]["salle"].startswith("Salle A131")
    assert "Husson" in events[0]["title"]
    assert events[2]["time_hhmm"] == "16:30"


def test_parse_page_skips_malformed_li():
    """Un <li> sans jour / mois / titre doit être skippé silencieusement."""
    now = datetime(2026, 4, 24)
    # Un item OK + un item cassé (pas de titre)
    ok = _li(day=28, month="avril", title="Audition sport", salle="Salle X", heure="9:00")
    broken = '<li class="list-group-item"><div class="row"><span class="display-4 ff-alt lh-1">99</span></div></li>'
    html = _html_with_events(ok, broken)
    events = mod._parse_page(html, now=now)
    assert len(events) == 1
    assert "Audition sport" in events[0]["title"]


def test_parse_page_skips_title_too_short():
    """Titre < 5 caractères → item jeté (probable artefact HTML)."""
    now = datetime(2026, 4, 24)
    short = _li(day=28, month="avril", title="x", salle="s", heure="9:00")
    html = _html_with_events(short)
    assert mod._parse_page(html, now=now) == []


# ---------------------------------------------------------------------------
# fetch_source (integration offline)
# ---------------------------------------------------------------------------

def test_fetch_source_returns_items_with_organe(monkeypatch):
    """Items produits ont `raw.organe` = `commission_organe` (pour R27 bypass).

    C'est le point clé qui permet au bypass organe de matcher côté
    Sénat comme il le fait côté AN : sans ça, ces events commission
    culture Sénat ne remonteraient qu'avec un match keyword explicite.
    """
    html = _html_with_events(_li(
        day=28, month="avril",
        title="Audition du ministre des Sports",
        salle="Salle X",
        heure="9:00",
    ))
    monkeypatch.setattr(mod, "fetch_text", lambda url: html)
    # Pin now pour stabilité
    import datetime as _dt
    real_datetime = _dt.datetime
    class _FrozenNow(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 4, 24, 10, 0)
    monkeypatch.setattr(mod, "datetime", _FrozenNow)
    src = {
        "id": "senat_agenda_culture",
        "url": "https://www.senat.fr/…/agenda-de-la-commission.html",
        "category": "agenda",
        "commission_label": "Commission culture/éducation/communication/sport",
        "commission_organe": "PO211490",
    }
    items = mod.fetch_source(src)
    assert len(items) == 1
    it = items[0]
    assert it.source_id == "senat_agenda_culture"
    assert it.category == "agenda"
    assert it.chamber == "Senat"
    assert it.title.startswith("Commission culture/éducation/communication/sport — ")
    assert "Audition du ministre des Sports" in it.title
    assert it.published_at == datetime(2026, 4, 28, 9, 0)
    assert it.raw["organe"] == "PO211490"
    assert it.raw["path"] == "senat:commission_agenda_html"
    assert it.raw["commission"] == "Commission culture/éducation/communication/sport"


def test_fetch_source_empty_page_returns_empty(monkeypatch):
    """Page 'Aucun événement' → 0 item (cas inter-session / pause parlementaire)."""
    monkeypatch.setattr(mod, "fetch_text", lambda url: _HTML_EMPTY)
    items = mod.fetch_source({
        "id": "senat_agenda_culture",
        "url": "https://example.test/",
        "commission_label": "X",
    })
    assert items == []


def test_fetch_source_fetch_error_returns_empty(monkeypatch):
    """Si fetch_text raise, on renvoie [] sans polluer le pipeline."""
    def _raiser(url):
        raise RuntimeError("network down")
    monkeypatch.setattr(mod, "fetch_text", _raiser)
    items = mod.fetch_source({
        "id": "senat_agenda_culture",
        "url": "https://example.test/",
        "commission_label": "X",
    })
    assert items == []


def test_fetch_source_uid_stable_across_runs(monkeypatch):
    """Re-fetch de la même page → mêmes UIDs (idempotence cron)."""
    html = _html_with_events(_li(
        day=28, month="avril",
        title="Audition X",
        salle="Salle Y",
        heure="9:00",
    ))
    monkeypatch.setattr(mod, "fetch_text", lambda url: html)
    import datetime as _dt
    real_datetime = _dt.datetime
    class _FrozenNow(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 4, 24, 10, 0)
    monkeypatch.setattr(mod, "datetime", _FrozenNow)
    src = {
        "id": "senat_agenda_culture",
        "url": "https://example.test/",
        "commission_label": "X",
    }
    items_a = mod.fetch_source(src)
    items_b = mod.fetch_source(src)
    assert items_a[0].uid == items_b[0].uid


def test_fetch_source_no_commission_label_falls_back_to_raw_title(monkeypatch):
    """Sans commission_label YAML, pas de préfixe — juste le titre brut."""
    html = _html_with_events(_li(
        day=28, month="avril",
        title="Réunion libre",
        salle="Salle Z",
        heure="11:00",
    ))
    monkeypatch.setattr(mod, "fetch_text", lambda url: html)
    items = mod.fetch_source({
        "id": "x",
        "url": "https://example.test/",
        # pas de commission_label
    })
    assert len(items) == 1
    assert items[0].title == "Réunion libre"
