"""Dispatcher : lit config/sources.yml et route chaque source vers son connecteur.

Expose `run_all(config_path)` qui renvoie la liste plate des Item collectés,
avec un log par source et une résilience face aux erreurs réseau (chaque
source est isolée dans son try/except).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import yaml

from .models import Item
from .sources import assemblee, elysee, html_generic, piste, senat

log = logging.getLogger(__name__)

# Table de routage : (groupe, format) -> fonction fetch_source
ROUTER: list[tuple[Callable[[dict, dict], bool], Callable[[dict], list[Item]]]] = [
    # Assemblée nationale — zips JSON
    (lambda group, src: group == "assemblee_nationale", assemblee.fetch_source),
    # Sénat — CSV/ZIP/RSS
    (lambda group, src: group == "senat", senat.fetch_source),
    # PISTE — Légifrance / JORF
    (lambda group, src: group == "piste", piste.fetch_source),
    # Élysée — sitemap / html dédiés
    (
        lambda group, src: group == "executif" and src["id"].startswith("elysee"),
        elysee.fetch_source,
    ),
    # Tout le reste (matignon, info_gouv, ministères, autorités) -> scraper HTML générique
    (lambda group, src: True, html_generic.fetch_source),
]


def _dispatch(group: str, src: dict) -> Callable[[dict], list[Item]]:
    for predicate, fn in ROUTER:
        if predicate(group, src):
            return fn
    return html_generic.fetch_source


def iter_sources(config: dict):
    """Itère sur (group_name, source_dict) pour chaque source du YAML."""
    for group_name, group in config.items():
        if not isinstance(group, dict):
            continue
        sources = group.get("sources") or []
        for src in sources:
            yield group_name, src


def _fetch_one(group: str, src: dict) -> tuple[str, list[Item], str | None]:
    fn = _dispatch(group, src)
    try:
        items = fn(src) or []
        return src["id"], items, None
    except Exception as e:
        log.exception("Fetch KO %s : %s", src["id"], e)
        return src["id"], [], str(e)


def run_all(config_path: str | Path, parallel: int = 6) -> tuple[list[Item], dict]:
    """Charge la config, parallélise les fetchs, renvoie (items, stats)."""
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    jobs = list(iter_sources(cfg))
    log.info("Pipeline : %d sources à interroger", len(jobs))
    all_items: list[Item] = []
    stats: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_fetch_one, g, s): (g, s) for g, s in jobs}
        for fut in as_completed(futures):
            sid, items, err = fut.result()
            stats[sid] = {"fetched": len(items), "error": err}
            all_items.extend(items)

    log.info("Pipeline : %d items au total", len(all_items))
    return all_items, stats
