"""Connecteur Élysée — parsing du sitemap + scraping rubriques."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from lxml import etree

from ..models import Item
from ._common import fetch_text, parse_iso

log = logging.getLogger(__name__)


def fetch_source(src: dict) -> list[Item]:
    fmt = src.get("format")
    if fmt == "sitemap":
        return _from_sitemap(src)
    if fmt == "html":
        return _from_html_listing(src)
    return []


def _from_sitemap(src: dict) -> list[Item]:
    try:
        raw = fetch_text(src["url"])
    except Exception as e:
        log.error("Élysée sitemap KO: %s", e)
        return []

    root = etree.fromstring(raw.encode("utf-8"))
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    rubriques = set(src.get("rubriques") or [])
    out: list[Item] = []
    cutoff = datetime.now() - timedelta(days=30)
    for url_node in root.findall("s:url", ns):
        loc = (url_node.findtext("s:loc", namespaces=ns) or "").strip()
        last = parse_iso((url_node.findtext("s:lastmod", namespaces=ns) or "").strip())
        if not loc:
            continue
        # filtre rubriques
        if rubriques and not any(r in loc for r in rubriques):
            continue
        # URL typique : https://www.elysee.fr/emmanuel-macron/2026/04/17/...
        m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", loc)
        if m and not last:
            last = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if last and last.replace(tzinfo=None) < cutoff.replace(tzinfo=None):
            continue
        title = loc.rsplit("/", 1)[-1].replace("-", " ").strip() or "Élysée"
        # catégorie Follaw.sv
        category = src["category"]
        if "nomination" in loc.lower():
            category = "nominations"
        out.append(Item(
            source_id=src["id"], uid=loc, category=category, chamber="Elysee",
            title=title[:220].capitalize(),
            url=loc,
            published_at=last,
            summary="",
            raw={"lastmod": str(last) if last else ""},
        ))
    log.info("Élysée sitemap : %d items retenus", len(out))
    return out


def _from_html_listing(src: dict) -> list[Item]:
    try:
        html = fetch_text(src["url"])
    except Exception as e:
        log.error("Élysée HTML KO: %s", e)
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[Item] = []
    for a in soup.select("a"):
        href = a.get("href") or ""
        if "/agenda/" not in href and "/actualites/" not in href:
            continue
        url = urljoin(src["url"], href)
        title = (a.get_text(" ", strip=True) or "")[:220]
        if not title:
            continue
        out.append(Item(
            source_id=src["id"], uid=url, category=src["category"], chamber="Elysee",
            title=title, url=url, published_at=None, summary="",
        ))
    return out
