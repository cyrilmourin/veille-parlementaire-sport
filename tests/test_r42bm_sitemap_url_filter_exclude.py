"""Tests R42-BM — `url_filter_exclude` dans le scraper sitemap_generic.

Cyril 2026-05-12 : « /la-composition-de-la-conference-des-conciliateurs »
qui remonte comme dernière actu CNOSF, alors que c'est une page
institutionnelle statique. Le sitemap Drupal CNOSF met à jour
`<lastmod>` à chaque édition mineure, d'où le faux positif.
"""
from __future__ import annotations

from unittest.mock import patch

from src.sources.html_generic import _from_sitemap_generic


def _sitemap_fixture(urls: list[tuple[str, str]]) -> str:
    """Génère un sitemap XML avec (loc, lastmod)."""
    items = "\n".join(
        f"  <url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>"
        for loc, lastmod in urls
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{items}
</urlset>"""


def _src(url_filter=None, url_filter_exclude=None) -> dict:
    src = {
        "id": "test_sitemap",
        "category": "communiques",
        "url": "https://cnosf.franceolympique.com/sitemap.xml",
        "format": "sitemap",
    }
    if url_filter is not None:
        src["url_filter"] = url_filter
    if url_filter_exclude is not None:
        src["url_filter_exclude"] = url_filter_exclude
    return src


def test_url_filter_exclude_rejette_pages_institutionnelles():
    """3 pages institutionnelles rejetées par les patterns CNOSF."""
    fixture = _sitemap_fixture([
        ("https://cnosf.franceolympique.com/le-cnosf-felicite-medaille",
         "2026-05-10T10:00:00Z"),
        ("https://cnosf.franceolympique.com/la-composition-de-la-conference-des-conciliateurs",
         "2026-05-11T15:00:00Z"),
        ("https://cnosf.franceolympique.com/organisation-operationnelle",
         "2026-05-12T08:00:00Z"),
        ("https://cnosf.franceolympique.com/comite-de-deontologie-du-cnosf",
         "2026-05-12T08:00:00Z"),
    ])
    with patch("src.sources.html_generic.fetch_text", return_value=fixture):
        items = _from_sitemap_generic(_src(
            url_filter=["/"],
            url_filter_exclude=[
                "/la-composition-", "/organisation-", "/comite-de-deontologie",
            ],
        ))
    # Seul le 1er (vraie actu) doit rester
    assert len(items) == 1
    assert "felicite" in items[0].url


def test_pas_url_filter_exclude_no_op():
    """Sans `url_filter_exclude` : comportement legacy (toutes URLs
    matchant url_filter passent)."""
    fixture = _sitemap_fixture([
        ("https://cnosf.franceolympique.com/page-a",
         "2026-05-10T10:00:00Z"),
        ("https://cnosf.franceolympique.com/page-b",
         "2026-05-11T10:00:00Z"),
    ])
    with patch("src.sources.html_generic.fetch_text", return_value=fixture):
        items = _from_sitemap_generic(_src(url_filter=["/"]))
    assert len(items) == 2


def test_url_filter_exclude_case_insensitive():
    """Comparaison sur loc.lower() → cas case-insensitive."""
    fixture = _sitemap_fixture([
        ("https://cnosf.franceolympique.com/LA-CHARTE-OLYMPIQUE",
         "2026-05-12T08:00:00Z"),
    ])
    with patch("src.sources.html_generic.fetch_text", return_value=fixture):
        items = _from_sitemap_generic(_src(
            url_filter=["/"],
            url_filter_exclude=["/la-charte-"],
        ))
    assert items == []


def test_url_filter_exclude_patterns_sans_slash_match_large():
    """Patterns sans `/` initial = match substring large. Permet
    d'attraper les pages institutionnelles dont le pattern apparaît
    n'importe où dans le slug (ex. `cahn` matche `de-la-cahn`)."""
    fixture = _sitemap_fixture([
        ("https://cnosf.franceolympique.com/comment-saisir-la-conciliation",
         "2026-05-12T08:00:00Z"),
        ("https://cnosf.franceolympique.com/conference-des-conciliateurs",
         "2026-05-12T08:00:00Z"),
        ("https://cnosf.franceolympique.com/role-et-missions-de-la-cahn",
         "2026-05-12T08:00:00Z"),
        ("https://cnosf.franceolympique.com/athletes-les-rendez-vous-de-la-cahn",
         "2026-05-12T08:00:00Z"),
    ])
    with patch("src.sources.html_generic.fetch_text", return_value=fixture):
        items = _from_sitemap_generic(_src(
            url_filter=["/"],
            url_filter_exclude=["conciliation", "conciliateur", "cahn"],
        ))
    # Les 4 rejetés (patterns sans / matchent en substring partout)
    assert items == []
