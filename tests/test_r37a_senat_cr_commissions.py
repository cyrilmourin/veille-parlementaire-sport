"""R37-A (2026-04-24) — Tests du scraper CR commissions Sénat.

Offline : `fetch_text` monkeypatché. Fixtures HTML construites à partir
du vrai rendu observé le 2026-04-24 sur
/compte-rendu-commissions/culture.html (h3 avec liens vers les CR hebdo).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.sources import senat_cr_commissions as mod


_LISTING_HTML = """
<html><body>
<main>
  <h3 id=curses><a class="link" href="/compte-rendu-commissions/20260413/cult.html">Semaine
  du 13 avril 2026</a></h3><p>…</p>
  <h3 id=curses><a class="link" href="/compte-rendu-commissions/20260406/cult.html">Semaine
  du 6 avril 2026</a></h3><p>…</p>
  <h3 id=curses><a class="link" href="/compte-rendu-commissions/20260330/cult.html">Semaine
  du 30 mars 2026</a></h3><p>…</p>
</main>
</body></html>
"""

_CR_PAGE_HTML = """
<html><body>
<main>
  <h1>Commission de la culture : compte rendu de la semaine du 13 avril 2026</h1>
  <section>
    <h2>Table ronde sur la lutte contre le dopage dans le sport professionnel</h2>
    <p>Audition de M. Dupont, président de l'AFLD, sur les progrès de
    la lutte anti-dopage. Les enjeux des Jeux olympiques de 2030 ont été
    longuement évoqués…</p>
  </section>
</main>
</body></html>
"""

_LISTING_EMPTY = "<html><body><main><p>Aucun CR publié pour l'instant.</p></main></body></html>"


def test_strip_html_removes_tags_and_entities():
    html = "<p>Hello <b>world</b>&nbsp;— test &#039;apos&#039;</p>"
    out = mod._strip_html(html)
    assert "Hello" in out
    assert "<" not in out
    assert "&nbsp;" not in out
    assert "'apos'" in out


def test_strip_html_removes_scripts_and_styles():
    html = "<html><script>var x=1;</script><style>.a{}</style><p>Keep me</p></html>"
    out = mod._strip_html(html)
    assert "var x" not in out
    assert ".a{}" not in out
    assert "Keep me" in out


def test_parse_listing_extracts_entries():
    entries = mod._parse_listing(_LISTING_HTML)
    assert len(entries) == 3
    assert entries[0]["yyyymmdd"] == "20260413"
    assert entries[0]["short"] == "cult"
    assert entries[0]["date"] == datetime(2026, 4, 13)
    assert entries[0]["url"].endswith("/compte-rendu-commissions/20260413/cult.html")


def test_parse_listing_respects_max_entries():
    entries = mod._parse_listing(_LISTING_HTML, max_entries=2)
    assert len(entries) == 2


def test_parse_listing_empty():
    assert mod._parse_listing(_LISTING_EMPTY) == []


def test_fetch_source_produces_items_with_organe(monkeypatch):
    """Items ont `raw.organe` = `commission_organe` pour R27 bypass."""
    fetched: list[str] = []

    def _fake_fetch(url: str) -> str:
        fetched.append(url)
        if url.endswith("culture.html"):
            return _LISTING_HTML
        return _CR_PAGE_HTML

    monkeypatch.setattr(mod, "fetch_text", _fake_fetch)

    src = {
        "id": "senat_cr_culture",
        "category": "comptes_rendus",
        "url": "https://www.senat.fr/compte-rendu-commissions/culture.html",
        "commission_label": "Commission culture/éducation/communication/sport",
        "commission_organe": "PO211490",
        "max_new_per_run": 3,
    }
    items = mod.fetch_source(src)
    assert len(items) == 3
    it0 = items[0]
    assert it0.source_id == "senat_cr_culture"
    assert it0.category == "comptes_rendus"
    assert it0.chamber == "Senat"
    assert it0.title.startswith("Commission culture/éducation/communication/sport — Semaine")
    assert it0.published_at == datetime(2026, 4, 13)
    assert it0.raw["organe"] == "PO211490"
    assert it0.raw["path"] == "senat:cr_commissions_html"
    assert "dopage" in it0.raw["haystack_body"].lower()
    # Les 3 CR hebdo ont été fetchés (+1 pour le listing)
    assert len(fetched) == 4


def test_fetch_source_listing_ko_returns_empty(monkeypatch):
    def _raiser(url: str) -> str:
        raise RuntimeError("network down")
    monkeypatch.setattr(mod, "fetch_text", _raiser)
    items = mod.fetch_source({
        "id": "senat_cr_culture",
        "url": "https://example.test/",
        "commission_label": "X",
    })
    assert items == []


def test_fetch_source_empty_listing_returns_empty(monkeypatch):
    monkeypatch.setattr(mod, "fetch_text", lambda url: _LISTING_EMPTY)
    items = mod.fetch_source({
        "id": "senat_cr_culture",
        "url": "https://example.test/",
        "commission_label": "X",
    })
    assert items == []


def test_fetch_source_individual_cr_ko_is_skipped(monkeypatch):
    """Si un CR hebdo renvoie une erreur, on continue avec les suivants."""
    def _fetch(url: str) -> str:
        if "culture.html" in url:
            return _LISTING_HTML
        if "20260413" in url:
            raise RuntimeError("404")
        return _CR_PAGE_HTML
    monkeypatch.setattr(mod, "fetch_text", _fetch)
    items = mod.fetch_source({
        "id": "senat_cr_culture",
        "url": "https://example.test/culture.html",
        "commission_label": "X",
        "max_new_per_run": 5,
    })
    # 2 sur 3 ont été ingérés (le 13 avril a échoué)
    assert len(items) == 2


def test_fetch_source_uid_stable(monkeypatch):
    """Re-fetch de la même page → mêmes UIDs."""
    def _fetch(url: str) -> str:
        return _LISTING_HTML if url.endswith("culture.html") else _CR_PAGE_HTML
    monkeypatch.setattr(mod, "fetch_text", _fetch)
    src = {
        "id": "senat_cr_culture",
        "url": "https://example.test/",
        "commission_label": "X",
        "max_new_per_run": 3,
    }
    a = mod.fetch_source(src)
    b = mod.fetch_source(src)
    assert [it.uid for it in a] == [it.uid for it in b]
