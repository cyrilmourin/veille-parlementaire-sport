"""R42-Z (2026-05-11) — Sanity check blocklist : 7 dossiers législatifs
(6 AN + 1 Sénat) signalés par Cyril comme faux positifs après le passage
R42-X (fetch texte intégral des dossiers AN).

Test : vérifier que `_load_blocklist()` charge bien les 7 URLs canonicalisées
et que `_filter_blocklist` drop effectivement les rows correspondants tout
en gardant un dossier sport-relevant légitime.
"""
from __future__ import annotations

from src.site_export import _canon_block_url, _filter_blocklist, _load_blocklist


_AN_DOSSIERS_BLOQUES = (
    "DLR5L17N52293",
    "DLR5L17N53522",
    "DLR5L17N51720",
    "DLR5L17N53171",
    "DLR5L17N54095",
    "DLR5L17N54133",
)

_SENAT_DOSSIER_BLOQUE = "ppl95-400"


def test_blocklist_contient_les_6_dossiers_an_r42z():
    blocked_urls, _, _, _ = _load_blocklist()
    for uid in _AN_DOSSIERS_BLOQUES:
        canon = _canon_block_url(
            f"https://www.assemblee-nationale.fr/dyn/17/dossiers/{uid}")
        assert canon in blocked_urls, (
            f"Dossier AN {uid!r} pas dans la blocklist : attendu {canon!r}")


def test_blocklist_contient_le_dossier_senat_r42z():
    blocked_urls, _, _, _ = _load_blocklist()
    canon = _canon_block_url(
        f"https://www.senat.fr/dossier-legislatif/{_SENAT_DOSSIER_BLOQUE}.html")
    assert canon in blocked_urls


def test_filter_drops_les_7_dossiers_et_garde_un_legitime():
    rows = []
    for uid in _AN_DOSSIERS_BLOQUES:
        rows.append({
            "source_id": "an_dossiers_legislatifs",
            "uid": uid,
            "url": f"https://www.assemblee-nationale.fr/dyn/17/dossiers/{uid}",
            "title": f"Dossier {uid}",
            "category": "dossiers_legislatifs",
            "raw": {},
        })
    rows.append({
        "source_id": "senat_dosleg",
        "uid": _SENAT_DOSSIER_BLOQUE,
        "url": f"https://www.senat.fr/dossier-legislatif/{_SENAT_DOSSIER_BLOQUE}.html",
        "title": "PPL Sénat 95-400",
        "category": "dossiers_legislatifs",
        "raw": {},
    })
    rows.append({
        "source_id": "an_dossiers_legislatifs",
        "uid": "DLR5L17N99999",
        "url": "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N99999",
        "title": "Dossier légitime à conserver",
        "category": "dossiers_legislatifs",
        "raw": {},
    })

    out = _filter_blocklist(rows)
    out_uids = {r["uid"] for r in out}
    for uid in _AN_DOSSIERS_BLOQUES:
        assert uid not in out_uids, f"Dossier AN {uid} pas filtré"
    assert _SENAT_DOSSIER_BLOQUE not in out_uids
    assert "DLR5L17N99999" in out_uids


def test_filter_match_scheme_insensible_senat_http_https():
    """L'URL fournie par Cyril utilisait `http://` alors que le pipeline
    stocke `https://`. Le canonicaliseur strip le scheme, donc les deux
    formes matchent. Régression explicite."""
    rows_http = [{
        "source_id": "senat_dosleg",
        "uid": _SENAT_DOSSIER_BLOQUE,
        "url": f"http://www.senat.fr/dossier-legislatif/{_SENAT_DOSSIER_BLOQUE}.html",
        "title": "PPL via http://",
        "category": "dossiers_legislatifs",
        "raw": {},
    }]
    rows_https = [{
        "source_id": "senat_dosleg",
        "uid": _SENAT_DOSSIER_BLOQUE,
        "url": f"https://www.senat.fr/dossier-legislatif/{_SENAT_DOSSIER_BLOQUE}.html",
        "title": "PPL via https://",
        "category": "dossiers_legislatifs",
        "raw": {},
    }]
    assert _filter_blocklist(rows_http) == []
    assert _filter_blocklist(rows_https) == []
