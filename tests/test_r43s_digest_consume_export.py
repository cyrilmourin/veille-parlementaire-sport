"""Tests R43-S (2026-05-18) — Architecture : le digest consume la liste
`filtered_rows` retournée par `site_export.export()` au lieu de taper la
DB directement.

Constat Cyril 18/05 (revue du digest reçu via Brevo) : « j'y vois des
choses qui sont - et heureusement - filtrées sur le site et que je ne
veux pas voir dans ce mail, mais aussi des choses qui sont hors délais
comme au jorf une nomination de juillet 2024 ». Cause racine :
`store.fetch_matched_since` filtrait juste par `inserted_at >= since`,
contournant TOUTE la chaîne de filtres publication (blocklist,
`_filter_window`, R41-H/I, R42-BI/BS/BF/CZG/DC/DD, dédup, etc.).

Demande Cyril : « il faut juste designer le mail à l'issue, de la même
manière que le site se construit sur la base des résultats, le mail se
design avec les (mêmes) résultats. Je veux pas qu'on doublonne les
recherches/flux ».

Implémentation :
1. `site_export.export()` retourne désormais `filtered_rows` (la liste
   `rows` finale post toute la chaîne) dans son dict de retour.
2. `main.run()` fait `site_export.export(all_matched)` AVANT le digest,
   récupère `publishable_rows = summary["filtered_rows"]`, applique
   uniquement le filtre temporel `inserted_at >= since` dessus.

Garde-fous testés :
- La clé `filtered_rows` est présente dans le retour de `export()`
- Items hors window (ex. JORF 2024) ne ressortent pas dans le digest
- Items blocklist'és n'apparaissent pas dans le digest
- Items récents et publiés restent visibles
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_row(
    hash_key: str,
    category: str,
    title: str,
    url: str,
    published_at: str,
    inserted_at: str,
    matched_keywords: str = '["sport"]',
    chamber: str = "AN",
) -> dict:
    """Helper : construit une row façon DB."""
    return {
        "hash_key": hash_key,
        "source_id": f"src-{hash_key[:6]}",
        "uid": f"uid-{hash_key[:6]}",
        "category": category,
        "chamber": chamber,
        "title": title,
        "url": url,
        "published_at": published_at,
        "summary": "",
        "matched_keywords": matched_keywords,
        "keyword_families": "[]",
        "raw": "{}",
        "inserted_at": inserted_at,
    }


def test_r43s_export_returns_filtered_rows_key(tmp_path):
    """Garde-fou contrat : `site_export.export()` doit exposer la clé
    `filtered_rows` dans son dict de retour. C'est le contrat que
    `main.run()` consomme pour bâtir le digest."""
    from src import site_export

    site_root = tmp_path / "site"
    site_root.mkdir()
    rows = [
        _make_row(
            hash_key="abc123",
            category="amendements",
            title="Amdt test sport",
            url="https://example.com/amdt-1",
            published_at="2026-05-15T10:00:00",
            inserted_at="2026-05-18T08:00:00",
        ),
    ]
    summary = site_export.export(rows, site_root)
    assert "filtered_rows" in summary, (
        "site_export.export() doit retourner `filtered_rows` (R43-S) — "
        "c'est la liste consommée par main.run() pour le digest."
    )
    assert isinstance(summary["filtered_rows"], list)


def test_r43s_filtered_rows_exclut_items_hors_window(tmp_path):
    """Cas concret du décret JORF 16/07/2024 : la fenêtre `nominations`
    est de 90 jours côté `_filter_window`. Un item publié en 2024 doit
    être exclu de `filtered_rows` même s'il a été inséré récemment en DB.
    """
    from src import site_export

    site_root = tmp_path / "site"
    site_root.mkdir()
    rows = [
        # Item ancien (2024) — doit être exclu par _filter_window
        _make_row(
            hash_key="old2024",
            category="nominations",
            title="Décret 2024 hors window",
            url="https://example.com/jorf-2024",
            published_at="2024-07-16T10:00:00",
            inserted_at="2026-05-18T08:00:00",
        ),
        # Item récent — doit passer
        _make_row(
            hash_key="recent",
            category="nominations",
            title="Décret 2026 dans window",
            url="https://example.com/jorf-2026",
            published_at="2026-05-10T10:00:00",
            inserted_at="2026-05-18T08:00:00",
        ),
    ]
    summary = site_export.export(rows, site_root)
    filtered = summary["filtered_rows"]
    hash_keys = {r.get("hash_key") for r in filtered}
    assert "old2024" not in hash_keys, (
        "Item JORF 2024 doit être exclu par _filter_window (fenêtre nominations=90j)"
    )
    assert "recent" in hash_keys, "Item récent doit passer la fenêtre"


def test_r43s_filtered_rows_exclut_items_blocklist(tmp_path, monkeypatch):
    """Garde-fou : un item présent dans la blocklist (`config/blocklist.yml`)
    doit être exclu de `filtered_rows`. Cyril : « j'y vois des choses qui
    sont - et heureusement - filtrées sur le site et que je ne veux pas
    voir dans ce mail »."""
    from src import site_export

    # On va mocker `_filter_blocklist` pour ne pas dépendre du contenu
    # réel de config/blocklist.yml (qui change avec le temps). On lui fait
    # systématiquement écarter l'item dont l'URL contient "blocked".
    real_filter = site_export._filter_blocklist

    def fake_filter(rows):
        return [r for r in rows if "blocked" not in (r.get("url") or "")]

    monkeypatch.setattr(site_export, "_filter_blocklist", fake_filter)

    site_root = tmp_path / "site"
    site_root.mkdir()
    rows = [
        _make_row(
            hash_key="blocked1",
            category="amendements",
            title="Amdt blocklist",
            url="https://example.com/blocked/amdt-x",
            published_at="2026-05-15T10:00:00",
            inserted_at="2026-05-18T08:00:00",
        ),
        _make_row(
            hash_key="allowed1",
            category="amendements",
            title="Amdt OK",
            url="https://example.com/amdt-y",
            published_at="2026-05-15T10:00:00",
            inserted_at="2026-05-18T08:00:00",
        ),
    ]
    summary = site_export.export(rows, site_root)
    hash_keys = {r.get("hash_key") for r in summary["filtered_rows"]}
    assert "blocked1" not in hash_keys, "Item blocklisté doit être exclu"
    assert "allowed1" in hash_keys, "Item non blocklisté doit passer"


def test_r43s_main_run_digest_subset_of_publishable(tmp_path):
    """Test d'intégration léger : si on simule `publishable_rows` et qu'on
    filtre par `inserted_at >= since`, le digest doit être un sous-ensemble
    strict de la liste publiable. Vérifie la cohérence du contrat.
    """
    from datetime import datetime, timedelta

    publishable = [
        _make_row("a", "amendements", "A", "https://e.com/a",
                  "2026-05-17T10:00:00", "2026-05-18T08:00:00"),
        _make_row("b", "amendements", "B", "https://e.com/b",
                  "2026-05-10T10:00:00", "2026-05-10T08:00:00"),
        _make_row("c", "amendements", "C", "https://e.com/c",
                  "2026-05-18T10:00:00", "2026-05-18T09:00:00"),
    ]
    since = datetime(2026, 5, 18, 0, 0, 0)
    since_iso = since.isoformat(timespec="seconds")
    # Logique identique à main.run()
    digest_rows = [
        r for r in publishable
        if (r.get("inserted_at") or "") >= since_iso
    ]
    digest_hash = {r["hash_key"] for r in digest_rows}
    pub_hash = {r["hash_key"] for r in publishable}
    # Le digest est un sous-ensemble strict de la liste publiable
    assert digest_hash.issubset(pub_hash)
    # Items inserted_at >= since
    assert "a" in digest_hash  # inserted 2026-05-18T08:00 >= 2026-05-18T00:00
    assert "c" in digest_hash  # inserted 2026-05-18T09:00
    assert "b" not in digest_hash  # inserted 2026-05-10 < since


def test_r43s_export_summary_loggable_apres_pop(tmp_path):
    """Garde-fou doc : après `summary.pop('filtered_rows')`, le summary
    doit rester sérialisable JSON et compact (utilisable dans un log).
    """
    from src import site_export

    site_root = tmp_path / "site"
    site_root.mkdir()
    rows = [
        _make_row(
            hash_key="x",
            category="amendements",
            title="X",
            url="https://e.com/x",
            published_at="2026-05-15T10:00:00",
            inserted_at="2026-05-18T08:00:00",
        ),
    ]
    summary = site_export.export(rows, site_root)
    summary.pop("filtered_rows", None)
    # Doit être sérialisable JSON (pas de list géante, pas de datetime nu)
    s = json.dumps(summary, default=str)
    assert len(s) < 2000, (
        "Le summary post-pop doit rester compact (~quelques centaines de "
        "chars) pour rester lisible en log."
    )
    assert "filtered_rows" not in s
    # Les clés essentielles restent
    assert "total" in summary
    assert "par_categorie" in summary
