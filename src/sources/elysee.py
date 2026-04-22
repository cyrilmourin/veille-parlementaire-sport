"""Connecteur Élysée — RSS officiel + agenda HTML dédié.

R16 (2026-04-22) — Refonte complète du connecteur après audit 0 items
R15 sur `elysee_sitemap` et `elysee_agenda`.

État observé côté elysee.fr (vérifié live 2026-04-22) :
  - /sitemap.static.xml → 200 OK mais seulement 10 URLs de navigation
    (home, agenda, lettre-information, recherche, contact…). Pas
    d'articles. → ancien `_from_sitemap` produisait 0 item.
  - /sitemap.xml → sitemap index pointant vers publication / sp /
    dossier / space / president / static. `sitemap.publication.xml`
    expose 15184 URLs mais tous les <lastmod> sont identiques
    (2026-03-18T02:00:09+00:00) — artefact de régénération. Impossible
    de filtrer par récence.
  - /feed → 200 OK, RSS 2.0, 34 Ko, items récents avec vrai <pubDate>,
    <title>, <description>, <link>. C'est le flux canonique.
  - /agenda → 200 OK, 296 Ko. Grille d'événements rendue côté serveur
    via <li class="newsBlock-grid-item"> / <a class="newsBlock-grid-
    link">. Ancien `_from_html_listing` cherchait `/agenda/` et
    `/actualites/` dans les href — aucun match sur la classe effective.

Stratégie retenue :
  - format: rss (`elysee_feed`) → déléguer à _from_rss_generic de
    html_generic (cohérence avec autres flux RSS gouvernementaux).
    Avantage : chamber "Elysee" déjà mappé dans html_generic._chamber.
  - format: elysee_agenda_html → handler dédié ci-dessous qui connaît
    la structure réelle de la page agenda (newsBlock-grid-link).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Item
from ._common import fetch_text

log = logging.getLogger(__name__)


# Mois français pour parser les dates "17 avril 2026" de la grille agenda.
_MONTHS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12, "decembre": 12,
}
_DATE_FR_PAT = re.compile(
    r"\b(\d{1,2})\s+("
    + "|".join(_MONTHS_FR.keys())
    + r")\s+(\d{4})\b",
    re.IGNORECASE,
)


def fetch_source(src: dict) -> list[Item]:
    """Dispatcher local Élysée.

    - `rss` : flux RSS `/feed` (officiel). Délègue au parser RSS
      générique (feedparser) via un appel direct à `_from_rss_generic`.
    - `elysee_agenda_html` : grille agenda présidence (handler dédié).
    - `sitemap` / `html` : anciens formats. On les laisse pour
      rétro-compat mais ils produisent 0 item sur la nouvelle arbo.
    """
    fmt = src.get("format")
    if fmt == "rss":
        # Import local pour éviter un cycle d'import en tête de fichier.
        from .html_generic import _from_rss_generic
        return _from_rss_generic(src)
    if fmt == "elysee_agenda_html":
        return _from_agenda_html(src)
    # Rétro-compat : anciens `sitemap` / `html`.
    if fmt == "sitemap":
        log.warning("elysee sitemap déprécié — préférer format=rss /feed")
        return []
    if fmt == "html":
        log.warning("elysee html déprécié — préférer format=elysee_agenda_html")
        return []
    return []


def _from_agenda_html(src: dict) -> list[Item]:
    """Scrape la page agenda : <li class='newsBlock-grid-item'> avec
    <a class='newsBlock-grid-link'> contenant date FR texte + <span>
    pour le titre. Un seul fetch, pas de pagination nécessaire
    (la page expose les prochaines ~20 dates présidentielles).
    """
    try:
        html = fetch_text(src["url"])
    except Exception as e:
        log.error("Élysée agenda KO: %s", e)
        return []

    soup = BeautifulSoup(html, "html.parser")
    out: list[Item] = []
    seen: set[str] = set()
    base = src["url"]
    category = src["category"]
    # Fenêtre : on garde tout ce qui a une date entre J-30 et J+90.
    # L'agenda présidence publie surtout du futur mais peut contenir
    # quelques déplacements de la semaine écoulée.
    now = datetime.utcnow()
    low = now - timedelta(days=30)
    high = now + timedelta(days=90)

    for a in soup.select("a.newsBlock-grid-link"):
        href = a.get("href") or ""
        if not href or href.startswith("#"):
            continue
        url = urljoin(base, href)
        if url in seen:
            continue
        seen.add(url)
        # Titre : le <span> interne. À défaut, texte complet du <a>
        # (mais alors la date y sera préfixée).
        span = a.find("span")
        if span and span.get_text(strip=True):
            title = span.get_text(" ", strip=True)
        else:
            title = a.get_text(" ", strip=True)
        if not title or len(title) < 4:
            continue
        # Date : "17 avril 2026" quelque part dans le texte du lien.
        text = a.get_text(" ", strip=True)
        dt = None
        m = _DATE_FR_PAT.search(text)
        if m:
            try:
                dt = datetime(int(m.group(3)),
                              _MONTHS_FR[m.group(2).lower()],
                              int(m.group(1)))
            except (ValueError, KeyError):
                dt = None
        # Fallback : date dans l'URL (ex. /emmanuel-macron/2026/04/17/slug).
        if dt is None:
            m2 = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
            if m2:
                try:
                    dt = datetime(int(m2.group(1)), int(m2.group(2)),
                                  int(m2.group(3)))
                except ValueError:
                    dt = None
        if dt is not None and (dt < low or dt > high):
            continue
        out.append(Item(
            source_id=src["id"],
            uid=url,
            category=category,
            chamber="Elysee",
            title=title[:220],
            url=url,
            published_at=dt,
            summary="",
            raw={"path": "elysee_agenda_html"},
        ))
    log.info("elysee_agenda : %d items (grille newsBlock)", len(out))
    return out
