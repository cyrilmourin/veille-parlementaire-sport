"""Tests sur la déduplication des dossiers législatifs (`site_export._dedup`).

Couvre R18+ (2026-04-22) — passe 2c par identifiant de procédure législative :

* AN (`DLR5L17N52100`) et Sénat (`pjl24-630`) doivent fusionner dès que le
  parser Sénat AKN expose `raw["url_an"]` pointant vers l'URL AN. C'est le
  cas robuste : un PJL avec deux chambres, deux URLs, deux titres quasi
  identiques. Avant R18+, seule la passe sémantique pouvait les fusionner
  — et seulement si l'intersection des mots significatifs atteignait le
  seuil (5 depuis R18+). La passe 2c garantit la fusion même quand les
  titres divergent trop ou sont trop courts.
* `_extract_dossier_ids_from_url` : isolation des IDs depuis une URL AN
  ou Sénat.
* Deux dossiers distincts (IDs disjoints) ne fusionnent PAS via 2c.
* Tiebreak `_prefer` : à date égale, on garde la variante Sénat avec URL
  `/dossier-legislatif/` plutôt que l'AN.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.site_export import (  # noqa: E402
    _dedup,
    _extract_dossier_ids_from_url,
    _item_dossier_ids,
)


def _make_item(uid, title, url, chamber, date, raw=None):
    """Forge un row tel que `_dedup` l'attend : `raw` dict (pas JSON)."""
    return {
        "uid": uid,
        "title": title,
        "url": url,
        "chamber": chamber,
        "category": "dossiers_legislatifs",
        "published_at": f"{date}T10:00:00Z",
        "raw": raw or {},
    }


# ---------- _extract_dossier_ids_from_url ---------------------------------

def test_extract_an_dossier_id_from_url():
    url = "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N52100"
    ids = _extract_dossier_ids_from_url(url)
    assert "dlr5l17n52100" in ids


def test_extract_senat_dossier_id_from_url():
    url = "https://www.senat.fr/dossier-legislatif/pjl24-630.html"
    ids = _extract_dossier_ids_from_url(url)
    assert "pjl24-630" in ids


def test_extract_from_http_senat_url_without_scheme_variant():
    # Scheme http + URL tronquée (vu dans la DB — Sénat historique)
    url = "http://www.senat.fr/dossier-legislatif/pjl24-630.html"
    ids = _extract_dossier_ids_from_url(url)
    assert "pjl24-630" in ids


def test_extract_empty_url_returns_empty_set():
    assert _extract_dossier_ids_from_url("") == set()
    assert _extract_dossier_ids_from_url(None) == set()


# ---------- _item_dossier_ids --------------------------------------------

def test_item_ids_collects_raw_and_url():
    row = _make_item(
        "senat-pjl24-630",
        "Jeux Olympiques et Paralympiques de 2030",
        "https://www.senat.fr/dossier-legislatif/pjl24-630.html",
        "Senat",
        "2026-03-20",
        raw={
            "signet": "pjl24-630",
            "dossier_id": "pjl24-630",
            "url_an": "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N52100",
        },
    )
    ids = _item_dossier_ids(row)
    # Doit collecter le signet + l'ID extrait de l'URL Sénat
    # + l'ID AN extrait de url_an.
    assert "pjl24-630" in ids
    assert "dlr5l17n52100" in ids


# ---------- _dedup passe 2c : fusion AN↔Sénat par url_an ------------------

def test_dedup_merges_an_and_senat_via_url_an():
    """Cœur du fix : un PJL AN (DLR5L17N52100) et son pendant Sénat
    (pjl24-630) avec `raw.url_an` côté Sénat doivent fusionner en 1 item.
    Tiebreak _prefer : date égale → chambre Sénat → URL dosleg."""
    an = _make_item(
        "DLR5L17N52100",
        "Projet de loi relatif à l'organisation des jeux Olympiques et Paralympiques de 2030",
        "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N52100",
        "AN",
        "2026-03-20",
        raw={"dossier_id": "DLR5L17N52100"},
    )
    senat = _make_item(
        "pjl24-630",
        "Jeux Olympiques et Paralympiques de 2030 (PJL)",
        "https://www.senat.fr/dossier-legislatif/pjl24-630.html",
        "Senat",
        "2026-03-20",
        raw={
            "signet": "pjl24-630",
            "dossier_id": "pjl24-630",
            "url_an": "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N52100",
        },
    )
    result = _dedup([an, senat])
    dosleg = [r for r in result if r.get("category") == "dossiers_legislatifs"]
    assert len(dosleg) == 1, f"attendu 1 item fusionné, vu {len(dosleg)}"
    # Tiebreak : Sénat gagne (chambre Sénat, et URL /dossier-legislatif/)
    assert dosleg[0]["chamber"] == "Senat"
    assert "senat.fr/dossier-legislatif/pjl24-630" in dosleg[0]["url"]


def test_dedup_does_not_merge_distinct_dossiers():
    """Deux dossiers sans ID commun (et sans intersection sémantique)
    doivent rester séparés après toutes les passes de dedup."""
    a = _make_item(
        "DLR5L17N52100",
        "Projet de loi relatif à l'organisation des jeux Olympiques et Paralympiques de 2030",
        "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N52100",
        "AN",
        "2026-03-20",
        raw={"dossier_id": "DLR5L17N52100"},
    )
    b = _make_item(
        "DLR5L17N12345",
        "Proposition de loi portant simplification administrative",
        "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N12345",
        "AN",
        "2026-02-10",
        raw={"dossier_id": "DLR5L17N12345"},
    )
    result = _dedup([a, b])
    dosleg = [r for r in result if r.get("category") == "dossiers_legislatifs"]
    assert len(dosleg) == 2, f"attendu 2 items distincts, vu {len(dosleg)}"


def test_dedup_merges_senat_variants_via_signet_across_urls():
    """Deux variantes Sénat du même dossier (URLs scheme http vs https,
    titres différents, dates différentes) partagent le même signet
    `pjl24-630` → passe 2a ou 2c les fusionne. Le plus récent gagne."""
    old = _make_item(
        "pjl24-630-old",
        "Jeux Olympiques et Paralympiques de 2030 (PJL)",
        "http://www.senat.fr/dossier-legislatif/pjl24-630.html",
        "Senat",
        "2026-01-27",
        raw={"signet": "pjl24-630", "dossier_id": "pjl24-630"},
    )
    new = _make_item(
        "pjl24-630-new",
        "Projet de loi relatif à l'organisation des jeux Olympiques et Paralympiques de 2030",
        "https://www.senat.fr/dossier-legislatif/pjl24-630.html",
        "Senat",
        "2026-03-20",
        raw={"signet": "pjl24-630", "dossier_id": "pjl24-630"},
    )
    result = _dedup([new, old])
    dosleg = [r for r in result if r.get("category") == "dossiers_legislatifs"]
    assert len(dosleg) == 1
    assert dosleg[0]["published_at"].startswith("2026-03-20")


def test_dedup_r22a_preserves_url_an_bridge_across_passes():
    """R22a (2026-04-23) — régression : passe 2a écrasait l'url_an utile
    au bridge AN↔Sénat. Scénario réel JOP Alpes 2030 : 4 items (1 AN +
    2 senat_akn avec url_an + 1 senat_promulguees sans url_an). Passe 2a
    fusionne les 3 Sénat → garde senat_promulguees (date plus récente) →
    perd `url_an`. Passe 2c ne peut plus relier AN↔Sénat. Fix :
    `_merge_ids_into_winner` cumule les IDs dans `raw._merged_dossier_ids`
    pour que 2c les voie. Résultat attendu : 1 item au lieu de 2.
    """
    an = _make_item(
        "DLR5L17N52100",
        "Projet de loi relatif à l'organisation des jeux Olympiques et Paralympiques de 2030",
        "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N52100",
        "AN",
        "2026-03-20",
        raw={"dossier_id": "DLR5L17N52100"},
    )
    senat_prom = _make_item(
        "2026-201",
        "Projet de loi relatif à l'organisation des jeux Olympiques et Paralympiques de 2030",
        "http://www.senat.fr/dossier-legislatif/pjl24-630.html",
        "Senat",
        "2026-03-20",
        raw={"dossier_id": "2026-201"},
    )
    senat_akn_adop = _make_item(
        "pjl24-630",
        "Jeux Olympiques et Paralympiques de 2030 (PJL)",
        "https://www.senat.fr/dossier-legislatif/pjl24-630.html",
        "Senat",
        "2026-02-05",
        raw={
            "dossier_id": "pjl24-630",
            "signet": "pjl24-630",
            "url_an": "http://www.assemblee-nationale.fr/17/dossiers/DLR5L17N52100.asp",
        },
    )
    senat_akn_depot = _make_item(
        "pjl24-630",
        "Jeux Olympiques et Paralympiques de 2030 (PJL)",
        "https://www.senat.fr/dossier-legislatif/pjl24-630.html",
        "Senat",
        "2026-01-27",
        raw={
            "dossier_id": "pjl24-630",
            "signet": "pjl24-630",
            "url_an": "http://www.assemblee-nationale.fr/17/dossiers/DLR5L17N52100.asp",
        },
    )
    result = _dedup([senat_prom, an, senat_akn_adop, senat_akn_depot])
    dosleg = [r for r in result if r.get("category") == "dossiers_legislatifs"]
    assert len(dosleg) == 1, (
        f"attendu 1 item fusionné (4→1 via merged_dossier_ids bridge), "
        f"vu {len(dosleg)}"
    )
