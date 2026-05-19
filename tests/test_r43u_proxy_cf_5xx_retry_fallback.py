"""Tests R43-U (2026-05-19) — Retry étendu 3→5 essais sur le proxy CF
+ fallback `_fetch_bytes_impersonate` si toutes les tentatives échouent
avec un code "upstream timeout" Cloudflare (522/523/524).

Constat (revue logs run 19/05 05:47Z) : ccomptes.fr/rss/publications
échoue 3/3 essais via le worker CF avec HTTP 522, alors qu'il répond
en < 1s en direct depuis n'importe quelle machine. Le worker CF
hébergé sur PoP US (`CF-RAY 9fe0ce8999fba63b-SJC`) semble parfois
throttlé côté origine, sans pattern temporel évident.

Approche minimale (zéro modif worker JS, zéro redéploiement Cyril) :
1. Bumper retry pipeline 3 → 5 essais (backoff 3-25s)
2. Si malgré 5 essais on a un 522/523/524, tenter une seule fois
   `_fetch_bytes_impersonate(url)` (curl_cffi direct). Si ça passe,
   on a la réponse. Sinon on remonte l'erreur d'origine.

Couplé à R43-T (alerte FORMAT_DRIFT après 3 runs consécutifs à 0)
qui filtre déjà les blips, le bruit dans le digest devrait disparaître.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest


def _make_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Helper : crée une HTTPStatusError avec un code donné."""
    req = httpx.Request("GET", "https://example.com")
    resp = httpx.Response(status_code, request=req)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=req,
        response=resp,
    )


def test_r43u_retry_attempts_bumped_to_5():
    """Garde-fou config : le décorateur retry de `_fetch_bytes_via_proxy`
    doit autoriser 5 tentatives (vs 3 avant R43-U). Sinon les blips
    transitoires ccomptes.fr échouent toujours."""
    from src.sources import _common

    # tenacity expose la conf via `retry.stop` du décorateur
    fn = _common._fetch_bytes_via_proxy
    # Le décorateur tenacity wrappe la fonction, on accède à .retry.stop
    assert hasattr(fn, "retry"), "Décorateur tenacity attendu"
    stop_after = fn.retry.stop
    # stop_after_attempt expose `max_attempt_number`
    assert stop_after.max_attempt_number == 5, (
        f"Attendu 5 tentatives R43-U, obtenu {stop_after.max_attempt_number}"
    )


def test_r43u_is_retryable_proxy_5xx_yes():
    """5xx (y compris 522/523/524 CF) doivent être retryables."""
    from src.sources._common import _is_retryable_proxy

    for code in (500, 502, 503, 504, 522, 523, 524):
        assert _is_retryable_proxy(_make_status_error(code)), (
            f"HTTP {code} doit être retryable"
        )


def test_r43u_is_retryable_proxy_4xx_no():
    """4xx ne doivent PAS être retryables (refus volontaire origine)."""
    from src.sources._common import _is_retryable_proxy

    for code in (400, 401, 403, 404, 418, 429):
        assert not _is_retryable_proxy(_make_status_error(code)), (
            f"HTTP {code} NE doit PAS être retryable (côté client)"
        )


def test_r43u_is_retryable_proxy_network_error_yes():
    """Erreurs réseau (ConnectError, TimeoutException) doivent être retryables."""
    from src.sources._common import _is_retryable_proxy

    assert _is_retryable_proxy(httpx.ConnectError("connection refused"))
    assert _is_retryable_proxy(httpx.ReadTimeout("timeout"))


def test_r43u_fallback_impersonate_sur_522(monkeypatch):
    """Si `_fetch_bytes_via_proxy` échoue avec un 522 même après les 5
    tentatives, `fetch_bytes(via_proxy=True)` doit tenter un fallback
    `_fetch_bytes_impersonate(url)` avant d'abandonner. Si ce fallback
    réussit, on récupère le contenu."""
    from src.sources import _common

    # Mock proxy CF qui échoue toujours en 522
    proxy_err = _make_status_error(522)

    def fail_proxy(url):
        raise proxy_err

    # Mock impersonate qui réussit avec un contenu utile
    expected_content = b"<html>OK via impersonate</html>"

    def ok_impersonate(url):
        return expected_content

    monkeypatch.setattr(_common, "_fetch_bytes_via_proxy", fail_proxy)
    monkeypatch.setattr(_common, "_fetch_bytes_impersonate", ok_impersonate)

    result = _common.fetch_bytes(
        "https://www.ccomptes.fr/rss/publications", via_proxy=True
    )
    assert result == expected_content, (
        "Fallback impersonate doit servir le contenu quand proxy CF échoue 522"
    )


def test_r43u_fallback_pas_active_sur_4xx(monkeypatch):
    """Si le proxy renvoie un 4xx (ex. 403 si token absent côté worker),
    PAS de fallback impersonate — l'erreur est volontaire côté origine.
    L'exception remonte telle quelle."""
    from src.sources import _common

    impersonate_called = []

    def fail_proxy_403(url):
        raise _make_status_error(403)

    def ok_impersonate(url):
        impersonate_called.append(url)
        return b"impersonate ne devrait pas etre appele"

    monkeypatch.setattr(_common, "_fetch_bytes_via_proxy", fail_proxy_403)
    monkeypatch.setattr(_common, "_fetch_bytes_impersonate", ok_impersonate)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        _common.fetch_bytes(
            "https://www.ccomptes.fr/rss/publications", via_proxy=True
        )
    assert exc_info.value.response.status_code == 403
    assert not impersonate_called, (
        "Fallback impersonate ne doit PAS être tenté sur 4xx"
    )


def test_r43u_fallback_ko_remonte_erreur_proxy(monkeypatch):
    """Si proxy CF échoue 522 ET fallback impersonate échoue aussi
    (ex. origine vraiment cassée OU IP GHA blacklist),
    on remonte l'erreur du proxy CF d'origine (pas celle du fallback)
    — plus parlante pour le diag (`HTTP 522` vs message curl_cffi
    générique)."""
    from src.sources import _common

    proxy_err = _make_status_error(522)

    def fail_proxy(url):
        raise proxy_err

    def fail_impersonate(url):
        raise httpx.ConnectError("ip blacklisted by origin")

    monkeypatch.setattr(_common, "_fetch_bytes_via_proxy", fail_proxy)
    monkeypatch.setattr(_common, "_fetch_bytes_impersonate", fail_impersonate)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        _common.fetch_bytes(
            "https://www.ccomptes.fr/rss/publications", via_proxy=True
        )
    # L'exception remontée doit être celle du proxy CF (522), pas la
    # ConnectError du fallback
    assert exc_info.value.response.status_code == 522


def test_r43u_cf_upstream_timeout_codes_set():
    """Garde-fou contrat : la constante `_CF_UPSTREAM_TIMEOUT_CODES`
    contient bien 522/523/524 — les 3 codes qui déclenchent le fallback."""
    from src.sources._common import _CF_UPSTREAM_TIMEOUT_CODES

    assert _CF_UPSTREAM_TIMEOUT_CODES == {522, 523, 524}
