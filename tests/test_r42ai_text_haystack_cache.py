"""Tests R42-AI — cache SQLite des textes intégraux dossiers législatifs.

Vérifie :
- get/put atomiques (cache miss → put → cache hit)
- TTL 14j pour les actifs, infini pour les promulgués
- Validation min 500 chars (cache empoisonné rejeté)
- Purge totale ou par source
- Stats (hits, miss_absent, miss_expired, put, put_rejected_too_short)
  par source AN / Sénat séparées
- Intégration dans _fetch_an_dossier_text_haystack et
  _fetch_senat_dossier_text_haystack : cache hit → pas de fetch live
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src import text_haystack_cache as hc


@pytest.fixture
def db_tmp(tmp_path: Path) -> Path:
    """Fichier SQLite isolé pour ces tests."""
    return tmp_path / "veille.sqlite3"


@pytest.fixture(autouse=True)
def _enable_cache(monkeypatch):
    """Active le cache pour ces tests (le conftest global le coupe)."""
    monkeypatch.delenv("VEILLE_DOSLEG_TEXT_CACHE_DISABLE", raising=False)
    hc.reset_stats()


# ----------------------------------------------------------------------------
# 1. get/put basique
# ----------------------------------------------------------------------------

def test_miss_then_put_then_hit(db_tmp: Path):
    """Cache miss → put → cache hit."""
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJLANR5L17B1") is None
    assert hc.get_stats(hc.SOURCE_AN)["miss_absent"] == 1

    text = "x" * 600  # > MIN_VALID_LEN
    assert hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJLANR5L17B1", text) is True

    got = hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJLANR5L17B1")
    assert got == text
    assert hc.get_stats(hc.SOURCE_AN)["hits"] == 1
    assert hc.get_stats(hc.SOURCE_AN)["put"] == 1


def test_ref_vide_no_op(db_tmp: Path):
    """ref vide → None / False sans toucher la DB."""
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "") is None
    assert hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "", "x" * 600) is False


def test_source_invalide_no_op(db_tmp: Path):
    """Source inconnue → no-op silencieux."""
    assert hc.get_cached_haystack(db_tmp, "wrong_source", "PRJL1") is None
    assert hc.put_cached_haystack(db_tmp, "wrong_source", "PRJL1", "x" * 600) is False


# ----------------------------------------------------------------------------
# 2. Validation cache empoisonné
# ----------------------------------------------------------------------------

def test_put_rejette_texte_trop_court(db_tmp: Path):
    """Un haystack < 500 chars n'est PAS caché (cache empoisonné suspecté)."""
    short_text = "Maintenance en cours."  # 21 chars
    assert hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1", short_text) is False
    assert hc.get_stats(hc.SOURCE_AN)["put_rejected_too_short"] == 1
    # Confirmation : rien n'a été écrit
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1") is None


def test_put_accepte_exactement_500_chars(db_tmp: Path):
    """Cas limite : 500 chars exactement → accepté."""
    text = "y" * 500
    assert hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1", text) is True


# ----------------------------------------------------------------------------
# 3. TTL
# ----------------------------------------------------------------------------

def test_ttl_expire_pour_dossier_actif(db_tmp: Path):
    """Au-delà de 14 j, un dossier ACTIF n'est plus servi du cache."""
    now0 = datetime(2026, 5, 11, 12, 0, 0)
    text = "z" * 600
    hc.put_cached_haystack(
        db_tmp, hc.SOURCE_AN, "PRJL1", text,
        is_promulgated=False, now=now0,
    )
    # 13j plus tard : encore servi
    in_window = now0 + timedelta(days=13)
    assert hc.get_cached_haystack(
        db_tmp, hc.SOURCE_AN, "PRJL1", now=in_window
    ) == text
    # 15j plus tard : expiré
    out_window = now0 + timedelta(days=15)
    assert hc.get_cached_haystack(
        db_tmp, hc.SOURCE_AN, "PRJL1", now=out_window
    ) is None
    assert hc.get_stats(hc.SOURCE_AN)["miss_expired"] == 1


def test_ttl_infini_pour_promulgue(db_tmp: Path):
    """Un dossier promulgué reste servi du cache même après 3 ans."""
    now0 = datetime(2023, 1, 1, 0, 0, 0)
    text = "w" * 600
    hc.put_cached_haystack(
        db_tmp, hc.SOURCE_AN, "PRJL_PROMULGUE", text,
        is_promulgated=True, now=now0,
    )
    way_later = now0 + timedelta(days=1200)
    assert hc.get_cached_haystack(
        db_tmp, hc.SOURCE_AN, "PRJL_PROMULGUE", now=way_later
    ) == text


# ----------------------------------------------------------------------------
# 4. Cohabitation AN / Sénat
# ----------------------------------------------------------------------------

def test_cles_an_et_senat_disjointes(db_tmp: Path):
    """Même ref entre AN et Sénat ne se mélangent pas."""
    text_an = "A" * 600
    text_senat = "S" * 600
    hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "shared_ref", text_an)
    hc.put_cached_haystack(db_tmp, hc.SOURCE_SENAT, "shared_ref", text_senat)
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "shared_ref") == text_an
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_SENAT, "shared_ref") == text_senat


def test_stats_par_source_independantes(db_tmp: Path):
    """Les compteurs AN et Sénat sont strictement isolés."""
    hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1", "x" * 600)
    hc.put_cached_haystack(db_tmp, hc.SOURCE_SENAT, "ppl25-1", "y" * 600)
    hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1")  # +1 hit AN
    hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL_absent")  # +1 miss AN
    assert hc.get_stats(hc.SOURCE_AN)["hits"] == 1
    assert hc.get_stats(hc.SOURCE_AN)["miss_absent"] == 1
    assert hc.get_stats(hc.SOURCE_SENAT)["hits"] == 0
    assert hc.get_stats(hc.SOURCE_SENAT)["miss_absent"] == 0


# ----------------------------------------------------------------------------
# 5. Purge
# ----------------------------------------------------------------------------

def test_purge_totale(db_tmp: Path):
    """purge_haystack_cache(db) vide la table."""
    hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1", "x" * 600)
    hc.put_cached_haystack(db_tmp, hc.SOURCE_SENAT, "ppl1", "y" * 600)
    n = hc.purge_haystack_cache(db_tmp)
    assert n == 2
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1") is None
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_SENAT, "ppl1") is None


def test_purge_par_source(db_tmp: Path):
    """purge_haystack_cache(db, source=...) ne supprime QUE cette source."""
    hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1", "x" * 600)
    hc.put_cached_haystack(db_tmp, hc.SOURCE_SENAT, "ppl1", "y" * 600)
    n = hc.purge_haystack_cache(db_tmp, source=hc.SOURCE_AN)
    assert n == 1
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1") is None
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_SENAT, "ppl1") == "y" * 600


# ----------------------------------------------------------------------------
# 6. Coupe-circuit env var (utilisé par conftest pour les autres tests)
# ----------------------------------------------------------------------------

def test_env_var_disable_short_circuite_lecture(db_tmp: Path, monkeypatch):
    hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1", "x" * 600)
    monkeypatch.setenv("VEILLE_DOSLEG_TEXT_CACHE_DISABLE", "1")
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1") is None


def test_env_var_disable_short_circuite_ecriture(db_tmp: Path, monkeypatch):
    monkeypatch.setenv("VEILLE_DOSLEG_TEXT_CACHE_DISABLE", "1")
    assert hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1", "x" * 600) is False
    # Vérifie qu'on n'a vraiment rien écrit
    monkeypatch.delenv("VEILLE_DOSLEG_TEXT_CACHE_DISABLE")
    assert hc.get_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJL1") is None


# ----------------------------------------------------------------------------
# 7. Intégration : _fetch_*_text_haystack utilisent bien le cache
# ----------------------------------------------------------------------------

def test_fetch_an_utilise_le_cache_si_present(db_tmp: Path, monkeypatch):
    """Cache hit → pas de fetch_text appelé."""
    from src.sources import assemblee as an_mod

    text = "Texte intégral fictif " * 50  # > 500 chars
    hc.put_cached_haystack(db_tmp, hc.SOURCE_AN, "PRJLANR5L17B999", text,
                           is_promulgated=False)

    # Le helper lit SQLITE_PATH depuis src.main — on le redirige vers db_tmp.
    import src.main as main_mod
    monkeypatch.setattr(main_mod, "SQLITE_PATH", db_tmp)

    # Si le cache était bypassé, fetch_text serait appelé → on le mocke pour
    # détecter une régression.
    called = {"n": 0}

    def _boom(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("fetch_text appelé alors que cache était présent")

    monkeypatch.setattr("src.sources._common.fetch_text", _boom)

    out = an_mod._fetch_an_dossier_text_haystack("PRJLANR5L17B999")
    assert out == text[:200000]
    assert called["n"] == 0


def test_fetch_an_cache_le_resultat_apres_fetch_live(db_tmp: Path, monkeypatch):
    """Cache miss → fetch live → cache populé pour le prochain appel."""
    from src.sources import assemblee as an_mod

    import src.main as main_mod
    monkeypatch.setattr(main_mod, "SQLITE_PATH", db_tmp)

    fake_html = "<html><body><main>" + ("Texte AN " * 100) + "</main></body></html>"
    monkeypatch.setattr("src.sources._common.fetch_text", lambda url, **kw: fake_html)

    # 1er appel : fetch live + cache write
    out1 = an_mod._fetch_an_dossier_text_haystack("PRJLANR5L17B999")
    assert "Texte AN" in out1

    # 2e appel : doit venir du cache
    fetch_calls = {"n": 0}
    def _no_more_fetch(*args, **kwargs):
        fetch_calls["n"] += 1
        return fake_html
    monkeypatch.setattr("src.sources._common.fetch_text", _no_more_fetch)

    out2 = an_mod._fetch_an_dossier_text_haystack("PRJLANR5L17B999")
    assert out2 == out1
    assert fetch_calls["n"] == 0


def test_fetch_senat_utilise_le_cache(db_tmp: Path, monkeypatch):
    """Symétrique côté Sénat : cache hit → pas de fetch_text."""
    from src.sources import senat as senat_mod

    text = "Texte intégral Sénat fictif " * 30
    hc.put_cached_haystack(db_tmp, hc.SOURCE_SENAT, "ppl25-566", text)

    import src.main as main_mod
    monkeypatch.setattr(main_mod, "SQLITE_PATH", db_tmp)

    def _boom(*args, **kwargs):
        raise AssertionError("fetch_text appelé alors que cache était présent")

    monkeypatch.setattr("src.sources.senat.fetch_text", _boom)

    out = senat_mod._fetch_senat_dossier_text_haystack(
        "https://www.senat.fr/dossier-legislatif/ppl25-566.html",
        max_chars=200000,
    )
    assert out == text[:200000]
