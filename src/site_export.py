"""Export JSON + Markdown Hugo pour le site statique veille.sideline-conseil.fr.

Structure produite :

    site/data/index.json                    — tous les items matchés (≤ 30 j)
    site/data/by_category/{cat}.json        — regroupement par catégorie
    site/data/by_chamber/{cham}.json        — regroupement par chambre
    site/content/_index.md                  — page d'accueil (zone <24h puis 30j)
    site/content/items/{cat}/_index.md      — page de listing catégorie
    site/content/items/{cat}/{slug}.md      — une page par item matché
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .digest import CATEGORY_LABELS, CATEGORY_ORDER

# Fenêtre de publication visible sur le site (jours).
WINDOW_DAYS = 30
# Sous-fenêtre "mises à jour du jour" pour le haut de la home.
RECENT_HOURS = 24


def _slugify(s: str) -> str:
    s = s or ""
    # Retire les schémas d'URL pour qu'ils ne polluent pas les slugs
    s = re.sub(r"https?://", "", s, flags=re.IGNORECASE)
    s = re.sub(r"www\.", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] or "item"


def _parse_dt(value) -> datetime | None:
    """Parse best-effort d'un datetime stocké en string ISO."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        # fallback : juste la date
        try:
            return datetime.fromisoformat(s[:10])
        except Exception:
            return None


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


def _filter_window(rows: list[dict]) -> list[dict]:
    """Garde uniquement les items publiés dans la fenêtre WINDOW_DAYS.
    Si la date de publication est absente, on garde par défaut (utile pour les
    sources sans date fiable — ces items seront classés 'autre')."""
    cutoff = datetime.utcnow() - timedelta(days=WINDOW_DAYS)
    kept = []
    for r in rows:
        dt = _parse_dt(r.get("published_at"))
        if dt is None:
            # On tente la date d'insertion en base comme fallback
            dt = _parse_dt(r.get("inserted_at"))
        if dt is None:
            # Aucune date — on skip plutôt que de polluer avec de l'ancien
            continue
        if dt >= cutoff:
            kept.append(r)
    return kept


def _group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        v = r.get(key) or "autre"
        buckets.setdefault(v, []).append(r)
    return buckets


def _sort_by_date_desc(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (_parse_dt(r.get("published_at")) or _parse_dt(r.get("inserted_at")) or datetime.min),
        reverse=True,
    )


def export(rows: list[dict], site_root: str | Path) -> dict:
    """Écrit les fichiers JSON + Markdown dans le site/ Hugo.

    Renvoie {total, par_categorie, par_chambre, recent_24h}.
    """
    root = Path(site_root)
    data = root / "data"
    content = root / "content"
    items_dir = content / "items"
    data.mkdir(parents=True, exist_ok=True)
    (data / "by_category").mkdir(parents=True, exist_ok=True)
    (data / "by_chamber").mkdir(parents=True, exist_ok=True)
    items_dir.mkdir(parents=True, exist_ok=True)

    # Charge + filtre 30 jours glissants + tri date desc
    rows = _load(rows)
    rows = _filter_window(rows)
    rows = _sort_by_date_desc(rows)

    # Index global
    index_payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "window_days": WINDOW_DAYS,
        "total": len(rows),
        "items": rows,
    }
    (data / "index.json").write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    by_cat = _group(rows, "category")
    for cat, lst in by_cat.items():
        (data / "by_category" / f"{cat}.json").write_text(
            json.dumps(lst, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    by_cham = _group(rows, "chamber")
    for cham, lst in by_cham.items():
        (data / "by_chamber" / f"{_slugify(cham)}.json").write_text(
            json.dumps(lst, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    # Page d'accueil
    recent = _recent(rows, hours=RECENT_HOURS)
    _write_home(content, rows, by_cat, recent)

    # Page de listing par catégorie (_index.md) — nécessaire pour que
    # /items/amendements/ etc. ne donne pas un 404.
    _write_category_indexes(items_dir, by_cat)

    # Une page par item matché
    _write_item_pages(items_dir, rows)

    return {
        "total": len(rows),
        "par_categorie": {k: len(v) for k, v in by_cat.items()},
        "par_chambre": {k: len(v) for k, v in by_cham.items()},
        "recent_24h": len(recent),
        "window_days": WINDOW_DAYS,
    }


def _recent(rows: list[dict], hours: int = 24) -> list[dict]:
    """Items publiés (officiellement) dans les dernières `hours` heures.
    On utilise strictement `published_at` ici — pas `inserted_at` —
    pour que la zone 'dernières 24h' reflète la publication institutionnelle
    réelle, pas la date à laquelle le scraper a inséré en base."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    out = []
    for r in rows:
        dt = _parse_dt(r.get("published_at"))
        if dt and dt >= cutoff:
            out.append(r)
    return out


# ---------- écritures Markdown ---------------------------------------------

def _fmt_item_line(it: dict) -> str:
    """Ligne Markdown d'un item dans un listing (home / catégorie)."""
    date = (it.get("published_at") or "")[:10]
    title = (it.get("title") or "").replace("\n", " ").strip()
    url = it.get("url") or "#"
    chamber = it.get("chamber") or ""
    kws = it.get("matched_keywords") or []
    snippet = (it.get("snippet") or "").replace("\n", " ").strip()
    parts = [f"- **[{title}]({url})**"]
    meta_bits = []
    if chamber:
        meta_bits.append(chamber)
    if date:
        meta_bits.append(date)
    if kws:
        meta_bits.append("mots-clés : " + ", ".join(kws[:5]))
    if meta_bits:
        parts.append(" — " + " · ".join(meta_bits))
    line = "".join(parts)
    if snippet:
        line += f"  \n  <small>« {snippet} »</small>"
    return line


def _write_home(content_dir: Path, rows: list[dict], by_cat: dict[str, list[dict]],
                recent: list[dict]):
    now = datetime.now()
    lines = [
        "---",
        f'title: "Veille parlementaire sport — {now:%Y-%m-%d}"',
        f'date: {now.isoformat(timespec="seconds")}',
        'description: "Veille institutionnelle du sport — actualisée quotidiennement par Sideline Conseil."',
        "---",
        "",
        f"**{len(rows)} publications officielles** dans la fenêtre des {WINDOW_DAYS} derniers jours.",
        "Dernière mise à jour : " + now.strftime("%A %d %B %Y — %H:%M").capitalize() + ".",
        "",
    ]

    # -------- Section top : mises à jour des dernières 24 h ----------
    lines.append(f"## Dernières 24 h ({len(recent)})")
    lines.append("")
    if recent:
        for it in recent[:30]:
            lines.append(_fmt_item_line(it))
    else:
        lines.append("_Aucune nouveauté dans les dernières 24 heures — la collecte reste active._")
    lines.append("")

    # -------- Sections par catégorie (fenêtre 30 j) ------------------
    lines.append(f"## Derniers {WINDOW_DAYS} jours, par thématique")
    lines.append("")
    for cat in CATEGORY_ORDER:
        if cat not in by_cat:
            continue
        label = CATEGORY_LABELS.get(cat, cat)
        bucket = by_cat[cat]
        # Lien vers la page catégorie pour consultation exhaustive
        lines.append(f"### [{label}](/items/{cat}/) ({len(bucket)})")
        lines.append("")
        for it in bucket[:15]:
            lines.append(_fmt_item_line(it))
        if len(bucket) > 15:
            lines.append(f"")
            lines.append(f"→ [Voir les {len(bucket)} {label.lower()}](/items/{cat}/)")
        lines.append("")

    (content_dir / "_index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_category_indexes(items_dir: Path, by_cat: dict[str, list[dict]]):
    """Écrit un _index.md par catégorie pour que Hugo route /items/<cat>/."""
    for cat in CATEGORY_ORDER:
        d = items_dir / cat
        d.mkdir(parents=True, exist_ok=True)
        label = CATEGORY_LABELS.get(cat, cat)
        count = len(by_cat.get(cat, []))
        lines = [
            "---",
            f'title: "{label}"',
            f'description: "Veille {label.lower()} — {count} items sur {WINDOW_DAYS} jours glissants."',
            "---",
            "",
            f"{count} publication{'s' if count > 1 else ''} dans cette catégorie sur les {WINDOW_DAYS} derniers jours.",
            "",
        ]
        (d / "_index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_item_pages(items_dir: Path, rows: list[dict]):
    # On évite l'explosion du nombre de fichiers : on garde les 500 plus récents.
    rows_sorted = _sort_by_date_desc(rows)
    for r in rows_sorted[:500]:
        cat = r.get("category") or "autre"
        d = items_dir / cat
        d.mkdir(parents=True, exist_ok=True)
        slug = _slugify(f"{r.get('source_id','')}-{r.get('uid','')}-{r.get('title','')[:40]}")
        fp = d / f"{slug}.md"
        title = (r.get("title") or "").replace('"', "'")
        date = r.get("published_at") or r.get("inserted_at") or ""
        source_url = (r.get("url") or "").replace('"', "")
        snippet = (r.get("snippet") or "").replace('"', "'").replace("\n", " ")
        lines = [
            "---",
            f'title: "{title}"',
            f"date: {date}",
            f"category: {cat}",
            f'chamber: "{r.get("chamber") or ""}"',
            f'source: "{r.get("source_id") or ""}"',
            f'source_url: "{source_url}"',
            f"keywords: {json.dumps(r.get('matched_keywords') or [], ensure_ascii=False)}",
            f"families: {json.dumps(r.get('keyword_families') or [], ensure_ascii=False)}",
            f'snippet: "{snippet}"',
            "---",
            "",
            (r.get("summary") or "").strip(),
            "",
            f"[Consulter la source officielle]({source_url or '#'})",
        ]
        fp.write_text("\n".join(lines), encoding="utf-8")
