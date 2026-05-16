"""R42-CZC/R42-CZF (2026-05-16) — Module sidebar « Dernières publications ».

R42-CZF (correction) : un seul flux = la catégorie `communiques` (=
rubrique « Publications »). Pas de matrice par catégorie comme tenté
en R42-CZC. Affichage côté template : visible partout SAUF sur
`/items/communiques/`.

Limite agenda principale ramenée de 8 à 4 items dans sidebar_agenda.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from src.site_export import export


_SOURCE_BY_CAT = {
    "dossiers_legislatifs": ("an_dossiers", "AN"),
    "amendements": ("an_amendements", "AN"),
    "questions": ("an_questions", "AN"),
    "comptes_rendus": ("an_comptes_rendus", "AN"),
    "agenda": ("an_agenda", "AN"),
    "jorf": ("dila_jorf", "JORF"),
    "nominations": ("dila_jorf_nominations", "JORF"),
    "communiques": ("elysee_feed", "Elysee"),
}


def _mk(cat: str, n: int, base_date: datetime) -> list[dict]:
    src, chamber = _SOURCE_BY_CAT.get(cat, (f"src_{cat}", "AN"))
    rows = []
    for i in range(n):
        d = base_date - timedelta(days=i)
        rows.append({
            "source_id": src,
            "uid": f"u_{cat}_{i}",
            "category": cat,
            "chamber": chamber,
            "title": f"Item {cat} #{i} sport",
            "url": f"https://example.com/{cat}/{i}",
            "published_at": d.isoformat(timespec="seconds"),
            "summary": "Sport olympique",
            "raw": {},
            "matched_keywords": ["sport"],
        })
    return rows


def _seed_rows() -> list[dict]:
    base = datetime(2026, 5, 16, 8, 0)
    out = []
    out += _mk("dossiers_legislatifs", 7, base)
    out += _mk("amendements", 7, base)
    out += _mk("questions", 7, base)
    out += _mk("comptes_rendus", 7, base)
    out += _mk("agenda", 7, base)
    out += _mk("jorf", 7, base)
    out += _mk("nominations", 7, base)
    out += _mk("communiques", 8, base)
    return out


def test_sidebar_publications_file_generated(tmp_path):
    rows = _seed_rows()
    export(rows, tmp_path)
    f = tmp_path / "data" / "sidebar_publications.json"
    assert f.exists(), f"manque {f}"
    items = json.loads(f.read_text(encoding="utf-8"))
    assert len(items) == 5, f"expected 5 items, got {len(items)}"


def test_sidebar_publications_only_communiques(tmp_path):
    rows = _seed_rows()
    export(rows, tmp_path)
    items = json.loads(
        (tmp_path / "data" / "sidebar_publications.json").read_text(encoding="utf-8")
    )
    cats = {it["category"] for it in items}
    assert cats == {"communiques"}, (
        f"Le module doit ne contenir QUE category=communiques, vu : {cats}"
    )


def test_sidebar_publications_sorted_date_desc(tmp_path):
    rows = _seed_rows()
    export(rows, tmp_path)
    items = json.loads(
        (tmp_path / "data" / "sidebar_publications.json").read_text(encoding="utf-8")
    )
    dates = [it["published_at"] for it in items]
    assert dates == sorted(dates, reverse=True)


def test_sidebar_publications_item_schema(tmp_path):
    rows = _seed_rows()
    export(rows, tmp_path)
    items = json.loads(
        (tmp_path / "data" / "sidebar_publications.json").read_text(encoding="utf-8")
    )
    assert items, "bucket vide"
    sample = items[0]
    for k in ("title", "url", "published_at", "chamber", "category"):
        assert k in sample, f"clé {k!r} manquante"


def test_no_per_category_pub_files_anymore(tmp_path):
    """R42-CZF : on n'écrit plus la matrice `sidebar_pub_<cat>.json` du
    proto R42-CZC. Sanity check pour éviter un retour en arrière
    accidentel.
    """
    rows = _seed_rows()
    export(rows, tmp_path)
    data = tmp_path / "data"
    for cat in ("dossiers_legislatifs", "amendements", "questions",
                "comptes_rendus", "agenda", "jorf", "nominations"):
        assert not (data / f"sidebar_pub_{cat}.json").exists(), (
            f"sidebar_pub_{cat}.json ne doit plus être généré (R42-CZF)"
        )
    assert not (data / "sidebar_pub_all.json").exists()


def test_sidebar_agenda_limited_to_4(tmp_path):
    rows = _seed_rows()
    export(rows, tmp_path)
    items = json.loads(
        (tmp_path / "data" / "sidebar_agenda.json").read_text(encoding="utf-8")
    )
    assert len(items) <= 4
