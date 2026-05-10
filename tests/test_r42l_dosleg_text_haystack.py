"""Tests R42-L (2026-05-10) — Extension du matching aux dossiers
législatifs Sénat via fetch de la page texte intégral `/leg/<slug>.html`.

Couvre :
- `_build_dossier_leg_url` : conversion `dossier-legislatif/<slug>.html`
  → `leg/<slug>.html` pour PPL/PJL modernes ; no-op si format atypique.
- `_fetch_senat_dossier_text_haystack` : graceful degradation symétrique
  à `_fetch_senat_rap_haystack` (R42-B).
- Cache 404 dédié `_RAP_DOSSIER_TEXT_CACHE` (séparé du cache rapports
  R42-B-bis) — skip immédiat / mark on 404 / no-mark sur autres erreurs
  / persistance / load.

Régression cible : PPL 25-566 « Repenser l'agencification » dont le
texte intégral mentionne 9× « Agence nationale du sport » — doit
matcher avec haystack_body, ratait avant R42-L (titre + thèmes seuls).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _reset_dossier_text_404_cache_per_test():
    """Isole le module-level cache entre tests."""
    from src.sources import senat as senat_mod
    from pathlib import Path as _P
    senat_mod._RAP_DOSSIER_TEXT_CACHE = None
    senat_mod._RAP_DOSSIER_TEXT_DIRTY = False
    senat_mod._RAP_DOSSIER_TEXT_CACHE_PATH = _P("data/senat_dossier_text_404.json")
    yield
    senat_mod._RAP_DOSSIER_TEXT_CACHE = None
    senat_mod._RAP_DOSSIER_TEXT_DIRTY = False
    senat_mod._RAP_DOSSIER_TEXT_CACHE_PATH = _P("data/senat_dossier_text_404.json")


# ----------------------------------- _build_dossier_leg_url
def test_build_leg_url_ppl_moderne():
    from src.sources.senat import _build_dossier_leg_url
    out = _build_dossier_leg_url(
        "http://www.senat.fr/dossier-legislatif/ppl25-566.html"
    )
    assert out == "https://www.senat.fr/leg/ppl25-566.html"


def test_build_leg_url_pjl_moderne():
    from src.sources.senat import _build_dossier_leg_url
    out = _build_dossier_leg_url(
        "https://www.senat.fr/dossier-legislatif/pjl24-100.html"
    )
    assert out == "https://www.senat.fr/leg/pjl24-100.html"


def test_build_leg_url_format_ancien_sxxxxx():
    """Vieux format `s78790566.html` (Sénat session 1978-1979) :
    le regex le capte mais on tolère ; le fetch retournera 404 et
    sera caché."""
    from src.sources.senat import _build_dossier_leg_url
    out = _build_dossier_leg_url(
        "http://www.senat.fr/dossier-legislatif/s78790566.html"
    )
    assert out == "https://www.senat.fr/leg/s78790566.html"


def test_build_leg_url_format_atypique_renvoie_vide():
    from src.sources.senat import _build_dossier_leg_url
    assert _build_dossier_leg_url("https://www.senat.fr/rap/r24-006/r24-006_mono.html") == ""
    assert _build_dossier_leg_url("https://example.com/foo") == ""


def test_build_leg_url_vide():
    from src.sources.senat import _build_dossier_leg_url
    assert _build_dossier_leg_url("") == ""
    assert _build_dossier_leg_url(None) == ""  # type: ignore[arg-type]


# ----------------------------------- _fetch_senat_dossier_text_haystack
def test_fetch_haystack_skip_si_cache_404(monkeypatch, tmp_path):
    from src.sources import senat as senat_mod
    monkeypatch.setattr(senat_mod, "_RAP_DOSSIER_TEXT_CACHE_PATH", tmp_path / "404.json")
    senat_mod._RAP_DOSSIER_TEXT_CACHE = {"ppl25-566"}

    def fetch_should_not_be_called(url):
        raise AssertionError(f"fetch_text appelé sur slug en cache : {url}")

    monkeypatch.setattr(senat_mod, "fetch_text", fetch_should_not_be_called)
    out = senat_mod._fetch_senat_dossier_text_haystack(
        "https://www.senat.fr/dossier-legislatif/ppl25-566.html"
    )
    assert out == ""


def test_fetch_haystack_marks_on_404(monkeypatch, tmp_path):
    from src.sources import senat as senat_mod
    monkeypatch.setattr(senat_mod, "_RAP_DOSSIER_TEXT_CACHE_PATH", tmp_path / "404.json")
    senat_mod._RAP_DOSSIER_TEXT_CACHE = set()

    def fake_fetch_404(url):
        raise RuntimeError("Client error '404 Not Found'")

    monkeypatch.setattr(senat_mod, "fetch_text", fake_fetch_404)
    senat_mod._fetch_senat_dossier_text_haystack(
        "https://www.senat.fr/dossier-legislatif/ppl25-600.html"
    )
    assert "ppl25-600" in senat_mod._RAP_DOSSIER_TEXT_CACHE
    assert senat_mod._RAP_DOSSIER_TEXT_DIRTY is True


def test_fetch_haystack_no_mark_on_timeout(monkeypatch, tmp_path):
    """Erreurs réseau non-404 ne caching PAS — on retentera."""
    from src.sources import senat as senat_mod
    monkeypatch.setattr(senat_mod, "_RAP_DOSSIER_TEXT_CACHE_PATH", tmp_path / "404.json")
    senat_mod._RAP_DOSSIER_TEXT_CACHE = set()

    def fake_timeout(url):
        raise RuntimeError("Connection timeout")

    monkeypatch.setattr(senat_mod, "fetch_text", fake_timeout)
    senat_mod._fetch_senat_dossier_text_haystack(
        "https://www.senat.fr/dossier-legislatif/ppl25-700.html"
    )
    assert "ppl25-700" not in senat_mod._RAP_DOSSIER_TEXT_CACHE


def test_fetch_haystack_extrait_main(monkeypatch, tmp_path):
    """Fetch OK + extraction du <main> retourne le texte du dossier."""
    from src.sources import senat as senat_mod
    monkeypatch.setattr(senat_mod, "_RAP_DOSSIER_TEXT_CACHE_PATH", tmp_path / "404.json")
    senat_mod._RAP_DOSSIER_TEXT_CACHE = set()

    fake_html = """
    <html><body>
      <nav>Menu Sénat</nav>
      <main>
        Article 13 — Les activités de l'Agence nationale du sport relevant
        du sport de haut niveau et de la haute performance sportive sont
        transférées à l'État. L'ANS est dissoute.
      </main>
      <footer>Footer Sénat</footer>
    </body></html>
    """

    def fake_fetch(url):
        return fake_html

    monkeypatch.setattr(senat_mod, "fetch_text", fake_fetch)
    out = senat_mod._fetch_senat_dossier_text_haystack(
        "https://www.senat.fr/dossier-legislatif/ppl25-566.html",
        max_chars=10000,
    )
    assert "Agence nationale du sport" in out
    assert "ANS est dissoute" in out
    # nav et footer exclus
    assert "Menu Sénat" not in out
    assert "Footer Sénat" not in out


def test_fetch_haystack_truncate(monkeypatch, tmp_path):
    from src.sources import senat as senat_mod
    monkeypatch.setattr(senat_mod, "_RAP_DOSSIER_TEXT_CACHE_PATH", tmp_path / "404.json")
    senat_mod._RAP_DOSSIER_TEXT_CACHE = set()

    fake_html = "<html><body><main>" + ("a" * 100000) + "</main></body></html>"

    def fake_fetch(url):
        return fake_html

    monkeypatch.setattr(senat_mod, "fetch_text", fake_fetch)
    out = senat_mod._fetch_senat_dossier_text_haystack(
        "https://www.senat.fr/dossier-legislatif/ppl25-566.html",
        max_chars=5000,
    )
    assert len(out) == 5000


def test_fetch_haystack_url_atypique_ne_fetche_pas(monkeypatch, tmp_path):
    """URL non reconnue par le regex → return "" sans fetch."""
    from src.sources import senat as senat_mod
    monkeypatch.setattr(senat_mod, "_RAP_DOSSIER_TEXT_CACHE_PATH", tmp_path / "404.json")
    senat_mod._RAP_DOSSIER_TEXT_CACHE = set()

    def fetch_should_not_be_called(url):
        raise AssertionError(f"fetch_text appelé sur URL atypique : {url}")

    monkeypatch.setattr(senat_mod, "fetch_text", fetch_should_not_be_called)
    out = senat_mod._fetch_senat_dossier_text_haystack(
        "https://example.com/something/random.html"
    )
    assert out == ""


# ----------------------------------- Persistance cache
def test_persist_writes_json_when_dirty(monkeypatch, tmp_path):
    from src.sources import senat as senat_mod
    cache_path = tmp_path / "404.json"
    monkeypatch.setattr(senat_mod, "_RAP_DOSSIER_TEXT_CACHE_PATH", cache_path)
    senat_mod._RAP_DOSSIER_TEXT_CACHE = {"ppl25-700", "pjl24-100"}
    senat_mod._RAP_DOSSIER_TEXT_DIRTY = True

    senat_mod._persist_dossier_text_404_cache()

    import json
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert data == {"slugs_404": ["pjl24-100", "ppl25-700"]}


def test_persist_noop_si_clean(monkeypatch, tmp_path):
    from src.sources import senat as senat_mod
    cache_path = tmp_path / "404.json"
    monkeypatch.setattr(senat_mod, "_RAP_DOSSIER_TEXT_CACHE_PATH", cache_path)
    senat_mod._RAP_DOSSIER_TEXT_CACHE = {"ppl25-700"}
    senat_mod._RAP_DOSSIER_TEXT_DIRTY = False

    senat_mod._persist_dossier_text_404_cache()
    assert not cache_path.exists()


def test_load_from_existing(monkeypatch, tmp_path):
    from src.sources import senat as senat_mod
    cache_path = tmp_path / "404.json"
    cache_path.write_text(
        '{"slugs_404": ["ppl25-100", "pjl24-200"]}', encoding="utf-8"
    )
    monkeypatch.setattr(senat_mod, "_RAP_DOSSIER_TEXT_CACHE_PATH", cache_path)

    cache = senat_mod._load_dossier_text_404_cache()
    assert cache == {"ppl25-100", "pjl24-200"}


def test_load_corrupt_json_returns_empty(monkeypatch, tmp_path):
    from src.sources import senat as senat_mod
    cache_path = tmp_path / "404.json"
    cache_path.write_text("not-json{{{", encoding="utf-8")
    monkeypatch.setattr(senat_mod, "_RAP_DOSSIER_TEXT_CACHE_PATH", cache_path)
    assert senat_mod._load_dossier_text_404_cache() == set()
