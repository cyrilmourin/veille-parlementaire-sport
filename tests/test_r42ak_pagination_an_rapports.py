"""Tests R42-AK — pagination des listings AN documents parlementaires.

L'AN expose `?type=X&legis=17&offset=N&limit=N` (le param `&page=N` est
ignoré). On itère offset=0, page_size, 2*page_size, … jusqu'à `max_pages`
ou jusqu'à ce qu'une page n'apporte aucun nouveau data-id (défense si
l'AN renvoie en boucle la même top 150).

Ce module valide :
- `_paginate_url` construit correctement les URLs avec offset.
- `fetch_source` boucle jusqu'à max_pages, dédup les data_id.
- Stop précoce si une page n'apporte aucun nouveau item.
- Backward-compat max_pages=1 (1 seul fetch).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.sources.assemblee_rapports import (
    _paginate_url,
    fetch_source,
)


# ---------------------------------------------------------------------------
# _paginate_url
# ---------------------------------------------------------------------------

def test_paginate_url_offset_zero_renvoie_url_inchangee():
    """offset=0 sur une URL sans offset existant → no-op."""
    url = "https://www2.assemblee-nationale.fr/documents/liste?type=rapports&legis=17"
    assert _paginate_url(url, offset=0, limit=150) == url


def test_paginate_url_offset_non_zero_ajoute_params():
    url = "https://www2.assemblee-nationale.fr/documents/liste?type=rapports&legis=17"
    out = _paginate_url(url, offset=150, limit=150)
    assert "offset=150" in out
    assert "limit=150" in out
    # Préserve les params existants
    assert "type=rapports" in out
    assert "legis=17" in out


def test_paginate_url_replace_offset_existant():
    """Si l'URL contient déjà offset/limit, on les remplace, pas dupliqués."""
    url = "https://x/liste?type=rapports&offset=0&limit=150"
    out = _paginate_url(url, offset=300, limit=150)
    assert out.count("offset=") == 1
    assert "offset=300" in out
    assert "offset=0" not in out


# ---------------------------------------------------------------------------
# fetch_source : pagination effective
# ---------------------------------------------------------------------------

def _page_html(data_ids: list[str]) -> bytes:
    """Construit un HTML AN-like avec une liste de <li data-id="...">."""
    lis = "\n".join(
        f'<li data-id="{did}">'
        f'<span class="heure">Mis en ligne lundi 1 mars 2026 à 10h00</span>'
        f'<h3>Doc {did}</h3>'
        f'<a href="/dyn/17/dossiers/x">Dossier</a>'
        f'</li>'
        for did in data_ids
    )
    return f"<html><body><ul>{lis}</ul></body></html>".encode("utf-8")


def test_fetch_source_pagine_jusqua_max_pages():
    """3 pages distinctes → 3 fetches + 3*N items."""
    calls = []

    def _fake_fetch(url, **kw):
        calls.append(url)
        # Génère 3 data_ids différents par page
        idx = len(calls)
        return _page_html([
            f"OMC_RAPPANR5L17B{idx}001",
            f"OMC_RAPPANR5L17B{idx}002",
            f"OMC_RAPPANR5L17B{idx}003",
        ])

    with patch("src.sources.assemblee_rapports.fetch_bytes", side_effect=_fake_fetch), \
         patch("src.sources.assemblee_rapports._fetch_pdf_haystack", return_value=""):
        items = fetch_source({
            "id": "an_rapports",
            "url": "https://x/liste?type=rapports&legis=17",
            "category": "communiques",
            "max_pages": 3,
            "page_size": 150,
        })

    assert len(calls) == 3
    # 1ère URL sans offset, 2e avec offset=150, 3e avec offset=300
    assert "offset=" not in calls[0]
    assert "offset=150" in calls[1]
    assert "offset=300" in calls[2]
    assert len(items) == 9  # 3 par page × 3 pages


def test_fetch_source_stop_si_aucun_nouveau():
    """Si la page 2 retourne les mêmes data_id que la page 1, on s'arrête.
    Défense contre AN ignorant le param offset (cas réel observé sur
    `&page=N`)."""
    calls = []
    repeated_ids = ["OMC_RAPPANR5L17B2396", "OMC_RAPPANR5L17B2500"]

    def _fake_fetch(url, **kw):
        calls.append(url)
        return _page_html(repeated_ids)

    with patch("src.sources.assemblee_rapports.fetch_bytes", side_effect=_fake_fetch), \
         patch("src.sources.assemblee_rapports._fetch_pdf_haystack", return_value=""):
        items = fetch_source({
            "id": "an_rapports",
            "url": "https://x/liste",
            "category": "communiques",
            "max_pages": 5,
            "page_size": 150,
        })

    # 2 fetches : la 1ère pose 2 items, la 2e n'apporte rien → arrêt.
    assert len(calls) == 2
    assert len(items) == 2


def test_fetch_source_backward_compat_max_pages_1():
    """Sans config max_pages (défaut 1) : un seul fetch, comportement R42-AJ."""
    calls = []

    def _fake_fetch(url, **kw):
        calls.append(url)
        return _page_html([f"OMC_RAPPANR5L17B0001", "OMC_RAPPANR5L17B0002"])

    with patch("src.sources.assemblee_rapports.fetch_bytes", side_effect=_fake_fetch), \
         patch("src.sources.assemblee_rapports._fetch_pdf_haystack", return_value=""):
        items = fetch_source({
            "id": "an_rapports",
            "url": "https://x/liste",
            "category": "communiques",
            # max_pages absent → défaut 1
        })

    assert len(calls) == 1
    assert len(items) == 2


def test_fetch_source_stop_si_page_vide():
    """Page sans aucun data-id parsable → arrêt sans warning catastrophique."""
    def _fake_fetch(url, **kw):
        return b"<html><body><p>Pas de docs</p></body></html>"

    with patch("src.sources.assemblee_rapports.fetch_bytes", side_effect=_fake_fetch), \
         patch("src.sources.assemblee_rapports._fetch_pdf_haystack", return_value=""):
        items = fetch_source({
            "id": "an_rapports",
            "url": "https://x/liste",
            "category": "communiques",
            "max_pages": 5,
            "page_size": 150,
        })

    assert items == []
