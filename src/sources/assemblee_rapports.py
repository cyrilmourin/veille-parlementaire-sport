"""Scraper HTML des rapports parlementaires AN.

R28 (2026-04-23) — L'AN n'expose pas (encore) les rapports comme un
dataset unitaire sur `data.assemblee-nationale.fr` : les rapports sont
inclus dans le dump `Dossiers_Legislatifs.json.zip` mais noyés parmi
toutes les étapes de la procédure (projets/propositions de loi, textes
adoptés, avis, comptes rendus de commissions). Pour alimenter la
veille d'un flux "rapports AN" dédié, on scrape la page publique de
listing des rapports législatifs :

    https://www2.assemblee-nationale.fr/documents/liste?type=rapports&legis=17

Cette page est server-rendered (pas de SPA) et retourne les ~100 rapports
les plus récents, avec pour chaque entrée :
- `<li data-id="OMC_RAPPANR5L17BXXXX">` où XXXX = numéro du rapport
- `<h3>` : titre du projet/proposition de loi examiné(e)
- `<span class="heure">Mis en ligne {jour} {date} à {heure}</span>`
- `<a href=".../dossiers/...">Dossier législatif</a>`
- `<a href=".../pdf/rapports/rXXXX-a…COMPA.pdf">Document</a>`
- `<p>` : résumé (rapport de la commission X sur la PPL/PJL de M. Y…)

On filtre les entrées RAPP (rapports) en ignorant les `OMC_PRJL`/`PION`
(textes comparatifs / textes de loi) qui cohabitent dans la liste.

Choix d'URL de l'item : lien du **dossier législatif** quand présent (page
HTML riche, dynamique), sinon fallback sur le PDF.

Parse ultra-tolérant : bs4 + regex, zéro dépendance réseau secondaire.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Iterable

from bs4 import BeautifulSoup

from ..models import Item
from ._common import fetch_bytes

log = logging.getLogger(__name__)

# Mois FR → numéro pour parser "Mis en ligne mercredi 28 janvier 2026 à 15h20"
_MONTHS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2,
    "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    "decembre": 12,
}

# Regex date dans "Mis en ligne mercredi 28 janvier 2026 à 15h20"
_DATE_RE = re.compile(
    r"Mis\s+en\s+ligne\s+\S+\s+(\d{1,2})\s+([A-Za-zéû]+)\s+(\d{4})"
    r"(?:\s+à\s+(\d{1,2})h(\d{1,2}))?",
    re.IGNORECASE,
)

# `data-id="OMC_RAPPANR5L17B2396"` — on garde RAPP, ignore PRJL/PION/AVIS…
_DATA_ID_RE = re.compile(r"^OMC_RAPP")


def _parse_date_fr(text: str) -> datetime | None:
    """Parse 'Mis en ligne mercredi 28 janvier 2026 à 15h20' → datetime naïf UTC-ish."""
    if not text:
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    try:
        day = int(m.group(1))
        month_name = m.group(2).lower().strip()
        year = int(m.group(3))
        month = _MONTHS_FR.get(month_name)
        if not month:
            return None
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        return datetime(year, month, day, hh, mm)
    except (ValueError, TypeError):
        return None


def _parse_report_li(li) -> dict | None:
    """Extrait {uid, title, date, summary, url_dossier, url_pdf, num} d'un `<li>`.

    Retourne None si l'entrée n'est pas un vrai rapport RAPP ou si les
    champs obligatoires manquent (titre + url).
    """
    data_id = (li.get("data-id") or "").strip()
    if not data_id or not _DATA_ID_RE.match(data_id):
        return None

    # Numéro du rapport depuis data-id : OMC_RAPPANR5L17B2396 → 2396
    # (suffixe -COMPA possible pour les "textes comparatifs", on strippe
    #  pour dédup avec la version principale).
    num_m = re.search(r"B(\d+)", data_id)
    num = num_m.group(1) if num_m else ""

    h3 = li.find("h3")
    title = h3.get_text(" ", strip=True) if h3 else ""
    # Certaines entrées RAPP sont dupliquées en version "Texte comparatif" :
    # on les garde (date/contenu distincts) — l'uid data-id diffère déjà.

    # Résumé = premier <p> proche du <h3>
    p = li.find("p")
    summary = p.get_text(" ", strip=True) if p else ""

    # Date
    span_heure = li.find("span", class_="heure")
    heure_text = span_heure.get_text(" ", strip=True) if span_heure else ""
    published_at = _parse_date_fr(heure_text)

    # URLs : Dossier législatif (préféré) + PDF
    url_dossier = ""
    url_pdf = ""
    for a in li.find_all("a", href=True):
        href = a["href"].strip()
        label = a.get_text(" ", strip=True).lower()
        if not href:
            continue
        if "/dossiers/" in href and not url_dossier:
            url_dossier = href
        elif href.lower().endswith(".pdf") and not url_pdf:
            url_pdf = href
        elif "dossier" in label and not url_dossier:
            url_dossier = href

    url = url_dossier or url_pdf
    if not url or not title:
        return None

    # UID stable : hash de (source_id, data_id). Deux versions (classique +
    # COMPA) auront des UIDs différents — voulu : elles sont publiées
    # séparément.
    uid = hashlib.sha1(f"an_rapports:{data_id}".encode()).hexdigest()[:16]

    return {
        "uid": uid,
        "data_id": data_id,
        "num": num,
        "title": title,
        "summary": summary,
        "published_at": published_at,
        "url": url,
        "url_dossier": url_dossier,
        "url_pdf": url_pdf,
    }


def _extract_reports(html_body: str) -> list[dict]:
    """Parse le HTML complet → liste de dicts rapport."""
    if not html_body:
        return []
    soup = BeautifulSoup(html_body, "html.parser")
    out: list[dict] = []
    for li in soup.find_all("li", attrs={"data-id": True}):
        entry = _parse_report_li(li)
        if entry:
            out.append(entry)
    return out


def fetch_source(src: dict) -> list[Item]:
    """Scrape la page AN listing rapports et construit les Item.

    Config YAML attendue :
        - id: an_rapports
          category: communiques
          url: https://www2.assemblee-nationale.fr/documents/liste?type=rapports&legis=17
          format: an_rapports_html
    """
    sid = src["id"]
    url = src["url"]
    cat = src.get("category", "communiques")

    try:
        payload = fetch_bytes(url)
    except Exception as e:
        log.warning("%s : fetch KO (%s)", sid, e)
        return []

    html_body = payload.decode("utf-8", errors="replace")
    reports = _extract_reports(html_body)
    if not reports:
        log.warning("%s : aucun rapport extrait (layout AN changé ?)", sid)
        return []

    items: list[Item] = []
    for r in reports:
        items.append(Item(
            source_id=sid,
            uid=r["uid"],
            category=cat,
            chamber="AN",
            title=r["title"][:220],
            url=r["url"],
            published_at=r["published_at"],
            summary=r["summary"][:2000],
            raw={
                "path": "assemblee:rapport",
                "data_id": r["data_id"],
                "num": r["num"],
                "url_dossier": r["url_dossier"],
                "url_pdf": r["url_pdf"],
            },
        ))
    log.info("%s : %d rapports extraits", sid, len(items))
    return items
