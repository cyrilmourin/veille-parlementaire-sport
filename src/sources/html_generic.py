"""Connecteur HTML générique — ministères, autorités, Matignon, info.gouv…

Stratégie : on télécharge la page d'atterrissage (listing presse / actualités),
on sélectionne les <a> portant un titre lisible, et on retient la date si on
la trouve dans le lien parent ou via un <time datetime>.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..models import Item
from ._common import fetch_text, parse_iso

log = logging.getLogger(__name__)


_DATE_PAT = re.compile(r"(\d{4})[-/](\d{2})[-/](\d{2})")


def _chamber(domain: str) -> str:
    d = domain.lower()
    if "sports.gouv.fr" in d:
        return "MinSports"
    if "elysee.fr" in d:
        return "Elysee"
    if "gouvernement.fr" in d or "info.gouv.fr" in d:
        return "Matignon"
    if "afld.fr" in d:
        return "AFLD"
    if "agencedusport" in d:
        return "ANS"
    if "arcom.fr" in d:
        return "ARCOM"
    if "anj.fr" in d:
        return "ANJ"
    if "ccomptes.fr" in d:
        return "CourComptes"
    if "defenseurdesdroits" in d:
        return "DDD"
    if "franceolympique" in d:
        return "CNOSF"
    if "france-paralympique" in d:
        return "CPSF"
    if "cojop" in d:
        return "Alpes2030"
    if ".gouv.fr" in d:
        return d.split(".")[0].capitalize()
    return d


def fetch_source(src: dict) -> list[Item]:
    try:
        html = fetch_text(src["url"])
    except Exception as e:
        log.warning("HTML KO %s : %s", src["id"], e)
        return []

    soup = BeautifulSoup(html, "html.parser")
    base = src["url"]
    domain = urlparse(base).netloc
    chamber = _chamber(domain)
    out: list[Item] = []
    seen: set[str] = set()

    # On cible les liens d'articles : <article> <a>, <h2> <a>, liens de type /presse/..., /actualites/...
    selectors = [
        "article a", "h2 a", "h3 a",
        "a.fr-card__link", "a.news-item__link",
        "a[href*='presse']", "a[href*='actualite']", "a[href*='communique']",
        "a[href*='discours']", "a[href*='agenda']",
    ]
    for sel in selectors:
        for a in soup.select(sel):
            href = a.get("href") or ""
            if not href or href.startswith("#"):
                continue
            url = urljoin(base, href)
            if urlparse(url).netloc != domain:
                continue
            if url in seen:
                continue
            title = (a.get_text(" ", strip=True) or "")[:240]
            if not title or len(title) < 5:
                continue
            # date : on scrute les ancêtres pour un <time>
            dt = None
            for anc in a.parents:
                if anc is None or anc is soup:
                    break
                t = anc.find("time") if hasattr(anc, "find") else None
                if t and t.get("datetime"):
                    dt = parse_iso(t["datetime"])
                    break
                m = _DATE_PAT.search(str(anc.get("data-date") or ""))
                if m:
                    dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    break
            seen.add(url)
            out.append(Item(
                source_id=src["id"], uid=url, category=src["category"], chamber=chamber,
                title=title, url=url, published_at=dt, summary="",
            ))
    log.info("%s : %d items", src["id"], len(out))
    return out
