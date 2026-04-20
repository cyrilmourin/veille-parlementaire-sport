"""Tests unitaires pour scripts/refresh_amo_cache.py.

On ne teste PAS le download HTTP (allowlist sandbox + flakiness réseau).
On teste les pures fonctions d'extraction + le parse_zip via une fixture
zip in-memory minimale.
"""
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from scripts import refresh_amo_cache as rc


def test_extract_acteur_minimal():
    rec = {
        "uid": "PA720770",
        "etatCivil": {
            "ident": {"civ": "Mme", "prenom": "Marie", "nom": "Dupont"},
        },
    }
    r = rc.extract_acteur(rec)
    assert r is not None
    uid, data = r
    assert uid == "PA720770"
    assert data == {"civ": "Mme", "prenom": "Marie", "nom": "Dupont"}


def test_extract_acteur_uid_dict_form():
    """XML→JSON convertit parfois `uid` en {"#text": ...}."""
    rec = {
        "uid": {"#text": "PA123"},
        "etatCivil": {"ident": {"civ": "M.", "prenom": "Jean", "nom": "Martin"}},
    }
    r = rc.extract_acteur(rec)
    assert r is not None
    assert r[0] == "PA123"


def test_extract_acteur_skip_si_uid_invalide():
    assert rc.extract_acteur({"uid": "X123", "etatCivil": {"ident": {"prenom": "J", "nom": "M"}}}) is None
    assert rc.extract_acteur({"etatCivil": {"ident": {"prenom": "J", "nom": "M"}}}) is None


def test_extract_acteur_skip_si_pas_de_nom():
    rec = {"uid": "PA999", "etatCivil": {"ident": {"civ": "M."}}}
    assert rc.extract_acteur(rec) is None


def test_extract_organe_complet():
    rec = {
        "uid": "PO838901",
        "codeType": "COMPER",
        "libelle": "Commission des affaires culturelles",
        "libelleAbrege": "Affaires culturelles",
        "libelleAbrev": "CAC",
    }
    r = rc.extract_organe(rec)
    assert r is not None
    uid, data = r
    assert uid == "PO838901"
    assert data["libelle"] == "Commission des affaires culturelles"
    assert data["libelle_abrege"] == "Affaires culturelles"
    assert data["libelle_abrev"] == "CAC"
    assert data["type"] == "COMPER"


def test_extract_organe_skip_si_dissous():
    """Un organe avec dateFin renseignée est marqué `actif=False`."""
    rec = {
        "uid": "PO111",
        "libelle": "Ancienne commission",
        "viMoDe": {"dateFin": "2020-06-30"},
    }
    r = rc.extract_organe(rec)
    assert r is not None
    assert r[1]["actif"] is False


def test_extract_organe_skip_si_pas_de_libelle():
    assert rc.extract_organe({"uid": "PO111"}) is None


def test_extract_mandat_actif_avec_organe():
    rec = {
        "uid": "PM4040",
        "acteurRef": "PA720770",
        "typeOrgane": "GP",
        "dateDebut": "2024-07-18",
        "organes": {"organeRef": "PO800538"},
        "infosQualite": {"codeQualite": "M", "libQualite": "Membre"},
    }
    r = rc.extract_mandat(rec)
    assert r is not None
    _, data = r
    assert data["acteur_ref"] == "PA720770"
    assert data["organes"] == ["PO800538"]
    assert data["type_organe"] == "GP"


def test_extract_mandat_skip_si_clos():
    """Mandats historiques (dateFin renseignée) ignorés."""
    rec = {
        "acteurRef": "PA720770",
        "dateFin": "2022-06-21",
        "typeOrgane": "GP",
    }
    assert rc.extract_mandat(rec) is None


def test_extract_mandat_organes_liste():
    """Multi-rattachement : organeRef peut être une liste."""
    rec = {
        "acteurRef": "PA123",
        "organes": {"organeRef": ["PO111", "PO222"]},
    }
    r = rc.extract_mandat(rec)
    assert r is not None
    assert sorted(r[1]["organes"]) == ["PO111", "PO222"]


def _make_zip_unitaire(files: dict[str, dict]) -> bytes:
    """Construit un zip in-memory avec un fichier JSON par entité."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, json.dumps(content))
    return buf.getvalue()


def _make_zip_global(payload: dict) -> bytes:
    """Construit un zip in-memory avec un seul gros fichier JSON."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("export/AMO30.json", json.dumps(payload))
    return buf.getvalue()


def test_parse_zip_format_global():
    payload = {
        "export": {
            "acteurs": {"acteur": [
                {"uid": "PA1", "etatCivil": {"ident": {"civ": "Mme", "prenom": "A", "nom": "Z"}}},
                {"uid": "PA2", "etatCivil": {"ident": {"civ": "M.", "prenom": "B", "nom": "Y"}}},
            ]},
            "organes": {"organe": [
                {"uid": "PO1", "codeType": "COMPER", "libelle": "Commission X"},
                {"uid": "PO2", "codeType": "GP", "libelle": "Groupe Y", "libelleAbrev": "GY"},
            ]},
            "mandats": {"mandat": [
                {"acteurRef": "PA1", "typeOrgane": "GP",
                 "organes": {"organeRef": "PO2"},
                 "infosQualite": {"libQualite": "Membre"}},
                {"acteurRef": "PA1", "typeOrgane": "COMPER",
                 "organes": {"organeRef": "PO1"},
                 "infosQualite": {"libQualite": "Présidente"}},
            ]},
        }
    }
    zb = _make_zip_global(payload)
    out = rc.parse_zip(zb)
    assert len(out["acteurs"]) == 2
    assert "PA1" in out["acteurs"]
    assert out["acteurs"]["PA1"]["nom"] == "Z"
    # Le mandat GP doit avoir renseigné le groupe
    assert out["acteurs"]["PA1"].get("groupe") == "GY"
    # La qualité "Présidente" rattachée à PO1 doit apparaître
    assert any("Présidente" in q for q in out["acteurs"]["PA1"].get("qualites", []))
    assert "PO1" in out["organes"]


def test_parse_zip_format_unitaire():
    """Format avec un fichier par entité (>100 entrées)."""
    files = {}
    # Acteurs
    for i in range(50):
        files[f"acteur/PA{i:03d}.json"] = {
            "acteur": {"uid": f"PA{i:03d}",
                       "etatCivil": {"ident": {"civ": "M.", "prenom": "P", "nom": f"N{i}"}}}
        }
    # Organes
    for i in range(40):
        files[f"organe/PO{i:03d}.json"] = {
            "organe": {"uid": f"PO{i:03d}", "codeType": "COMPER", "libelle": f"Comm{i}"}
        }
    # Mandats
    for i in range(20):
        files[f"mandat/PM{i:03d}.json"] = {
            "mandat": {"acteurRef": f"PA{i:03d}", "typeOrgane": "COMPER",
                       "organes": {"organeRef": f"PO{i:03d}"},
                       "infosQualite": {"libQualite": "Membre"}}
        }
    zb = _make_zip_unitaire(files)
    out = rc.parse_zip(zb)
    assert len(out["acteurs"]) == 50
    assert len(out["organes"]) == 40


def test_is_fresh_logic(tmp_path):
    p = tmp_path / "c.json"
    assert rc.is_fresh(p, 7) is False  # n'existe pas
    from datetime import datetime, timezone, timedelta
    fresh = {"generated_at": datetime.now(timezone.utc).isoformat()}
    p.write_text(json.dumps(fresh))
    assert rc.is_fresh(p, 7) is True
    old = {"generated_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()}
    p.write_text(json.dumps(old))
    assert rc.is_fresh(p, 7) is False


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
