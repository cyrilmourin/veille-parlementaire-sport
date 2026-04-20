"""Connecteur JORF via le dump XML DILA OPENDATA — pas de credentials.

Source : https://echanges.dila.gouv.fr/OPENDATA/JORF/
Format : fichiers `JORF_YYYYMMDD-HHMMSS.tar.gz` (1 à 2 éditions par jour,
matin et soir) contenant les XML LEGIPUBLI.

Avantage sur l'API PISTE : aucune authentification, flux stable, données identiques.

On télécharge les N dernières éditions (param `days_back` dans sources.yml,
sémantiquement = nombre d'éditions car le flux peut publier 2 fois/jour),
on extrait les XML à la volée, on ne retient que les natures pertinentes :
ARRETE, DECRET, DECISION, LOI, ORDONNANCE. Les arrêtés de nomination sont
reclassés dans la catégorie "nominations".
"""
from __future__ import annotations

import io
import logging
import re
import tarfile
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from lxml import etree

from ..models import Item
from ._common import fetch_bytes, fetch_text, parse_iso

log = logging.getLogger(__name__)

BASE_INDEX = "https://echanges.dila.gouv.fr/OPENDATA/JORF/"
# Éditions quotidiennes : JORF_YYYYMMDD-HHMMSS.tar.gz (parfois 2/jour)
# On capture séparément date + heure pour pouvoir trier précisément — sinon
# deux éditions du même jour apparaissent indistinctement.
_FILE_PAT = re.compile(
    r"^JORF_(?P<date>\d{8})-(?P<time>\d{6})\.tar\.gz$", re.IGNORECASE
)

# Natures que l'on garde (les plus fréquentes dans la veille sport)
KEEP_NATURES = {"ARRETE", "DECRET", "DECISION", "LOI", "ORDONNANCE"}


def _list_recent_dumps(n: int = 8) -> list[tuple[str, datetime]]:
    """Parse l'index Apache et renvoie [(url, datetime)…] triés du plus
    récent au plus ancien.

    On intègre l'heure pour distinguer les deux éditions quotidiennes
    éventuelles (matin ~00:30 UTC, soir ~20:00 UTC).
    """
    try:
        html = fetch_text(BASE_INDEX)
    except Exception as e:
        log.error("DILA index KO: %s", e)
        return []
    soup = BeautifulSoup(html, "html.parser")
    entries: list[tuple[str, datetime]] = []
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        m = _FILE_PAT.match(href)
        if not m:
            continue
        try:
            dt = datetime.strptime(
                m.group("date") + m.group("time"), "%Y%m%d%H%M%S"
            )
        except ValueError:
            continue
        entries.append((urljoin(BASE_INDEX, href), dt))
    entries.sort(key=lambda x: x[1], reverse=True)
    if not entries:
        log.warning(
            "DILA JORF : aucune entrée ne matche _FILE_PAT dans l'index "
            "(index HTML de %d chars). Vérifie le format des noms de fichier.",
            len(html or ""),
        )
    return entries[:n]


def _ns_strip(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text(el) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _find(root, *paths):
    """Cherche le premier élément matchant l'un des chemins XPath locaux."""
    for p in paths:
        res = root.find(p)
        if res is not None and (res.text or len(res) > 0):
            return res
    return None


def _parse_texte_version(xml_bytes: bytes) -> dict | None:
    """Extrait les champs utiles d'un fichier TEXTE_VERSION_xxx.xml."""
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None

    # Commun
    id_text = _text(_find(root, ".//ID", ".//META_COMMUN/ID"))
    nature = _text(_find(root, ".//NATURE", ".//META_COMMUN/NATURE")).upper()
    if nature and nature not in KEEP_NATURES:
        return None

    titre = _text(_find(root, ".//TITREFULL", ".//TITRE"))
    date_publi = _text(_find(root, ".//DATE_PUBLI"))
    date_sign = _text(_find(root, ".//DATE_SIGNATURE"))
    if not id_text or not titre:
        return None

    # URL Legifrance publique
    url = f"https://www.legifrance.gouv.fr/jorf/id/{id_text}"
    return {
        "id": id_text,
        "nature": nature or "ARRETE",
        "title": titre,
        "url": url,
        "date": parse_iso(date_publi) or parse_iso(date_sign),
    }


def _iter_texte_versions(tarball_bytes: bytes):
    """Itère sur les octets des fichiers TEXTE_VERSION_*.xml dans un .taz."""
    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tf:
        for member in tf:
            if not member.isfile():
                continue
            name = member.name.lower()
            if "/texte/version/" not in name and "texte_version" not in name:
                # Tolère différentes arborescences selon les années
                if not name.endswith(".xml"):
                    continue
            f = tf.extractfile(member)
            if f is None:
                continue
            data = f.read()
            if b"<TEXTE_VERSION" in data:
                yield data


def fetch_source(src: dict) -> list[Item]:
    days_back = int(src.get("days_back", 8))
    dumps = _list_recent_dumps(n=days_back)
    if not dumps:
        log.warning("DILA JORF : aucun dump récent trouvé")
        return []

    out: list[Item] = []
    seen: set[str] = set()
    for url, dt in dumps:
        try:
            raw = fetch_bytes(url)
        except Exception as e:
            log.warning("DILA %s KO: %s", url, e)
            continue

        for xml_bytes in _iter_texte_versions(raw):
            info = _parse_texte_version(xml_bytes)
            if not info:
                continue
            if info["id"] in seen:
                continue
            seen.add(info["id"])

            # Catégorisation : nomination si le titre le suggère.
            # On élargit le pattern pour capter aussi : "portant nomination",
            # "fin de fonctions", "renouvellement du mandat", "désignation",
            # formulations courantes dans les décrets JORF sport.
            title_low = info["title"].lower()
            cat = src["category"]
            _NOM_HINTS = (
                "nomination", "nommé", "nommée",
                "désigné", "désignée", "désignation",
                "cessation de fonctions", "fin de fonctions",
                "renouvellement du mandat", "renouvellement de mandat",
            )
            if any(h in title_low for h in _NOM_HINTS):
                cat = "nominations"

            out.append(Item(
                source_id=src["id"],
                uid=info["id"],
                category=cat,
                chamber="JORF",
                title=info["title"][:220],
                url=info["url"],
                published_at=info["date"],
                summary=f"{info['nature'].capitalize()} publié au JORF.",
                raw={"nature": info["nature"], "dump": url},
            ))
    log.info("DILA JORF : %d items uniques sur %d dumps", len(out), len(dumps))
    return out
