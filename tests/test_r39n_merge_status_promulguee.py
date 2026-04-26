"""R39-N (2026-04-26) — `_merge_ids_into_winner` propage `is_promulgated` /
`status_label="Promulguée"` du loser vers le winner.

Contexte : depuis R39-A (réactivation `senat_dosleg`), le CSV historique
des dossiers Sénat ramène TOUS les pjl, y compris ceux promulgués, mais
sans le flag promulgation. Au tiebreak `_prefer()` (date de dépôt = date
de promulgation → égalité → premier rencontré), `senat_dosleg` peut
gagner sur `senat_promulguees` qui porte pourtant l'info utile.

Sans ce merge, la chip « Promulguée » disparaît du site (cas observé
sur prod 2026-04-26 pour pjl22-220 / JOP 2024 et pjl24-630 / JOP 2030).
"""
from __future__ import annotations

from src.site_export import _merge_ids_into_winner


def _row(*, sid: str, pd: str, raw: dict) -> dict:
    return {
        "source_id": sid,
        "category": "dossiers_legislatifs",
        "chamber": "Senat",
        "url": f"https://www.senat.fr/dossier-legislatif/{raw.get('dossier_id','x')}.html",
        "published_at": pd,
        "raw": raw,
    }


def test_winner_inherits_is_promulgated_from_loser():
    """Cas pjl22-220 prod : senat_dosleg gagne le tiebreak, mais
    senat_promulguees portait `is_promulgated=True`. Après merge, le
    winner expose la chip Promulguée."""
    winner = _row(
        sid="senat_dosleg",
        pd="2023-05-19T00:00:00",
        raw={"dossier_id": "pjl22-220"},
    )
    loser = _row(
        sid="senat_promulguees",
        pd="2023-05-19T00:00:00",
        raw={
            "dossier_id": "2023-380",
            "is_promulgated": True,
            "status_label": "Promulguée",
        },
    )
    _merge_ids_into_winner(winner, loser)
    assert winner["raw"]["is_promulgated"] is True
    assert winner["raw"]["status_label"] == "Promulguée"


def test_winner_already_promulgated_no_op():
    """Si le winner a déjà le flag, le merge est idempotent."""
    winner = _row(
        sid="senat_promulguees",
        pd="2023-05-19T00:00:00",
        raw={
            "dossier_id": "2023-380",
            "is_promulgated": True,
            "status_label": "Promulguée",
        },
    )
    loser = _row(
        sid="senat_dosleg",
        pd="2023-05-19T00:00:00",
        raw={"dossier_id": "pjl22-220"},
    )
    _merge_ids_into_winner(winner, loser)
    assert winner["raw"]["is_promulgated"] is True
    assert winner["raw"]["status_label"] == "Promulguée"


def test_loser_without_promulgation_does_not_overwrite_winner():
    """Si le loser n'a aucun flag, le winner garde son état initial.
    Cas de fusion entre deux items sans info promulgation."""
    winner = _row(
        sid="senat_akn_depots",
        pd="2026-01-27T00:00:00",
        raw={
            "dossier_id": "pjl24-630",
            "status_label": "Sénat · CMP · commission",
        },
    )
    loser = _row(
        sid="senat_dosleg",
        pd="2026-01-27T00:00:00",
        raw={"dossier_id": "pjl24-630"},
    )
    _merge_ids_into_winner(winner, loser)
    # is_promulgated absent (pas écrasé par False non plus)
    assert "is_promulgated" not in winner["raw"] or winner["raw"]["is_promulgated"] is None or winner["raw"]["is_promulgated"] is False
    # status_label inchangé
    assert winner["raw"]["status_label"] == "Sénat · CMP · commission"


def test_loser_promulgated_overwrites_winner_in_progress_status():
    """senat_promulguees (loser au tiebreak) écrase un status_label
    « 1ère lecture » non informatif. Cas pjl24-630 où senat_akn_*
    porte un statut intermédiaire et senat_promulguees porte la
    promulgation finale.
    """
    winner = _row(
        sid="senat_akn_adoptions",
        pd="2026-03-20T00:00:00",
        raw={
            "dossier_id": "pjl24-630",
            "status_label": "Sénat · CMP · hémicycle",
        },
    )
    loser = _row(
        sid="senat_promulguees",
        pd="2026-03-20T00:00:00",
        raw={
            "dossier_id": "2026-201",
            "is_promulgated": True,
            "status_label": "Promulguée",
        },
    )
    _merge_ids_into_winner(winner, loser)
    assert winner["raw"]["is_promulgated"] is True
    assert winner["raw"]["status_label"] == "Promulguée"


def test_retire_priority_over_promulguee():
    """Si jamais le winner a `status_label="Retiré"` (R13-L), un loser
    promulgué ne doit pas l'écraser : un retrait l'emporte sur une
    promulgation (cas pathologique : un dossier apparemment promulgué
    qui a été retiré ensuite — la priorité retrait reste). Idempotent.
    """
    winner = _row(
        sid="senat_dosleg",
        pd="2023-05-19T00:00:00",
        raw={
            "dossier_id": "pjl22-220",
            "status_label": "Retiré",
            "is_retire": True,
        },
    )
    loser = _row(
        sid="senat_promulguees",
        pd="2023-05-19T00:00:00",
        raw={
            "dossier_id": "2023-380",
            "is_promulgated": True,
            "status_label": "Promulguée",
        },
    )
    _merge_ids_into_winner(winner, loser)
    # Retiré conservé (priorité 3 > Promulguée 2)
    assert winner["raw"]["status_label"] == "Retiré"
    # is_promulgated peut être marqué True (info technique conservée)
    # mais le label reste Retiré pour le rendu.
    assert winner["raw"]["is_promulgated"] is True


def test_dossier_ids_still_merged():
    """Le merge des IDs (R22a) doit continuer à fonctionner."""
    winner = _row(
        sid="senat_dosleg",
        pd="2023-05-19T00:00:00",
        raw={"dossier_id": "pjl22-220"},
    )
    loser = _row(
        sid="senat_promulguees",
        pd="2023-05-19T00:00:00",
        raw={
            "dossier_id": "2023-380",
            "is_promulgated": True,
            "status_label": "Promulguée",
        },
    )
    _merge_ids_into_winner(winner, loser)
    merged = winner["raw"]["_merged_dossier_ids"]
    assert "2023-380" in merged
