"""Tests sur la politique de retry et de logging de `_common.fetch_bytes_*`.

Régressions couvertes :

* R11d — `an_amendements` renvoyait 404 silencieusement pendant ~24h. Le
  retry tenacity aggravait la latence (3×16s) sans ajouter d'info. On
  vérifie maintenant que :
  1. un 4xx ne déclenche PAS de retry (économie de latence + pas de bruit),
  2. un 5xx déclenche bien un retry (transitoire serveur),
  3. chaque 4xx/5xx émet un log.error explicite avec code + URL,
  4. `run_all` émet un bloc WARNING récapitulatif listant les sources KO
     et les sources à 0 item.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.sources import _common  # noqa: E402


class _FakeResp:
    """Réponse httpx minimaliste pour mock sans sortir sur le réseau."""

    def __init__(self, status_code: int, url: str = "https://example.test/x",
                 content: bytes = b"", reason: str = ""):
        self.status_code = status_code
        self.url = url
        self.content = content
        self.reason_phrase = reason or (
            "OK" if 200 <= status_code < 300 else "Error"
        )
        self.is_success = 200 <= status_code < 300

    def raise_for_status(self):
        if not self.is_success:
            req = httpx.Request("GET", self.url)
            resp = httpx.Response(self.status_code, request=req)
            raise httpx.HTTPStatusError(
                f"{self.status_code} {self.reason_phrase}",
                request=req, response=resp,
            )


class _FakeClient:
    """Compteur d'appels + réponses scriptées en file."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        self.calls += 1
        if not self._responses:
            raise AssertionError("Plus de réponse scriptée mais get() appelé")
        resp = self._responses.pop(0)
        # Ré-injecte l'URL demandée pour que le log error remonte le bon
        if hasattr(resp, "url") and resp.url == "https://example.test/x":
            resp.url = url
        return resp


def _install_fake_client(monkeypatch, responses):
    """Substitue `_client(...)` pour renvoyer un client qui répond selon scénario."""
    client = _FakeClient(responses)
    monkeypatch.setattr(_common, "_client", lambda *a, **kw: client)
    return client


# ---------- Politique de retry -----------------------------------------------

def test_fetch_bytes_does_not_retry_on_404(monkeypatch, caplog):
    """Un 404 ne doit pas déclencher 3×retry (cas R11d)."""
    client = _install_fake_client(monkeypatch, [_FakeResp(404)])
    with caplog.at_level(logging.ERROR, logger="src.sources._common"):
        with pytest.raises(httpx.HTTPStatusError):
            _common.fetch_bytes.retry_with(
                stop=_common.stop_after_attempt(2)
            )("https://example.test/missing")
    # 1 seul appel HTTP, pas 2 (no retry sur 4xx)
    assert client.calls == 1, f"attendu 1 appel, vu {client.calls}"
    # log.error a bien été émis avec le code et l'URL
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, "aucun log.error sur 404"
    msg = error_records[0].getMessage()
    assert "404" in msg and "example.test/missing" in msg


def test_fetch_bytes_heavy_does_not_retry_on_403(monkeypatch, caplog):
    """403 = URL interdite, pas de retry utile."""
    client = _install_fake_client(monkeypatch, [_FakeResp(403)])
    with caplog.at_level(logging.ERROR, logger="src.sources._common"):
        with pytest.raises(httpx.HTTPStatusError):
            _common.fetch_bytes_heavy("https://example.test/forbidden")
    assert client.calls == 1


def test_fetch_bytes_heavy_retries_on_503(monkeypatch):
    """503 transitoire = mérite un retry (backoff tenacity)."""
    # 1er appel 503, 2e appel 200 OK
    client = _install_fake_client(
        monkeypatch,
        [_FakeResp(503), _FakeResp(200, content=b"ok")],
    )
    # On contourne le wait_exponential réel pour ne pas ralentir les tests
    with patch("tenacity.nap.time.sleep", lambda *a, **kw: None):
        result = _common.fetch_bytes_heavy("https://example.test/flaky")
    assert result == b"ok"
    assert client.calls == 2


# ---------- Logging explicite -----------------------------------------------

def test_fetch_bytes_logs_error_with_url_and_code(monkeypatch, caplog):
    """Le log.error doit contenir l'URL + le code HTTP pour diag rapide."""
    _install_fake_client(
        monkeypatch,
        [_FakeResp(404, reason="Not Found")],
    )
    with caplog.at_level(logging.ERROR, logger="src.sources._common"):
        with pytest.raises(httpx.HTTPStatusError):
            _common.fetch_bytes("https://data.assemblee-nationale.fr/foo.zip")
    msgs = [r.getMessage() for r in caplog.records
            if r.levelno == logging.ERROR]
    assert any("404" in m and "assemblee-nationale.fr/foo.zip" in m
               for m in msgs), f"logs : {msgs}"


# ---------- Récap run_all ---------------------------------------------------

def test_run_all_summary_flags_errored_and_empty(monkeypatch, tmp_path, caplog):
    """run_all doit émettre un bloc WARNING listant sources KO et sources
    à 0 item — visibilité accrue post-R11d.
    """
    from src import normalize

    cfg_path = tmp_path / "sources.yml"
    cfg_path.write_text(
        "g1:\n  sources:\n"
        "    - {id: src_ok, url: 'x'}\n"
        "    - {id: src_ko, url: 'x'}\n"
        "    - {id: src_empty, url: 'x'}\n",
        encoding="utf-8",
    )

    # Monkeypatch _dispatch pour renvoyer des fetchers mock par id
    def fake_ok(src):
        class _I:
            pass
        # renvoie 1 item (objet sentinelle)
        return [_I()]

    def fake_ko(src):
        raise httpx.HTTPStatusError(
            "404 Not Found",
            request=httpx.Request("GET", "https://x"),
            response=httpx.Response(404, request=httpx.Request("GET", "https://x")),
        )

    def fake_empty(src):
        return []

    def fake_dispatch(group, src):
        return {"src_ok": fake_ok, "src_ko": fake_ko,
                "src_empty": fake_empty}[src["id"]]

    monkeypatch.setattr(normalize, "_dispatch", fake_dispatch)

    with caplog.at_level(logging.WARNING, logger="src.normalize"):
        items, stats = normalize.run_all(cfg_path, parallel=1)

    # stats : src_ok=1 item, src_ko=error, src_empty=0 sans error
    assert stats["src_ok"]["fetched"] == 1 and stats["src_ok"]["error"] is None
    assert stats["src_ko"]["error"] is not None
    assert stats["src_empty"]["fetched"] == 0 and stats["src_empty"]["error"] is None

    warnings = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING]
    blob = "\n".join(warnings)
    assert "src_ko" in blob, f"src_ko absent du récap : {blob}"
    assert "src_empty" in blob, f"src_empty absent du récap : {blob}"
    assert "en erreur" in blob or "ERREUR" in blob
    assert "0 item" in blob
