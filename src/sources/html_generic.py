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
# URL contenant une date : /2026/04/15/ ou /2026-04-15/
_DATE_IN_URL = re.compile(r"/(\d{4})[/-](\d{2})[/-](\d{2})/")
# Format français "15 avril 2026"
_MONTHS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}
_DATE_FR_PAT = re.compile(
    r"\b(\d{1,2})\s+("
    + "|".join(_MONTHS_FR.keys())
    + r")\s+(\d{4})\b",
    re.IGNORECASE,
)


def _extract_date(a, url: str) -> datetime | None:
    """Stratégie en cascade pour trouver la date d'un lien d'article.

    Ordre : <time> proche → data-date ancêtre → date dans l'URL →
    français dans le texte voisin → ISO dans le texte voisin → None.
    On reste dans un rayon de 3 ancêtres maximum pour éviter de remonter
    jusqu'à <body> et de capturer une date sans rapport.
    """
    # 1. <time datetime> dans un rayon proche (3 ancêtres max). On stoppe dès
    #    qu'on rencontre <body>/<html>/<main> — ces conteneurs globaux
    #    mélangent tous les articles et un <time> y piocherait la date
    #    d'un autre item. On n'explore que les descendants proches
    #    (profondeur ≤ 2) de chaque ancêtre retenu.
    _STOP_TAGS = {"body", "html", "main"}
    parents = []
    for anc in a.parents:
        if anc is None:
            break
        if getattr(anc, "name", None) in _STOP_TAGS:
            break
        parents.append(anc)
        if len(parents) >= 3:
            break

    def _close_time(anc) -> datetime | None:
        """Cherche un <time datetime> parmi les descendants directs de anc
        (profondeur ≤ 2) pour ne pas capter un <time> d'un autre article."""
        if not hasattr(anc, "children"):
            return None
        for child in anc.children:
            if getattr(child, "name", None) is None:
                continue
            if child.name == "time" and child.get("datetime"):
                dt = parse_iso(child["datetime"])
                if dt:
                    return dt
            # un cran plus bas
            if hasattr(child, "find"):
                t = child.find("time", recursive=False)
                if t and t.get("datetime"):
                    dt = parse_iso(t["datetime"])
                    if dt:
                        return dt
        return None

    for anc in parents:
        dt = _close_time(anc)
        if dt:
            return dt
        m = _DATE_PAT.search(str(anc.get("data-date") or ""))
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
    # 2. Date dans l'URL (ex. /2026/04/15/article-slug)
    m = _DATE_IN_URL.search(url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # 3. Date au format français "15 avril 2026" dans texte du lien ou
    #    des 2 premiers parents (on récupère leur texte complet).
    texts = [a.get_text(" ", strip=True) or ""]
    for anc in parents[:2]:
        if hasattr(anc, "get_text"):
            texts.append(anc.get_text(" ", strip=True) or "")
    for text in texts:
        m = _DATE_FR_PAT.search(text)
        if m:
            day = int(m.group(1))
            month = _MONTHS_FR[m.group(2).lower()]
            year = int(m.group(3))
            try:
                return datetime(year, month, day)
            except ValueError:
                pass
    # 4. Format ISO dans le texte (filet)
    for text in texts:
        m = _DATE_PAT.search(text)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
    return None


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
    # R13-J (2026-04-21) : senat_agenda scraper via html_generic (pas de flux
    # JSON/XML officiel pour l'agenda Sénat). Le domaine www.senat.fr ne
    # matche aucun des blocs spécifiques plus haut.
    if "senat.fr" in d:
        return "Senat"
    # R13-G (2026-04-21) : fix "Www" affiché pour tous les ministères dont
    # l'URL commence par www. (www.defense.gouv.fr, www.justice.gouv.fr,
    # etc.). `d.split(".")[0]` retournait toujours "www" → badge "Www" sur
    # le site. Cyril a signalé le cas defense → MinARMEES ; on corrige
    # l'ensemble avec un mapping explicite + un fallback qui strip "www.".
    if ".gouv.fr" in d:
        # Premier segment du FQDN après avoir retiré le "www." éventuel.
        key = d
        if key.startswith("www."):
            key = key[4:]
        key = key.split(".gouv.fr")[0]
        # Mapping des ministères connus — on privilégie un libellé Min{XXX}
        # majuscule pour cohérence avec "MinSports" (déjà actif au-dessus).
        _MIN_MAP = {
            "defense":                "MinARMEES",
            "justice":                "MinJUSTICE",
            "interieur":              "MinINTERIEUR",
            "culture":                "MinCULTURE",
            "education":              "MinEDUCATION",
            "economie":               "MinECO",
            "sante":                  "MinSANTE",
            "travail-emploi":         "MinTRAVAIL",
            "diplomatie":             "MinAFFAIRES",
            "enseignementsup-recherche": "MinESR",
            "cohesion-territoires":   "MinCOHESION",
        }
        return _MIN_MAP.get(key, key.capitalize())
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
            # date : cascade <time> → data-date → URL → texte français → ISO
            dt = _extract_date(a, url)
            seen.add(url)
            out.append(Item(
                source_id=src["id"], uid=url, category=src["category"], chamber=chamber,
                title=title, url=url, published_at=dt, summary="",
            ))
    log.info("%s : %d items", src["id"], len(out))
    return out
