"""Tests unitaires pour _normalize_agenda (Patch C — AN agenda).

Reproduit la structure JSON observée en prod (shotgun DB + XSD AN 0.9.8) :
- seance_type avec ODJ + identifiants (numSeanceJO, quantieme)
- reunionCommission_type avec audition
- Fallback si dump utilise ancienne casse `timestampDebut`
"""
import sys
from pathlib import Path

# Ajouter racine repo au sys.path
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.sources.assemblee import (
    _normalize_agenda,
    _collect_agenda_titles,
    _is_agenda_title_candidate,
    _AGENDA_ID_RE,
)


def test_title_candidate_filter():
    # Vrais libellés d'ODJ
    assert _is_agenda_title_candidate(
        "Suite de la discussion du projet de loi de financement de la sécurité sociale pour 2026"
    )
    assert _is_agenda_title_candidate(
        "Audition de M. Alain Dupuy, directeur du programme scientifique"
    )
    assert _is_agenda_title_candidate(
        "Examen du rapport d'information sur le sport"
    )
    # Bruit : statuts, états, booléens, ordinaux, codes
    assert not _is_agenda_title_candidate("Confirmé")
    assert not _is_agenda_title_candidate("Ordinaire")
    assert not _is_agenda_title_candidate("Troisième")
    assert not _is_agenda_title_candidate("PO838901")
    assert not _is_agenda_title_candidate("SLANPBS6351")
    assert not _is_agenda_title_candidate("RUANR5L17S2026IDS29879")
    assert not _is_agenda_title_candidate("seance_type")
    assert not _is_agenda_title_candidate("podjSeanceConfPres_type")
    assert not _is_agenda_title_candidate("2025-11-07T21:30:00.000+01:00")
    # Trop court / vide
    assert not _is_agenda_title_candidate("Texte court")
    assert not _is_agenda_title_candidate("")
    # Codes collés sans espace
    assert not _is_agenda_title_candidate("CodeOrganeCollé")


def test_id_regex_rejects_codes_and_types():
    assert _AGENDA_ID_RE.match("PO838901")
    assert _AGENDA_ID_RE.match("SLANPBS6351")
    assert _AGENDA_ID_RE.match("DLR5L17N52922")
    assert _AGENDA_ID_RE.match("CRSANR5L17S2026O1N039")
    assert _AGENDA_ID_RE.match("seance_type")
    assert _AGENDA_ID_RE.match("podjSeanceConfPres_type")
    assert not _AGENDA_ID_RE.match("Suite de la discussion du projet")


def test_collect_titles_seance_prioritizes_titreODJ():
    root = {
        "uid": "RUANR5L17S2026IDS29879",
        "@xsi:type": "seance_type",
        "timeStampDebut": "2025-11-07T21:30:00.000+01:00",
        "ODJ": {"pointsODJ": {"pointODJ": [
            {
                "@xsi:type": "podjSeanceConfPres_type",
                "uid": "RUANR5L17S2026IDS29879PT50907",
                "titreODJ": "Suite de la discussion du projet de loi de financement de la sécurité sociale pour 2026",
                "dossierRef": "DLR5L17N52922",
                "libelle": "Suite de la discussion",
            }
        ]}},
    }
    titles = _collect_agenda_titles(root)
    assert titles, "aucun titre collecté"
    assert titles[0].startswith("Suite de la discussion du projet")


def test_normalize_agenda_seance_full():
    """Cas réel : séance publique avec ODJ + compte rendu + identifiants JO."""
    root = {
        "uid": "RUANR5L17S2026IDS29879",
        "@xsi:type": "seance_type",
        "timeStampDebut": "2025-11-07T21:30:00.000+01:00",
        "timeStampFin": "2025-11-08T00:00:00.000+01:00",
        "organeReuniRef": "PO838901",
        "lieu": {"code": "HE", "libelleLong": "Hémicycle"},
        "cycleDeVie": {"etat": "Confirmé"},
        "ODJ": {"pointsODJ": {"pointODJ": [
            {
                "titreODJ": "Suite de la discussion du projet de loi de financement de la sécurité sociale pour 2026",
                "dossierRef": "DLR5L17N52922",
            }
        ]}},
        "identifiants": {
            "numSeanceJO": "39",
            "idJO": "20260039",
            "quantieme": "Troisième",
        },
        "compteRenduRef": "CRSANR5L17S2026O1N039",
    }
    src = {"id": "an_agenda", "category": "agenda"}
    items = list(_normalize_agenda({"reunion": root}, src, "agenda"))
    assert len(items) == 1
    it = items[0]

    # Titre : "Troisième séance n°39 — Suite de la discussion…"
    assert "Troisième séance" in it.title, it.title
    assert "n°39" in it.title, it.title
    assert "Suite de la discussion" in it.title, it.title

    # Date : bug historique (timestampDebut vs timeStampDebut)
    assert it.published_at is not None, "published_at doit être résolu"
    assert it.published_at.date().isoformat() == "2025-11-07"

    # URL vers compte rendu séance
    assert "comptes-rendus/seance/CRSANR5L17S2026O1N039" in it.url, it.url

    # Raw : lieu correct (libelleLong), xsi_type
    assert it.raw["lieu"] == "Hémicycle"
    assert it.raw["xsi_type"] == "seance_type"
    assert it.raw["organe"] == "PO838901"


def test_normalize_agenda_commission_audition():
    """Cas réel : commission avec audition (item #4 prod)."""
    root = {
        "uid": "RUANR5L17S2025IDC455962",
        "@xsi:type": "reunionCommission_type",
        "timeStampDebut": "2025-06-11T15:00:00.000+02:00",
        "organeReuniRef": "PO861472",
        "lieu": {
            "code": "SLAN33SDS4204",
            "libelleLong": "Salle 4204 –  9 rue de Bourgogne, 2ème étage",
        },
        "cycleDeVie": {"etat": "Confirmé"},
        "ODJ": {"pointsODJ": {"pointODJ": [
            {
                "titreODJ": "Audition de M. Alain Dupuy, directeur du programme scientifique « eau et changement global » au bureau de recherches géologiques et minières (BRGM).",
                "libelleObjet": "audition de M. Alain Dupuy, directeur du programme scientifique au BRGM.",
            }
        ]}},
    }
    src = {"id": "an_agenda", "category": "agenda"}
    items = list(_normalize_agenda({"reunion": root}, src, "agenda"))
    assert len(items) == 1
    it = items[0]

    # Titre : commence par "Audition de M. Alain Dupuy"
    assert it.title.startswith("Audition de M. Alain Dupuy"), it.title
    assert it.published_at.date().isoformat() == "2025-06-11"

    # URL : pas de compteRenduRef → lien vers agenda par jour
    assert "agenda" in it.url.lower(), it.url
    assert "2025-06-11" in it.url

    assert it.raw["xsi_type"] == "reunioncommission_type"


def test_normalize_agenda_fallback_lowercase_timestamp():
    """Si un dump utilise l'ancienne casse `timestampDebut` (lowercase s), on
    doit quand même extraire la date (rétro-compat)."""
    root = {
        "uid": "RUANR5L17XYZ",
        "timestampDebut": "2025-10-01T10:00:00+02:00",
        "organeReuniRef": "PO000",
    }
    src = {"id": "an_agenda", "category": "agenda"}
    items = list(_normalize_agenda({"reunion": root}, src, "agenda"))
    assert len(items) == 1
    it = items[0]
    assert it.published_at is not None
    assert it.published_at.date().isoformat() == "2025-10-01"


def test_normalize_agenda_missing_title():
    """Réunion sans libellé d'ODJ : titre fallback 'Réunion (POxxx)'."""
    root = {
        "uid": "RUANR5L17EMPTY",
        "timeStampDebut": "2025-10-01T10:00:00+02:00",
        "organeReuniRef": "PO420120",
    }
    src = {"id": "an_agenda", "category": "agenda"}
    items = list(_normalize_agenda({"reunion": root}, src, "agenda"))
    assert len(items) == 1
    it = items[0]
    assert "Réunion" in it.title
    assert "PO420120" in it.title


def test_normalize_agenda_reunioncommission_without_title():
    """Réunion de commission sans ODJ : fallback 'Réunion de commission (PO…)'."""
    root = {
        "uid": "RUANR5L17C999",
        "@xsi:type": "reunionCommission_type",
        "timeStampDebut": "2025-09-15T10:00:00+02:00",
        "organeReuniRef": "PO420120",
    }
    src = {"id": "an_agenda", "category": "agenda"}
    items = list(_normalize_agenda({"reunion": root}, src, "agenda"))
    assert len(items) == 1
    it = items[0]
    assert "Réunion de commission" in it.title
    assert "PO420120" in it.title
    assert it.published_at.date().isoformat() == "2025-09-15"


if __name__ == "__main__":
    test_title_candidate_filter()
    test_id_regex_rejects_codes_and_types()
    test_collect_titles_seance_prioritizes_titreODJ()
    test_normalize_agenda_seance_full()
    test_normalize_agenda_commission_audition()
    test_normalize_agenda_fallback_lowercase_timestamp()
    test_normalize_agenda_missing_title()
    test_normalize_agenda_reunioncommission_without_title()
    print("Tous les tests passent.")
