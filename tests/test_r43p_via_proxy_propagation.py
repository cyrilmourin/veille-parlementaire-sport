"""Tests R43-P (2026-05-18) — Propagation du flag `via_proxy` côté
enrichissement meta description.

Bug constaté dans la revue logs R43 du 18/05/2026 : `min_education`
extrait correctement la home `/presse` via le proxy Cloudflare (HTTP 200)
mais le fetch d'enrichissement meta sur la page d'article `/espace-presse`
repassait en `curl_cffi` direct → HTTP 403 (WAF education.gouv.fr).

Fix : `_enrich_with_meta` accepte désormais `via_proxy: bool` et le
propage à `fetch_text()`. Les call-sites de `_from_html_listing` et
`_from_sitemap` lisent `src.get("proxy") == "cloudflare"` et passent
le flag.

Garde-fou : si quelqu'un ajoute un 3e call-site dans le futur, il doit
penser à passer `via_proxy` pour ne pas reproduire le bug.
"""
from __future__ import annotations

import inspect
from unittest.mock import patch, MagicMock

import pytest

from src.sources import html_generic


def test_r43p_enrich_with_meta_accepte_via_proxy():
    """Le helper d'enrichissement DOIT accepter le kwarg `via_proxy`.

    Cas d'usage : source avec `proxy: cloudflare` (ex. min_education) →
    quand on appelle `_enrich_with_meta` pour récupérer la meta description
    des pages d'articles, ces requêtes DOIVENT aussi passer par le proxy.
    """
    sig = inspect.signature(html_generic._enrich_with_meta)
    assert "via_proxy" in sig.parameters, (
        "_enrich_with_meta doit accepter `via_proxy` (R43-P) — sinon les "
        "fetch_meta secondaires bypassent le proxy et tombent en 403."
    )


def test_r43p_enrich_with_meta_propage_via_proxy_a_fetch_text():
    """Si on appelle `_enrich_with_meta(..., via_proxy=True)`, chaque
    `fetch_text()` interne DOIT recevoir `via_proxy=True`."""
    from src.sources.html_generic import Item

    items = [
        Item(
            source_id="t", uid=f"u{i}", category="communiques", chamber="X",
            title=f"T{i}", url=f"https://example.com/{i}",
            published_at=None, summary="",
        )
        for i in range(2)
    ]
    with patch("src.sources.html_generic.fetch_text") as ft:
        ft.return_value = '<meta name="description" content="x"*40>'
        html_generic._enrich_with_meta(
            items, impersonate=True, via_proxy=True, limit=2,
        )
    # Tous les appels fetch_text doivent inclure via_proxy=True
    for call in ft.call_args_list:
        kwargs = call.kwargs
        assert kwargs.get("via_proxy") is True, (
            f"fetch_text appelé sans via_proxy=True dans _enrich_with_meta : "
            f"args={call.args}, kwargs={kwargs}"
        )


def test_r43p_enrich_with_meta_via_proxy_defaut_false():
    """Sans flag explicite, `via_proxy` reste à `False` — pour les
    sources sans `proxy: cloudflare`, comportement inchangé."""
    from src.sources.html_generic import Item

    items = [Item(
        source_id="t", uid="u1", category="communiques", chamber="X",
        title="T", url="https://example.com/a", published_at=None, summary="",
    )]
    with patch("src.sources.html_generic.fetch_text") as ft:
        ft.return_value = ""
        html_generic._enrich_with_meta(items, impersonate=False, limit=1)
    # via_proxy doit être présent et False
    kwargs = ft.call_args_list[0].kwargs
    assert kwargs.get("via_proxy") is False
