"""R42-BR — Handler INSEP /fr/actualites (scraping HTML via curl_cffi).

Contexte : Cyril 2026-05-12 « les flux INSEP ne marchent pas bien, ils sont
pas actualisés. On ne peut pas scraper la page d'actualité [...]/fr/actualites ».
Le proxy CF (R42-BO) est inopérant sur INSEP : HTTP 418 « I'm a teapot »
systématique sur toute IP CF Worker, reproductible aussi depuis poste local.
curl_cffi + TLS Chrome 120 sur le path HTML répond 200 depuis poste local —
on parie que ça marche aussi depuis GHA (à valider au 1er daily).

Structure HTML INSEP :
  - `<article class="news-block__item ...">` (8 par page)
  - `<a href="/fr/actualites/<slug>">` + heading
  - Pas de `<time>` au listing → fetch page individuelle pour la date
    (1 `<time datetime="YYYY-MM-DD">` sur chaque page article).
"""
from __future__ import annotations

from unittest.mock import patch

import httpx

from src.sources import html_generic


_LISTING_HTML = """
<!doctype html>
<html><body>
  <article class="news-block__item underline-context -has-shadow">
    <a href="/fr/actualites/dre-margo-mountjoy-linsep">
      <h2>Dre Margo Mountjoy à l'INSEP : un échange scientifique international</h2>
    </a>
    <a href="/fr/taxonomy/term/123">Réseau international</a>
    <a href="/fr/taxonomy/term/124">Médical</a>
    <p>Chercheuse de renommée mondiale et experte en santé des athlètes…</p>
  </article>
  <article class="news-block__item underline-context -has-shadow">
    <a href="/fr/actualites/la-pr-louise-burke-en-visite-linsep">
      <h2>La Pr Louise Burke en visite à l'INSEP</h2>
    </a>
    <a href="/fr/taxonomy/term/125">Recherche</a>
    <p>Spécialiste mondiale de la nutrition sportive…</p>
  </article>
  <!-- doit être ignoré : pas dans une <article class="news-block__item"> -->
  <a href="/fr/actualites/page-de-nav">Lien de navigation</a>
</body></html>
"""

_ARTICLE_HTML_1 = """
<!doctype html>
<html><body>
  <article>
    <h1>Dre Margo Mountjoy à l'INSEP</h1>
    <time datetime="2026-04-14">14 avril 2026</time>
    <p>Le contenu de l'article…</p>
  </article>
</body></html>
"""

_ARTICLE_HTML_2 = """
<!doctype html>
<html><body>
  <article>
    <h1>La Pr Louise Burke en visite à l'INSEP</h1>
    <time datetime="2026-04-08">8 avril 2026</time>
  </article>
</body></html>
"""


def _fake_fetch_text(url, impersonate=False, via_proxy=False):
    if url.endswith("/fr/actualites"):
        return _LISTING_HTML
    if "dre-margo-mountjoy" in url:
        return _ARTICLE_HTML_1
    if "louise-burke" in url:
        return _ARTICLE_HTML_2
    raise RuntimeError(f"unexpected URL: {url}")


def test_insep_extracts_2_articles():
    """Listing scrapé + dates fetchées sur les pages individuelles."""
    src = {
        "id": "insep",
        "category": "communiques",
        "url": "https://www.insep.fr/fr/actualites",
        "format": "insep_actualites_html",
        "impersonate": True,
    }
    with patch("src.sources.html_generic.fetch_text", side_effect=_fake_fetch_text):
        items = html_generic._from_insep_actualites_html(src)
    assert len(items) == 2
    titles = {it.title for it in items}
    assert "Dre Margo Mountjoy à l'INSEP : un échange scientifique international" in titles
    assert "La Pr Louise Burke en visite à l'INSEP" in titles


def test_insep_dates_from_individual_pages():
    """`<time datetime="YYYY-MM-DD">` sur la page article → published_at."""
    src = {
        "id": "insep",
        "category": "communiques",
        "url": "https://www.insep.fr/fr/actualites",
        "format": "insep_actualites_html",
        "impersonate": True,
    }
    with patch("src.sources.html_generic.fetch_text", side_effect=_fake_fetch_text):
        items = html_generic._from_insep_actualites_html(src)
    by_title = {it.title: it for it in items}
    margo = by_title["Dre Margo Mountjoy à l'INSEP : un échange scientifique international"]
    assert margo.published_at is not None
    assert margo.published_at.year == 2026 and margo.published_at.month == 4 and margo.published_at.day == 14


def test_insep_chamber_is_insep():
    src = {
        "id": "insep",
        "category": "communiques",
        "url": "https://www.insep.fr/fr/actualites",
        "format": "insep_actualites_html",
        "impersonate": True,
    }
    with patch("src.sources.html_generic.fetch_text", side_effect=_fake_fetch_text):
        items = html_generic._from_insep_actualites_html(src)
    assert all(it.chamber == "INSEP" for it in items)


def test_insep_summary_includes_tags_and_intro():
    """Le summary contient les tags taxonomy + l'intro <p>."""
    src = {
        "id": "insep",
        "category": "communiques",
        "url": "https://www.insep.fr/fr/actualites",
        "format": "insep_actualites_html",
        "impersonate": True,
    }
    with patch("src.sources.html_generic.fetch_text", side_effect=_fake_fetch_text):
        items = html_generic._from_insep_actualites_html(src)
    margo = next(it for it in items if "Margo" in it.title)
    # Tags présents
    assert "Réseau international" in margo.summary
    assert "Médical" in margo.summary
    # Intro présente
    assert "Chercheuse de renommée mondiale" in margo.summary


def test_insep_handles_listing_fetch_error():
    """Soft-fail : si le listing échoue, on retourne [] (pas de crash)."""
    src = {
        "id": "insep",
        "category": "communiques",
        "url": "https://www.insep.fr/fr/actualites",
        "format": "insep_actualites_html",
        "impersonate": True,
    }
    with patch("src.sources.html_generic.fetch_text",
               side_effect=httpx.HTTPStatusError("418 teapot", request=None, response=None)):
        items = html_generic._from_insep_actualites_html(src)
    assert items == []


def test_insep_handles_individual_page_error_keeps_item_without_date():
    """Si le fetch d'une page individuelle échoue, on garde l'item avec
    published_at=None (le pipeline fallback inserted_at)."""
    src = {
        "id": "insep",
        "category": "communiques",
        "url": "https://www.insep.fr/fr/actualites",
        "format": "insep_actualites_html",
        "impersonate": True,
    }

    def _fake(url, impersonate=False, via_proxy=False):
        if url.endswith("/fr/actualites"):
            return _LISTING_HTML
        raise RuntimeError("page detail KO")

    with patch("src.sources.html_generic.fetch_text", side_effect=_fake):
        items = html_generic._from_insep_actualites_html(src)
    assert len(items) == 2
    assert all(it.published_at is None for it in items)


def test_insep_routed_via_fetch_source():
    """fetch_source dispatch sur format=`insep_actualites_html`."""
    src = {
        "id": "insep",
        "category": "communiques",
        "url": "https://www.insep.fr/fr/actualites",
        "format": "insep_actualites_html",
        "impersonate": True,
    }
    with patch("src.sources.html_generic.fetch_text", side_effect=_fake_fetch_text):
        items = html_generic.fetch_source(src)
    assert len(items) == 2
