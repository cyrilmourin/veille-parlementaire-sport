"""Tests unitaires du matcher de mots-clés."""
from pathlib import Path

import pytest

from src.keywords import KeywordMatcher, _normalize


CONFIG = Path(__file__).resolve().parent.parent / "config" / "keywords.yml"


@pytest.fixture(scope="module")
def m():
    return KeywordMatcher(CONFIG)


def test_normalize_accents():
    assert _normalize("Éducation physique et sportive") == "education physique et sportive"
    assert _normalize("  Pass'Sport  ") == "pass'sport"


def test_match_dispositif(m):
    kws, fams = m.match("Élargissement du dispositif Pass'Sport aux jeunes")
    assert "Pass'Sport" in kws
    assert "dispositif" in fams


def test_match_acteur(m):
    kws, fams = m.match("Audition du président du CNOSF")
    assert "CNOSF" in kws
    assert "acteur" in fams


def test_match_federation(m):
    kws, _ = m.match("Réforme de la FFR et de la LFP")
    assert "FFR" in kws and "LFP" in kws


def test_match_evenement(m):
    kws, fams = m.match("Projet Alpes 2030 et héritage Paris 2024")
    assert "Alpes 2030" in kws
    assert "evenement" in fams


def test_no_match_unrelated(m):
    kws, _ = m.match("Plan national biodiversité 2026")
    assert kws == []
