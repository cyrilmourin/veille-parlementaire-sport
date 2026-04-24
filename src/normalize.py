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
from .sources import (  # noqa: F401
    an_cr_commissions,
    assemblee,
    assemblee_rapports,
    data_gouv,
    dila_jorf,
    elysee,
    html_generic,
    min_sports,
    piste,
    senat,
    senat_commission_agenda,
)

log = logging.getLogger(__name__)

# Table de routage : (groupe, format) -> fonction fetch_source
ROUTER: list[tuple[Callable[[dict, dict], bool], Callable[[dict], list[Item]]]] = [
    # data.gouv.fr — routage par format (traverse les groupes YAML).
    # R15 (2026-04-22) : agendas ministériels bloqués par Cloudflare sur
    # gouvernement.fr/info.gouv.fr → contournement via data.gouv.fr
    # quand un dataset open data est disponible (cf. data_gouv.py).
    (
        lambda group, src: src.get("format", "").startswith("data_gouv_"),
        data_gouv.fetch_source,
    ),
    # Ministère des Sports — agenda hebdo de la ministre (scraper HTML
    # dédié, cf. `min_sports.py`). Route par format pour rester dans
    # le groupe `ministeres` sans forker la topologie YAML.
    (
        lambda group, src: src.get("format", "").startswith("min_sports_"),
        min_sports.fetch_source,
    ),
    # R28 (2026-04-23) — Rapports AN scrappés depuis la page HTML de
    # listing. Routé par format pour rester dans le groupe
    # `assemblee_nationale` sans forker la topologie YAML, et PRIORITAIRE
    # sur la règle `group == "assemblee_nationale"` ci-dessous qui
    # enverrait sinon au handler `assemblee.fetch_source` (format
    # json_zip / xml_zip uniquement).
    (
        lambda group, src: src.get("format") == "an_rapports_html",
        assemblee_rapports.fetch_source,
    ),
    # R35-B (2026-04-24) — Scraper CR de commissions AN. Routé par format
    # `an_cr_commissions` pour passer AVANT la règle générique `group ==
    # assemblee_nationale` qui pointerait sinon sur `assemblee.fetch_source`
    # (fait pour les dumps json_zip / xml_zip uniquement).
    (
        lambda group, src: src.get("format") == "an_cr_commissions",
        an_cr_commissions.fetch_source,
    ),
    # R35-E (2026-04-24) — Agenda HTML d'une commission Sénat (remplace
    # `senat_agenda_daily` désactivé depuis R15 parce que
    # /agenda/Global/agl*Print.html renvoie "Accès restreint" en prod).
    # Route par format pour passer AVANT `group == "senat"` qui enverrait
    # sinon sur `senat.fetch_source` (dédié aux formats CSV/AKN/RSS).
    (
        lambda group, src: src.get("format") == "senat_commission_agenda_html",
        senat_commission_agenda.fetch_source,
    ),
    # Assemblée nationale — zips JSON
    (lambda group, src: group == "assemblee_nationale", assemblee.fetch_source),
    # Sénat — CSV/ZIP/RSS
    (lambda group, src: group == "senat", senat.fetch_source),
    # DILA OPENDATA — dump JORF quotidien (remplace PISTE, sans credentials)
    (lambda group, src: group == "dila", dila_jorf.fetch_source),
    # PISTE — Légifrance / JORF via OAuth2 (gardé en réserve, non actif par défaut)
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
    """Itère sur (group_name, source_dict) pour chaque source du YAML.

    Une source peut être désactivée temporairement avec `enabled: false`
    dans son entrée YAML (ex. domaine cassé, Cloudflare challenge insoluble,
    timeout connect chronique). Elle est alors silencieusement skippée par
    la pipeline — utile pour stop-loss sans supprimer la conf.
    """
    for group_name, group in config.items():
        if not isinstance(group, dict):
            continue
        sources = group.get("sources") or []
        for src in sources:
            if src.get("enabled") is False:
                log.info("Source %s désactivée (enabled: false), skip", src.get("id", "?"))
                continue
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

    # Récap erreurs ET zero-hit. Sans ça, un an_amendements=0 silencieux
    # (cas R11 → R11d) passe inaperçu dans le bruit des logs quotidiens. On
    # émet un bloc WARNING visible qui liste les sources qui ont produit
    # zéro item, avec leur éventuelle erreur. À surveiller dans le run CI.
    errored = [(sid, s["error"]) for sid, s in stats.items() if s["error"]]
    empty = [sid for sid, s in stats.items() if s["error"] is None and s["fetched"] == 0]
    if errored:
        log.warning("Pipeline : %d source(s) en erreur :", len(errored))
        for sid, err in errored:
            log.warning("  [ERREUR] %s → %s", sid, err)
    if empty:
        log.warning(
            "Pipeline : %d source(s) à 0 item (sans erreur) — à auditer si persistant :",
            len(empty),
        )
        for sid in empty:
            log.warning("  [0 items] %s", sid)

    log.info("Pipeline : %d items au total", len(all_items))
    return all_items, stats
