"""R41-AS (2026-05-10) — Filtre stop-slugs côté sitemap parser.

Bug observé (capture Cyril 2026-05-10 page mobile /items/communiques/) :
la première publi CNOSF avait pour titre « Accueil » avec un sub-titre
« | Comité national olympique » — manifestement la page d'accueil du
site cnosf.franceolympique.com qui a un lastmod récent dans le sitemap
et dont le slug `/accueil` a été pris pour une actualité.

Cause : `url_filter: ["/"]` côté CNOSF (R22c) accepte tous les slugs (pas
de motif `actualites/`), et le titre est reconstitué depuis le slug
(`accueil` → « Accueil »).

Fix structurel : `_SITEMAP_STOP_SLUGS` exclut les slugs de pages
techniques/institutionnelles permanentes (accueil, contact, mentions
légales, etc.) côté scraper, AVANT le KeywordMatcher. Plus propre
qu'une entrée blocklist ad-hoc parce que générique pour tous les futurs
sites avec sitemap ouvert.

Tests :
1. Le filtre exclut bien `/accueil` même avec lastmod récent.
2. Variantes courantes (contact, mentions-legales, qui-sommes-nous, RGPD,
   plan-du-site) sont aussi exclues.
3. Casse insensible : `/Accueil` exclu autant que `/accueil`.
4. Les slugs réels d'actualités passent toujours.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import src.sources.html_generic as hg


SITEMAP_TPL = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>
"""


def _make_sitemap(entries: list[tuple[str, str]]) -> str:
    """entries = [(loc, lastmod_iso), ...]"""
    items = "\n".join(
        f"  <url><loc>{loc}</loc><lastmod>{last}</lastmod></url>"
        for loc, last in entries
    )
    return SITEMAP_TPL.format(urls=items)


def test_sitemap_skip_slug_accueil():
    """Le slug `/accueil` (sitemap CNOSF) ne doit PAS produire d'item."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    sitemap = _make_sitemap([
        ("https://cnosf.franceolympique.com/accueil", today),
        ("https://cnosf.franceolympique.com/100eme-anniversaire-cnosf", today),
    ])
    src = {
        "id": "cnosf",
        "url": "https://cnosf.franceolympique.com/sitemap.xml",
        "category": "communiques",
        "url_filter": ["/"],
    }
    with patch.object(hg, "fetch_text", return_value=sitemap):
        items = hg._from_sitemap_generic(src)
    titles = [i.title for i in items]
    assert "Accueil" not in titles, (
        "Le slug /accueil ne doit pas remonter — c'est la home, pas une actu"
    )
    # L'autre URL avec un slug d'actualité doit, elle, passer.
    assert any("100eme" in t.lower() for t in titles), (
        f"Une actualité légitime devrait remonter, got: {titles}"
    )


def test_sitemap_skip_common_techincal_slugs():
    """Variantes courantes (contact, mentions, RGPD, plan-du-site, etc.)."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    technical = [
        "contact", "mentions-legales", "qui-sommes-nous", "rgpd", "cgu",
        "plan-du-site", "newsletter", "recherche", "presentation",
        "accessibilite",
    ]
    sitemap = _make_sitemap([
        (f"https://example.com/{slug}", today) for slug in technical
    ])
    src = {
        "id": "example",
        "url": "https://example.com/sitemap.xml",
        "category": "communiques",
        "url_filter": ["/"],
    }
    with patch.object(hg, "fetch_text", return_value=sitemap):
        items = hg._from_sitemap_generic(src)
    assert items == [], (
        f"Aucun slug technique ne doit produire d'item, got {len(items)}"
    )


def test_sitemap_stop_slugs_case_insensitive():
    """Le filtre stop-slug doit être insensible à la casse."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    sitemap = _make_sitemap([
        ("https://example.com/Accueil", today),
        ("https://example.com/CONTACT", today),
        ("https://example.com/Mentions-Legales", today),
    ])
    src = {
        "id": "example",
        "url": "https://example.com/sitemap.xml",
        "category": "communiques",
        "url_filter": ["/"],
    }
    with patch.object(hg, "fetch_text", return_value=sitemap):
        items = hg._from_sitemap_generic(src)
    assert items == []


def test_sitemap_real_slugs_still_pass():
    """Régression : les vrais slugs d'actualité ne doivent pas être bloqués."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    sitemap = _make_sitemap([
        ("https://cnosf.franceolympique.com/630-000-participants-pour-la-10eme-edition-de-la-sop", today),
        ("https://cnosf.franceolympique.com/comite-national-olympique-2026-bilan", today),
    ])
    src = {
        "id": "cnosf",
        "url": "https://cnosf.franceolympique.com/sitemap.xml",
        "category": "communiques",
        "url_filter": ["/"],
    }
    with patch.object(hg, "fetch_text", return_value=sitemap):
        items = hg._from_sitemap_generic(src)
    assert len(items) == 2
