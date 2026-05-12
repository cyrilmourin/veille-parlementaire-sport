"""Tests R42-BO — fetch via Cloudflare Worker proxy pour les sources
WAF-bloquées côté GHA.

Vérifie :
- `fetch_text(via_proxy=True)` route via le worker si env vars définies
- Fallback fetch direct si env vars absentes (mode dégradé)
- URL cible passée correctement encodée
- Header X-Proxy-Token transmis
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from src.sources._common import fetch_text, _fetch_bytes_via_proxy


@pytest.fixture
def cf_env(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_PROXY_URL", "https://veille-proxy.example.workers.dev")
    monkeypatch.setenv("CLOUDFLARE_PROXY_TOKEN", "test-token-123")


@pytest.fixture
def no_cf_env(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_PROXY_URL", raising=False)
    monkeypatch.delenv("CLOUDFLARE_PROXY_TOKEN", raising=False)


def _mock_response(content: bytes = b"<rss/>", status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.content = content
    r.raise_for_status = MagicMock()
    return r


# ---------------------------------------------------------------------------
# Routage : si env vars définies → via proxy ; sinon fallback direct
# ---------------------------------------------------------------------------

def test_via_proxy_route_through_worker_when_env_set(cf_env):
    """fetch_bytes(via_proxy=True) appelle le worker via le client httpx."""
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _mock_response(b"<rss>proxied</rss>")

    with patch("src.sources._common._client", FakeClient):
        result = fetch_text("https://www.info.gouv.fr/rss", via_proxy=True)
    assert result == "<rss>proxied</rss>"
    # URL : worker.example/?url=<encoded target>
    assert captured["url"].startswith(
        "https://veille-proxy.example.workers.dev/?url=https%3A%2F%2Fwww.info.gouv.fr%2Frss"
    )
    # Header token transmis
    assert captured["headers"].get("X-Proxy-Token") == "test-token-123"


def test_via_proxy_falls_back_when_env_missing(no_cf_env):
    """Sans env vars, via_proxy=True bascule sur fetch direct (httpx)."""
    with patch("src.sources._common._fetch_bytes_httpx",
               return_value=b"<rss>direct</rss>") as direct:
        result = fetch_text("https://www.info.gouv.fr/rss", via_proxy=True)
    assert result == "<rss>direct</rss>"
    direct.assert_called_once_with("https://www.info.gouv.fr/rss")


def test_via_proxy_false_no_proxy_call(cf_env):
    """via_proxy=False → fetch direct standard même si env vars présentes."""
    with patch("src.sources._common._fetch_bytes_httpx",
               return_value=b"<rss>direct</rss>") as direct:
        result = fetch_text("https://example.com/feed")
    direct.assert_called_once()
    assert result == "<rss>direct</rss>"


def test_impersonate_priorite_via_proxy(cf_env):
    """Si via_proxy=True → proxy prime sur impersonate (les WAF stricts
    bloquent au TCP, pas au TLS — impersonate inutile)."""
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get(self, url, headers=None):
            captured["url"] = url
            return _mock_response(b"proxied")

    with patch("src.sources._common._client", FakeClient), \
         patch("src.sources._common._fetch_bytes_impersonate") as imp:
        fetch_text("https://www.info.gouv.fr/rss", impersonate=True, via_proxy=True)
    # Le proxy a été appelé, pas l'impersonate
    assert "veille-proxy" in captured["url"]
    imp.assert_not_called()


def test_via_proxy_trailing_slash_stripped(cf_env):
    """URL proxy avec trailing slash → on le strip pour construction."""
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get(self, url, headers=None):
            captured["url"] = url
            return _mock_response()

    import os
    os.environ["CLOUDFLARE_PROXY_URL"] = "https://veille-proxy.example.workers.dev/"
    try:
        with patch("src.sources._common._client", FakeClient):
            fetch_text("https://www.info.gouv.fr/rss", via_proxy=True)
        # Pas de double slash dans l'URL construite
        assert "workers.dev//" not in captured["url"]
        assert "workers.dev/?url=" in captured["url"]
    finally:
        os.environ.pop("CLOUDFLARE_PROXY_URL", None)


# ---------------------------------------------------------------------------
# Intégration html_generic : lecture du flag `proxy: cloudflare` côté YAML
# ---------------------------------------------------------------------------

def test_proxy_cloudflare_yaml_flag_route_via_proxy(cf_env):
    """Une source avec `proxy: cloudflare` dans son YAML appelle
    fetch_bytes(via_proxy=True)."""
    from src.sources import html_generic
    captured = {}

    def _fake_fetch_bytes(url, impersonate=False, via_proxy=False):
        captured["via_proxy"] = via_proxy
        captured["url"] = url
        return b"<rss><channel></channel></rss>"

    with patch("src.sources.html_generic.fetch_bytes", side_effect=_fake_fetch_bytes):
        html_generic._from_rss_generic({
            "id": "test_rss",
            "category": "communiques",
            "url": "https://www.info.gouv.fr/rss",
            "format": "rss",
            "proxy": "cloudflare",
        })
    assert captured["via_proxy"] is True


def test_proxy_absent_no_via_proxy_call(cf_env):
    """Une source sans `proxy:` ne passe pas par le worker."""
    from src.sources import html_generic
    captured = {}

    def _fake_fetch_bytes(url, impersonate=False, via_proxy=False):
        captured["via_proxy"] = via_proxy
        return b"<rss><channel></channel></rss>"

    with patch("src.sources.html_generic.fetch_bytes", side_effect=_fake_fetch_bytes):
        html_generic._from_rss_generic({
            "id": "test_rss",
            "category": "communiques",
            "url": "https://example.com/feed",
            "format": "rss",
        })
    assert captured["via_proxy"] is False
