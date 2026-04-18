"""Export JSON + Markdown Hugo pour le site statique veille.sideline-conseil.fr.

Structure produite :

    site/data/index.json            - tous les items matchés (avec meta)
    site/data/by_category/{cat}.json
    site/data/by_chamber/{cham}.json
    site/content/_index.md          - page d'accueil avec stats
    site/content/items/{cat}/{slug}.md - une page par item matché
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .digest import CATEGORY_LABELS, CATEGORY_ORDER


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s or "").strip("-").lower()
    return s[:80] or "item"


def _load(rows: list[dict]) -> list[dict]:
    """Parse les colonnes JSON-string vers des objets."""
    out = []
    for r in rows:
        r = dict(r)
        try:
            r["matched_keywords"] = json.loads(r.get("matched_keywords") or "[]")
        except Exception:
            r["matched_keywords"] = []
        try:
            r["keyword_families"] = json.loads(r.get("keyword_families") or "[]")
        except Exception:
            r["keyword_families"] = []
        out.append(r)
    return out


def _group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        v = r.get(key) or "autre"
        buckets.setdefault(v, []).append(r)
    return buckets


def export(rows: list[dict], site_root: str | Path) -> dict:
    """Écrit les fichiers JSON + Markdown dans le site/ Hugo.

    Renvoie un petit résumé {total, par_categorie, par_chambre}.
    """
    root = Path(site_root)
    data = root / "data"
    content = root / "content"
    items_dir = content / "items"
    data.mkdir(parents=True, exist_ok=True)
    (data / "by_category").mkdir(parents=True, exist_ok=True)
    (data / "by_chamber").mkdir(parents=True, exist_ok=True)
    items_dir.mkdir(parents=True, exist_ok=True)

    rows = _load(rows)

    # Index global
    index_payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "total": len(rows),
        "items": rows,
    }
    (data / "index.json").write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # Par catégorie
    by_cat = _group(rows, "category")
    for cat, lst in by_cat.items():
        (data / "by_category" / f"{cat}.json").write_text(
            json.dumps(lst, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    # Par chambre
    by_cham = _group(rows, "chamber")
    for cham, lst in by_cham.items():
        (data / "by_chamber" / f"{_slugify(cham)}.json").write_text(
            json.dumps(lst, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    # Page d'accueil
    _write_home(content, rows, by_cat)

    # Une page par item matché (pour l'URL permalien)
    _write_item_pages(items_dir, rows)

    return {
        "total": len(rows),
        "par_categorie": {k: len(v) for k, v in by_cat.items()},
        "par_chambre": {k: len(v) for k, v in by_cham.items()},
    }


def _write_home(content_dir: Path, rows: list[dict], by_cat: dict[str, list[dict]]):
    now = datetime.now()
    lines = [
        "---",
        f'title: "Veille parlementaire sport — {now:%Y-%m-%d}"',
        f'date: {now.isoformat(timespec="seconds")}',
        'description: "Veille institutionnelle du sport — actualisée quotidiennement par Sideline Conseil."',
        "---",
        "",
        f"**{len(rows)} publications officielles** correspondent aux mots-clés de la veille.",
        "Dernière mise à jour : " + now.strftime("%A %d %B %Y — %H:%M").capitalize() + ".",
        "",
    ]
    for cat in CATEGORY_ORDER:
        if cat not in by_cat:
            continue
        label = CATEGORY_LABELS.get(cat, cat)
        lines.append(f"## {label} ({len(by_cat[cat])})")
        lines.append("")
        for it in by_cat[cat][:30]:
            date = (it.get("published_at") or "")[:10]
            title = (it.get("title") or "").replace("\n", " ").strip()
            url = it.get("url") or "#"
            chamber = it.get("chamber") or ""
            lines.append(f"- **[{title}]({url})** — {chamber}{' — ' + date if date else ''}")
        lines.append("")
    (content_dir / "_index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_item_pages(items_dir: Path, rows: list[dict]):
    # On évite l'explosion du nombre de fichiers : on garde les 500 plus récents.
    rows_sorted = sorted(rows, key=lambda r: r.get("inserted_at") or "", reverse=True)
    for r in rows_sorted[:500]:
        cat = r.get("category") or "autre"
        d = items_dir / cat
        d.mkdir(parents=True, exist_ok=True)
        slug = _slugify(f"{r.get('source_id','')}-{r.get('uid','')}-{r.get('title','')[:40]}")
        fp = d / f"{slug}.md"
        title = (r.get("title") or "").replace('"', "'")
        date = r.get("published_at") or r.get("inserted_at") or ""
        lines = [
            "---",
            f'title: "{title}"',
            f"date: {date}",
            f"category: {cat}",
            f"chamber: {r.get('chamber') or ''}",
            f"source: {r.get('source_id') or ''}",
            f"url: {r.get('url') or ''}",
            f"keywords: {json.dumps(r.get('matched_keywords') or [], ensure_ascii=False)}",
            f"families: {json.dumps(r.get('keyword_families') or [], ensure_ascii=False)}",
            "---",
            "",
            (r.get("summary") or "").strip(),
            "",
            f"[Consulter la source officielle]({r.get('url') or '#'})",
        ]
        fp.write_text("\n".join(lines), encoding="utf-8")
