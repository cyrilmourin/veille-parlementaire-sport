"""Tests unitaires pour amo_loader (résolution PAxxx → nom, POxxx → libellé)."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src import amo_loader


@pytest.fixture
def amo_cache(tmp_path, monkeypatch):
    """Crée un fichier cache fixtures et redirige le loader dessus."""
    cache = tmp_path / "amo_resolved.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legislature": 17,
        "acteurs": {
            "PA720770": {
                "civ": "Mme", "prenom": "Marie", "nom": "Dupont",
                "groupe": "LFI-NFP", "groupe_ref": "PO800538",
                "qualites": ["Rapporteure — Affaires culturelles"],
            },
            "PA123": {
                "civ": "M.", "prenom": "Jean", "nom": "Martin",
                "groupe": "RE",
            },
            "PA999": {
                "prenom": "", "nom": "",  # edge : fiche vide (rare en prod)
            },
        },
        "organes": {
            "PO838901": {
                "libelle": "Commission des affaires culturelles et de l'éducation",
                "libelle_abrege": "Affaires culturelles",
                "libelle_abrev": "CAC",
                "type": "COMPER",
            },
            "PO800538": {
                "libelle": "La France insoumise - Nouveau Front populaire",
                "libelle_abrev": "LFI-NFP",
                "type": "GP",
            },
            "PO420120": {
                "libelle": "Délégation aux droits des femmes",
                "libelle_abrev": "DDF",
                "type": "DELEG",
            },
        },
    }
    cache.write_text(json.dumps(payload))
    monkeypatch.setenv("VEILLE_AMO_CACHE", str(cache))
    amo_loader.reset()
    yield cache
    amo_loader.reset()


def test_resolve_acteur_complet(amo_cache):
    assert amo_loader.resolve_acteur("PA720770") == "Mme Marie Dupont"
    assert amo_loader.resolve_acteur("PA720770", with_civ=False) == "Marie Dupont"


def test_resolve_acteur_inconnu(amo_cache):
    assert amo_loader.resolve_acteur("PA000000") == ""
    assert amo_loader.resolve_acteur("") == ""
    assert amo_loader.resolve_acteur("NOT_A_PA") == ""


def test_resolve_groupe(amo_cache):
    assert amo_loader.resolve_groupe("PA720770") == "LFI-NFP"
    assert amo_loader.resolve_groupe("PA123") == "RE"
    assert amo_loader.resolve_groupe("PA999") == ""


def test_resolve_qualites(amo_cache):
    qs = amo_loader.resolve_qualites("PA720770")
    assert qs == ["Rapporteure — Affaires culturelles"]
    assert amo_loader.resolve_qualites("PA123") == []


def test_resolve_organe_long(amo_cache):
    assert amo_loader.resolve_organe("PO838901") == (
        "Commission des affaires culturelles et de l'éducation"
    )


def test_resolve_organe_short(amo_cache):
    assert amo_loader.resolve_organe("PO838901", prefer_long=False) == "CAC"


def test_resolve_organe_inconnu(amo_cache):
    assert amo_loader.resolve_organe("PO000000") == ""
    assert amo_loader.resolve_organe("") == ""


def test_format_auteur_avec_groupe(amo_cache):
    assert amo_loader.format_auteur("PA720770") == "Mme Marie Dupont (LFI-NFP)"


def test_format_auteur_sans_groupe(amo_cache):
    assert amo_loader.format_auteur("PA999") == "Député PA999"


def test_format_auteur_inconnu(amo_cache):
    assert amo_loader.format_auteur("PA000000") == "Député PA000000"
    assert amo_loader.format_auteur("PA000000", default_role="Sénatrice") == "Sénatrice PA000000"


def test_format_organe(amo_cache):
    assert amo_loader.format_organe("PO838901") == (
        "Commission des affaires culturelles et de l'éducation"
    )
    assert amo_loader.format_organe("PO000000") == "Organe PO000000"


def test_cache_absent_fallback(tmp_path, monkeypatch):
    """Si le fichier cache n'existe pas, les resolveurs renvoient "" sans
    lever d'exception (dev local, pipeline tolère l'absence)."""
    missing = tmp_path / "absent.json"
    monkeypatch.setenv("VEILLE_AMO_CACHE", str(missing))
    amo_loader.reset()
    try:
        assert amo_loader.resolve_acteur("PA720770") == ""
        assert amo_loader.resolve_organe("PO838901") == ""
        assert amo_loader.format_auteur("PA720770") == "Député PA720770"
        s = amo_loader.stats()
        assert s["acteurs"] == 0
        assert s["organes"] == 0
        assert s["load_error"] and "introuvable" in s["load_error"].lower()
    finally:
        amo_loader.reset()


def test_cache_corrompu_fallback(tmp_path, monkeypatch):
    """Si le cache JSON est invalide, on log une erreur mais on continue."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not-valid-json")
    monkeypatch.setenv("VEILLE_AMO_CACHE", str(bad))
    amo_loader.reset()
    try:
        assert amo_loader.resolve_acteur("PA720770") == ""
        s = amo_loader.stats()
        assert s["load_error"] is not None
    finally:
        amo_loader.reset()


def test_stats_reporte_bonne_taille(amo_cache):
    s = amo_loader.stats()
    assert s["acteurs"] == 3
    assert s["organes"] == 3
    assert s["generated_at"] is not None


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
