"""Tests R23-H (2026-04-23) : helper `_source_family` utilise cote
site_export pour poser `family_source` dans le frontmatter des items.

5 familles stables + bucket "autres" :
  - parlement (AN, Senat)
  - gouvernement (Matignon, Elysee, ministeres)
  - autorites (ANJ, AFLD, ARCOM, AdlC, CC, CE, DDD, Cour des comptes, IGESR)
  - operateurs (ANS, INSEP, INJEP, CNOSF, CPSF / France paralympique)
  - jorf (DILA / journal officiel)
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.site_export import _source_family  # noqa: E402


# ---------- parlement -----------------------------------------------------

def test_family_an_agenda():
    assert _source_family("an_agenda", "AN") == "parlement"


def test_family_an_amendements():
    assert _source_family("an_amendements", "AN") == "parlement"


def test_family_senat_rss():
    assert _source_family("senat_rss", "Senat") == "parlement"


def test_family_senat_questions_1an():
    assert _source_family("senat_questions_1an", "Senat") == "parlement"


# ---------- gouvernement --------------------------------------------------

def test_family_min_sports_actualites():
    assert _source_family("min_sports_actualites", "MinSports") == "gouvernement"


def test_family_matignon_actualites():
    assert _source_family("matignon_actualites", "Matignon") == "gouvernement"


def test_family_elysee_feed():
    assert _source_family("elysee_feed", "Elysee") == "gouvernement"


def test_family_info_gouv_actualites():
    assert _source_family("info_gouv_actualites", "Matignon") == "gouvernement"


def test_family_min_education():
    assert _source_family("min_education", "MinEDUCATION") == "gouvernement"


# ---------- autorites -----------------------------------------------------

def test_family_anj():
    assert _source_family("anj", "ANJ") == "autorites"


def test_family_afld():
    assert _source_family("afld", "AFLD") == "autorites"


def test_family_arcom():
    assert _source_family("arcom", "ARCOM") == "autorites"


def test_family_autorite_concurrence():
    assert _source_family("autorite_concurrence", "AdlC") == "autorites"


def test_family_conseil_constit():
    assert _source_family("conseil_constit_decisions", "CC") == "autorites"


def test_family_conseil_etat():
    assert _source_family("conseil_etat", "CE") == "autorites"


def test_family_ccomptes():
    assert _source_family("ccomptes_publications", "CourComptes") == "autorites"


def test_family_igesr():
    assert _source_family("igesr_rapports", "MinESR") == "autorites"


# ---------- operateurs ----------------------------------------------------

def test_family_ans():
    assert _source_family("ans", "ANS") == "operateurs"


def test_family_injep():
    assert _source_family("injep", "INJEP") == "operateurs"


def test_family_cnosf():
    assert _source_family("cnosf", "CNOSF") == "operateurs"


def test_family_france_paralympique():
    assert _source_family("france_paralympique", "CPSF") == "operateurs"


# ---------- jorf ---------------------------------------------------------

def test_family_dila_jorf():
    assert _source_family("dila_jorf", "JORF") == "jorf"


# ---------- fallback -----------------------------------------------------

def test_family_fallback_by_chamber_an():
    """source_id inconnu mais chamber == AN → parlement."""
    assert _source_family("unknown_source", "AN") == "parlement"


def test_family_fallback_autres_when_nothing_matches():
    assert _source_family("unknown_xyz", "") == "autres"


def test_family_handles_none_source_id():
    assert _source_family(None, "AN") == "parlement"


def test_family_handles_none_both():
    assert _source_family(None, None) == "autres"


def test_family_case_insensitive():
    """Les source_id sont comparés en lowercase pour robustesse."""
    assert _source_family("AN_agenda", "AN") == "parlement"


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
