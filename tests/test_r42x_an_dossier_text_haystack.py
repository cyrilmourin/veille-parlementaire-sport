"""Tests R42-X (2026-05-11) — Fetch texte intégral dossiers AN via
`/dyn/opendata/<TEXTE_REF>.html`.

Symétrique R42-L côté Sénat. Couvre :
- `_TEXTE_REF_RE` étendu pour PNRE/PNRR/AVIS/RAPP (avant : PION/PRJL/PPL/TA)
- `_first_texte_ref_from_root` : extraction du 1er texte_ref de l'arbre
- `_fetch_an_dossier_text_haystack` : graceful degradation
- Cache 404 `_AN_DOSSIER_TEXT_CACHE` (skip / mark / persist / load)

Régression cible : PPR n°2126 (PNREANR5L17B2126) « Renforcer le pilotage
de la politique nationale du sport » qui matchait 0 keyword via son
titre + libelles_haystack mais 11+ via son texte intégral.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _reset_an_dossier_404_cache_per_test():
    from src.sources import assemblee as an_mod
    from pathlib import Path as _P
    an_mod._AN_DOSSIER_TEXT_CACHE = None
    an_mod._AN_DOSSIER_TEXT_DIRTY = False
    an_mod._AN_DOSSIER_TEXT_CACHE_PATH = _P("data/an_dossier_text_404.json")
    yield
    an_mod._AN_DOSSIER_TEXT_CACHE = None
    an_mod._AN_DOSSIER_TEXT_DIRTY = False
    an_mod._AN_DOSSIER_TEXT_CACHE_PATH = _P("data/an_dossier_text_404.json")


# --------------------- _TEXTE_REF_RE étendu (PNRE/PNRR/AVIS/RAPP)
def test_texte_ref_regex_capture_pnre():
    """PNREANR5L17B2126 (PPR politique nationale du sport) doit matcher."""
    from src.sources.assemblee import _TEXTE_REF_RE
    assert _TEXTE_REF_RE.match("PNREANR5L17B2126")


def test_texte_ref_regex_capture_avis_rapp():
    from src.sources.assemblee import _TEXTE_REF_RE
    assert _TEXTE_REF_RE.match("AVISANR5L17B1000")
    assert _TEXTE_REF_RE.match("RAPPANR5L17B2500")


def test_texte_ref_regex_existing_types_still_match():
    """Types pré-R42-X (PION, PRJL, PPL, TA) continuent de matcher."""
    from src.sources.assemblee import _TEXTE_REF_RE
    assert _TEXTE_REF_RE.match("PIONANR5L17B1560")
    assert _TEXTE_REF_RE.match("PRJLANR5L17B2100")
    assert _TEXTE_REF_RE.match("PPLANR5L17B999")
    assert _TEXTE_REF_RE.match("TAANR5L17B850")


def test_texte_ref_regex_rejects_other_uids():
    """Les autres UIDs AN (DLR, PO, etc.) ne doivent PAS matcher."""
    from src.sources.assemblee import _TEXTE_REF_RE
    assert not _TEXTE_REF_RE.match("DLR5L17N52126")
    assert not _TEXTE_REF_RE.match("PO838901")
    assert not _TEXTE_REF_RE.match("RUANR5L17S2026IDS29879")


# --------------------- _first_texte_ref_from_root
def test_first_texte_ref_extrait_du_dict():
    from src.sources.assemblee import _first_texte_ref_from_root
    root = {
        "uid": "DLR5L17N52126",
        "actesLegislatifs": {
            "acteLegislatif": [
                {"codeActe": "DEPOT-PNRE", "texteAssocieRef": "PNREANR5L17B2126"},
            ],
        },
    }
    assert _first_texte_ref_from_root(root) == "PNREANR5L17B2126"


def test_first_texte_ref_prefere_textes_initiaux():
    """Quand l'arbre contient à la fois un texte initial (PION/PNRE/PRJL)
    et un dérivé (TA/RAPP), on privilégie l'initial."""
    from src.sources.assemblee import _first_texte_ref_from_root
    root = {
        "actes": [
            {"texteAssocieRef": "RAPPANR5L17B2200"},  # dérivé
            {"texteAssocieRef": "PIONANR5L17B2100"},  # initial → préféré
        ],
    }
    assert _first_texte_ref_from_root(root) == "PIONANR5L17B2100"


def test_first_texte_ref_renvoie_vide_si_aucun():
    from src.sources.assemblee import _first_texte_ref_from_root
    root = {"uid": "DLR5L17N99999", "actes": []}
    assert _first_texte_ref_from_root(root) == ""


# --------------------- _fetch_an_dossier_text_haystack
def test_fetch_haystack_texte_ref_vide_retourne_vide():
    from src.sources.assemblee import _fetch_an_dossier_text_haystack
    assert _fetch_an_dossier_text_haystack("") == ""


def test_fetch_haystack_skip_si_cache_404(monkeypatch, tmp_path):
    from src.sources import assemblee as an_mod
    monkeypatch.setattr(an_mod, "_AN_DOSSIER_TEXT_CACHE_PATH", tmp_path / "404.json")
    an_mod._AN_DOSSIER_TEXT_CACHE = {"PNREANR5L17B2126"}

    def fetch_should_not_be_called(url):
        raise AssertionError(f"fetch_text appelé sur ref en cache 404 : {url}")

    import src.sources._common as _common_mod
    monkeypatch.setattr(_common_mod, "fetch_text", fetch_should_not_be_called)
    out = an_mod._fetch_an_dossier_text_haystack("PNREANR5L17B2126")
    assert out == ""


def test_fetch_haystack_marks_on_404(monkeypatch, tmp_path):
    from src.sources import assemblee as an_mod
    monkeypatch.setattr(an_mod, "_AN_DOSSIER_TEXT_CACHE_PATH", tmp_path / "404.json")
    an_mod._AN_DOSSIER_TEXT_CACHE = set()

    def fake_fetch_404(url):
        raise RuntimeError("Client error '404 Not Found'")

    import src.sources._common as _common_mod
    monkeypatch.setattr(_common_mod, "fetch_text", fake_fetch_404)
    an_mod._fetch_an_dossier_text_haystack("AVISANR5L17B9999")
    assert "AVISANR5L17B9999" in an_mod._AN_DOSSIER_TEXT_CACHE
    assert an_mod._AN_DOSSIER_TEXT_DIRTY is True


def test_fetch_haystack_no_mark_on_timeout(monkeypatch, tmp_path):
    """Timeout / DNS / WAF transitoire ne sont PAS cachés."""
    from src.sources import assemblee as an_mod
    monkeypatch.setattr(an_mod, "_AN_DOSSIER_TEXT_CACHE_PATH", tmp_path / "404.json")
    an_mod._AN_DOSSIER_TEXT_CACHE = set()

    def fake_timeout(url):
        raise RuntimeError("Connection timeout")

    import src.sources._common as _common_mod
    monkeypatch.setattr(_common_mod, "fetch_text", fake_timeout)
    an_mod._fetch_an_dossier_text_haystack("PIONANR5L17B7000")
    assert "PIONANR5L17B7000" not in an_mod._AN_DOSSIER_TEXT_CACHE


def test_fetch_haystack_extrait_main(monkeypatch, tmp_path):
    """Fetch OK + extraction du <main> retourne le texte du dossier."""
    from src.sources import assemblee as an_mod
    monkeypatch.setattr(an_mod, "_AN_DOSSIER_TEXT_CACHE_PATH", tmp_path / "404.json")
    an_mod._AN_DOSSIER_TEXT_CACHE = set()

    fake_html = """
    <html><body>
      <nav>Menu AN</nav>
      <main>
        Proposition de résolution visant à renforcer le pilotage et la
        cohérence de la politique nationale du sport. L'Agence nationale
        du sport (ANS) joue un rôle central. Pass'Sport est un dispositif clé.
      </main>
      <footer>Footer AN</footer>
    </body></html>
    """

    import src.sources._common as _common_mod

    def fake_fetch(url):
        return fake_html

    monkeypatch.setattr(_common_mod, "fetch_text", fake_fetch)
    out = an_mod._fetch_an_dossier_text_haystack(
        "PNREANR5L17B2126", max_chars=10000,
    )
    assert "politique nationale du sport" in out
    assert "Agence nationale du sport" in out
    assert "Menu AN" not in out
    assert "Footer AN" not in out


# --------------------- Persistance cache
def test_persist_writes_json_when_dirty(monkeypatch, tmp_path):
    from src.sources import assemblee as an_mod
    cache_path = tmp_path / "404.json"
    monkeypatch.setattr(an_mod, "_AN_DOSSIER_TEXT_CACHE_PATH", cache_path)
    an_mod._AN_DOSSIER_TEXT_CACHE = {"PNREANR5L17B100", "AVISANR5L17B200"}
    an_mod._AN_DOSSIER_TEXT_DIRTY = True

    an_mod._persist_an_dossier_text_404_cache()

    import json
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert data == {"texte_refs_404": ["AVISANR5L17B200", "PNREANR5L17B100"]}


def test_load_corrupt_json_returns_empty(monkeypatch, tmp_path):
    from src.sources import assemblee as an_mod
    cache_path = tmp_path / "404.json"
    cache_path.write_text("not-json{{{", encoding="utf-8")
    monkeypatch.setattr(an_mod, "_AN_DOSSIER_TEXT_CACHE_PATH", cache_path)
    assert an_mod._load_an_dossier_text_404_cache() == set()
