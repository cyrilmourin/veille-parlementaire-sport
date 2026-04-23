"""Tests R23-G (2026-04-23) : titre agenda AN precis, sans lieu.

Regressions couvertes :

* `_collect_agenda_titles` ne descend plus dans le sous-arbre `lieu.*`
  (sinon `lieu.libelleLong` = "Salle 6242 – Palais Bourbon" remontait
  comme titre de reunion via la clef `libelleLong`).
* `_is_agenda_title_candidate` rejette les chaines qui decrivent un
  lieu (regex _AGENDA_LIEU_RE : Salle, Visioconference, Hemicycle,
  Palais Bourbon, "N rue ...").
* `_fix_agenda_row` cote site_export.py reecrit les titres legacy qui
  transportent un suffixe " — <lieu>" ou qui SONT un lieu pur.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.sources.assemblee import (  # noqa: E402
    _collect_agenda_titles,
    _is_agenda_title_candidate,
)
from src.site_export import _fix_agenda_row  # noqa: E402


# ---------- _is_agenda_title_candidate : rejet des lieux ------------------

def test_is_candidate_rejects_salle():
    assert not _is_agenda_title_candidate(
        "Salle 6242 – Palais Bourbon, 2ème sous-sol"
    )


def test_is_candidate_rejects_visioconference():
    assert not _is_agenda_title_candidate("Visioconférence sans salle")


def test_is_candidate_rejects_palais_bourbon():
    assert not _is_agenda_title_candidate("Palais Bourbon, salle Lamartine")


def test_is_candidate_rejects_rue_address():
    assert not _is_agenda_title_candidate(
        "9 rue de Bourgogne, 3ème étage"
    )


def test_is_candidate_accepts_audition():
    assert _is_agenda_title_candidate(
        "Audition de la Fédération française de football"
    )


def test_is_candidate_accepts_examen_de_texte():
    assert _is_agenda_title_candidate(
        "Examen du projet de loi relatif au sport professionnel"
    )


# ---------- _collect_agenda_titles : ignore le sous-arbre `lieu` ---------

def test_collect_titles_ignores_lieu_subtree():
    """Un libelleLong sous `lieu.*` ne doit pas remonter comme titre
    candidat (cf. "Salle 6242 – Palais Bourbon, 2ème sous-sol")."""
    root = {
        "titreReunion": "Audition du président de la Cour des comptes",
        "lieu": {
            "code": "SLANPBS6242",
            "libelleCourt": "Salle 6242",
            "libelleLong": "Salle 6242 – Palais Bourbon, 2ème sous-sol",
        },
    }
    titles = _collect_agenda_titles(root)
    assert titles
    assert titles[0] == "Audition du président de la Cour des comptes"
    assert not any("Salle 6242" in t for t in titles)


def test_collect_titles_ignores_chambre_a_confirmer():
    """'Assemblée nationale (à confirmer)' / 'Sénat (à confirmer)' sont du
    bruit de chambre d'accueil pour les offices bicaméraux — ne doivent
    pas apparaître comme titre."""
    root = {
        "libelleObjet": "Office parlementaire — travaux sur l'IA",
        "chambreHote": "Assemblée nationale (à confirmer)",
    }
    titles = _collect_agenda_titles(root)
    assert titles
    assert all(
        "à confirmer" not in t.lower() and "a confirmer" not in t.lower()
        for t in titles
    )


# ---------- _fix_agenda_row R23-G : titres legacy pollues par le lieu ----

def test_fix_agenda_row_strips_lieu_suffix():
    """Un titre legacy "Commission des affaires sociales — Salle 6351 …"
    doit devenir "Commission des affaires sociales"."""
    r = {
        "category": "agenda",
        "title": "Commission des affaires sociales — Salle 6351 – Palais Bourbon, 1ème étage",
        "raw": {"organe_label": "Commission des affaires sociales"},
    }
    _fix_agenda_row(r)
    assert r["title"] == "Commission des affaires sociales"


def test_fix_agenda_row_strips_visioconference_suffix():
    r = {
        "category": "agenda",
        "title": "Mission d'information sur la gouvernance du sport — Visioconférence sans salle",
        "raw": {"organe_label": "Mission d'information"},
    }
    _fix_agenda_row(r)
    assert r["title"] == "Mission d'information sur la gouvernance du sport"


def test_fix_agenda_row_title_is_pure_lieu_falls_back_to_organe():
    """Titre = "salle 4075 (9 rue de Bourgogne)" → organe_label."""
    r = {
        "category": "agenda",
        "title": "salle 4075 (9 rue de Bourgogne)",
        "raw": {"organe_label": "Condition et bien-être des animaux"},
    }
    _fix_agenda_row(r)
    assert r["title"] == "Condition et bien-être des animaux"


def test_fix_agenda_row_title_is_pure_lieu_no_organe_falls_back_to_default():
    """Titre = lieu pur ET aucun organe_label → 'Réunion parlementaire'."""
    r = {
        "category": "agenda",
        "title": "Salle 4088 (9 rue de Bourgogne)",
        "raw": {},
    }
    _fix_agenda_row(r)
    assert r["title"] == "Réunion parlementaire"


def test_fix_agenda_row_r23g_is_idempotent():
    r = {
        "category": "agenda",
        "title": "Commission des lois — Salle Lamartine",
        "raw": {"organe_label": "Commission des lois"},
    }
    _fix_agenda_row(r)
    once = r["title"]
    _fix_agenda_row(r)
    assert r["title"] == once


def test_fix_agenda_row_r23g_noop_on_clean_title():
    """Un titre deja propre (pas de suffixe lieu) est laisse tel quel."""
    r = {
        "category": "agenda",
        "title": "Audition du ministre des sports sur le budget 2027",
        "raw": {},
    }
    _fix_agenda_row(r)
    assert r["title"] == "Audition du ministre des sports sur le budget 2027"


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
