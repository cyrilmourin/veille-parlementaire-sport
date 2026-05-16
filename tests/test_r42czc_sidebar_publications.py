"""R42-CZC (2026-05-16) — Module sidebar « Dernières publications ».

- Pour chaque catégorie pertinente (dosleg, amdt, questions, CR,
  agenda, jorf, nominations) : `site/data/sidebar_pub_<cat>.json` avec
  5 derniers items triés date desc.
- Fichier mix `sidebar_pub_all.json` (5 plus récents toutes catégories
  confondues, hors « communiques » qui correspond à la page Publications).
- Limite agenda principale ramenée de 8 à 4 items dans sidebar_agenda.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from src.site_export import export


def _mk(cat: str, n: int, base_date: datetime) -> list[dict]:
    """Crée n items synthétiques dans la catégorie `cat`."""
    rows = []
    for i in range(n):
        d = base_date - timedelta(days=i)
        rows.append({
            "source_id": f"src_{cat}",
            "uid": f"u_{cat}_{i}",
            "category": cat,
            "chamber": "AN",
            "title": f"Item {cat} #{i}",
            "url": f"https://example.com/{cat}/{i}",
            "published_at": d.isoformat(timespec="seconds"),
            "summary": "",
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
    out += _mk("communiques", 3, base)  # ne doit PAS apparaître dans `all`
    return out


def test_sidebar_pub_files_generated_for_each_category(tmp_path):
    rows = _seed_rows()
    export(rows, tmp_path)
    data = tmp_path / "data"
    for cat in (
        "dossiers_legislatifs", "amendements", "questions",
        "comptes_rendus", "agenda", "jorf", "nominations",
    ):
        f = data / f"sidebar_pub_{cat}.json"
        assert f.exists(), f"manque {f}"
        items = json.loads(f.read_text(encoding="utf-8"))
        assert len(items) <= 5, f"{cat} : {len(items)} > 5"


def test_sidebar_pub_all_excludes_communiques(tmp_path):
    rows = _seed_rows()
    export(rows, tmp_path)
    data = tmp_path / "data"
    items = json.loads((data / "sidebar_pub_all.json").read_text(encoding="utf-8"))
    assert len(items) == 5
    cats = {it["category"] for it in items}
    assert "communiques" not in cats, (
        "Le bucket `all` doit exclure communiques (= page Publications)"
    )


def test_sidebar_pub_items_sorted_date_desc(tmp_path):
    rows = _seed_rows()
    export(rows, tmp_path)
    data = tmp_path / "data"
    items = json.loads((data / "sidebar_pub_dossiers_legislatifs.json").read_text(encoding="utf-8"))
    dates = [it["published_at"] for it in items]
    assert dates == sorted(dates, reverse=True), (
        f"Items non triés date desc : {dates}"
    )


def test_sidebar_pub_item_schema(tmp_path):
    rows = _seed_rows()
    export(rows, tmp_path)
    data = tmp_path / "data"
    items = json.loads((data / "sidebar_pub_questions.json").read_text(encoding="utf-8"))
    assert items, "bucket questions vide"
    sample = items[0]
    for k in ("title", "url", "published_at", "chamber", "category"):
        assert k in sample, f"clé {k!r} manquante"


def test_sidebar_agenda_limited_to_4(tmp_path):
    rows = _seed_rows()
    export(rows, tmp_path)
    data = tmp_path / "data"
    items = json.loads((data / "sidebar_agenda.json").read_text(encoding="utf-8"))
    assert len(items) <= 4, f"sidebar_agenda doit ≤ 4 items, vu {len(items)}"
