"""R42-BQ — Retry tenacity sur le proxy CF + handler CCOMPTES sport-filtré.

Contexte : suite à R42-BO, 2 sources (INJEP publications sport, Cour des
comptes) renvoyaient parfois HTTP 522 (timeout origine → CF Worker) sous
charge serveur. Le 522 est transitoire — un retry 2-3 sec plus tard
repasse en 200. On ajoute donc 3 tentatives tenacity dans
`_fetch_bytes_via_proxy`, en réutilisant `_is_retryable` (5xx/timeouts
oui, 4xx non — refus volontaire de l'origine comme INSEP 418).

Nouvelle source `ccomptes_publications_sport` qui scrape la page Cour des
comptes filtrée par thématique « Famille, handicap, sport et jeunes »
(id 17182). Plus exhaustive que le RSS global pour les rapports sport
au titre peu évident.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import httpx
import pytest
import tenacity

from src.sources._common import _fetch_bytes_via_proxy, _is_retryable
from src.sources import html_generic


# ---------------------------------------------------------------------------
# Retry tenacity sur proxy CF — 522 doit être retried, 418 non
# ---------------------------------------------------------------------------

@pytest.fixture
def cf_env(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_PROXY_URL", "https://veille-proxy.example.workers.dev")
    monkeypatch.setenv("CLOUDFLARE_PROXY_TOKEN", "test-token-123")


def _mock_response(content: bytes = b"ok", status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.content = content
    r.is_success = (200 <= status < 400)
    r.reason_phrase = "OK" if status == 200 else "Error"
    r.url = "https://veille-proxy.example.workers.dev/?url=..."
    if r.is_success:
        r.raise_for_status = MagicMock()
    else:
        def _raise():
            req = MagicMock()
            resp = MagicMock()
            resp.status_code = status
            raise httpx.HTTPStatusError("err", request=req, response=resp)
        r.raise_for_status = _raise
    return r


def test_proxy_retry_succeeds_after_522(cf_env):
    """3 tentatives : 522 → 522 → 200. Doit retourner le contenu OK."""
    call_count = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, headers=None):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return _mock_response(b"", status=522)
            return _mock_response(b"<rss>ok</rss>", status=200)

    with patch("src.sources._common._client", FakeClient):
        result = _fetch_bytes_via_proxy("https://www.injep.fr/sport/")
    assert result == b"<rss>ok</rss>"
    assert call_count["n"] == 3


def test_proxy_no_retry_on_418(cf_env):
    """INSEP 418 (refus volontaire) ne doit PAS être retried — gain de temps,
    pas de spam au site."""
    call_count = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, headers=None):
            call_count["n"] += 1
            return _mock_response(b"teapot", status=418)

    with patch("src.sources._common._client", FakeClient):
        with pytest.raises(httpx.HTTPStatusError):
            _fetch_bytes_via_proxy("https://www.insep.fr/fr/actualites.xml")
    assert call_count["n"] == 1


def test_proxy_retry_gives_up_after_3_attempts(cf_env):
    """Si 522 persistent sur les 3 tentatives, on lève l'erreur."""
    call_count = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, headers=None):
            call_count["n"] += 1
            return _mock_response(b"", status=522)

    with patch("src.sources._common._client", FakeClient):
        with pytest.raises(tenacity.RetryError):
            _fetch_bytes_via_proxy("https://www.ccomptes.fr/rss/publications")
    assert call_count["n"] == 3


# ---------------------------------------------------------------------------
# Handler CCOMPTES sport-filtré
# ---------------------------------------------------------------------------

_CCOMPTES_HTML = """
<!doctype html>
<html><body>
  <article>
    <div class="publication-card">
      <h2><a href="/fr/publications/le-comite-paris-2024">Le Comité d'organisation des Jeux olympiques et paralympiques de Paris 2024</a></h2>
      <time datetime="2026-05-06">6 mai 2026</time>
    </div>
  </article>
  <article>
    <div class="publication-card">
      <h2><a href="/fr/publications/federation-francaise-de-rugby">Fédération française de rugby</a></h2>
      <time datetime="2025-11-15">15 novembre 2025</time>
    </div>
  </article>
  <article>
    <div class="publication-card">
      <h2><a href="/fr/publications/ladapt-handicap">LADAPT — insertion personnes handicapées</a></h2>
      <time datetime="2026-02-06">6 février 2026</time>
    </div>
  </article>
  <!-- liens hors /publications/ : nav, footer, etc. — doivent être ignorés -->
  <h2><a href="/fr/contact">Contact</a></h2>
  <h2><a href="/fr/publications/">Toutes les publications</a></h2>
</body></html>
"""


def test_ccomptes_publications_html_extracts_titles_and_urls():
    """Parse les `<h2><a href='/fr/publications/...'>` avec dates."""
    src = {
        "id": "ccomptes_publications_sport",
        "category": "communiques",
        "url": "https://www.ccomptes.fr/fr/publications?f%5B0%5D=institution%3A98",
        "format": "ccomptes_publications_html",
    }
    with patch("src.sources.html_generic.fetch_text", return_value=_CCOMPTES_HTML):
        items = html_generic._from_ccomptes_publications_html(src)
    assert len(items) == 3
    titles = {it.title for it in items}
    assert "Le Comité d'organisation des Jeux olympiques et paralympiques de Paris 2024" in titles
    assert "Fédération française de rugby" in titles
    assert "LADAPT — insertion personnes handicapées" in titles
    # Pas de Contact (hors /publications/), pas de "Toutes les publications" (titre trop court)
    assert "Contact" not in titles


def test_ccomptes_publications_html_extracts_dates():
    """`<time datetime>` à proximité du h2 → published_at."""
    src = {
        "id": "ccomptes_publications_sport",
        "category": "communiques",
        "url": "https://www.ccomptes.fr/fr/publications",
        "format": "ccomptes_publications_html",
    }
    with patch("src.sources.html_generic.fetch_text", return_value=_CCOMPTES_HTML):
        items = html_generic._from_ccomptes_publications_html(src)
    by_title = {it.title: it for it in items}
    paris = by_title["Le Comité d'organisation des Jeux olympiques et paralympiques de Paris 2024"]
    rugby = by_title["Fédération française de rugby"]
    assert paris.published_at is not None
    assert paris.published_at.year == 2026 and paris.published_at.month == 5
    assert rugby.published_at is not None
    assert rugby.published_at.year == 2025 and rugby.published_at.month == 11


def test_ccomptes_publications_html_chamber_default():
    """Chamber par défaut : Cour des comptes (cohérent avec _chamber()
    mapping ccomptes.fr → CourComptes)."""
    src = {
        "id": "ccomptes_publications_sport",
        "category": "communiques",
        "url": "https://www.ccomptes.fr/fr/publications",
        "format": "ccomptes_publications_html",
    }
    with patch("src.sources.html_generic.fetch_text", return_value=_CCOMPTES_HTML):
        items = html_generic._from_ccomptes_publications_html(src)
    assert all(it.chamber == "Cour des comptes" for it in items)


def test_ccomptes_publications_html_handles_fetch_error():
    """Soft-fail : si fetch lève, on retourne [] sans crash."""
    src = {
        "id": "ccomptes_publications_sport",
        "category": "communiques",
        "url": "https://www.ccomptes.fr/fr/publications",
        "format": "ccomptes_publications_html",
        "proxy": "cloudflare",
    }
    with patch("src.sources.html_generic.fetch_text",
               side_effect=httpx.ConnectError("timeout")):
        items = html_generic._from_ccomptes_publications_html(src)
    assert items == []


def test_ccomptes_publications_html_routed_via_fetch_source():
    """fetch_source dispatch sur format=`ccomptes_publications_html`."""
    src = {
        "id": "ccomptes_publications_sport",
        "category": "communiques",
        "url": "https://www.ccomptes.fr/fr/publications",
        "format": "ccomptes_publications_html",
    }
    with patch("src.sources.html_generic.fetch_text", return_value=_CCOMPTES_HTML):
        items = html_generic.fetch_source(src)
    assert len(items) == 3
