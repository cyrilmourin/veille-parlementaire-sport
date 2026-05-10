"""R41-AY (2026-05-10) — Scraper vidéothèque commission Sénat.

Source : https://videos.senat.fr/commission.{CODE}.p1
(p1 = 10 dernières publications ; pagination disponible jusqu'à p57+
mais on ne fetch que p1 pour rester ciblé sur le récent — coût réseau
constant quel que soit l'historique).

Contexte (Cyril 2026-05-10) : R41-AX a couvert les blocs « Réunions
passées » du template `agenda-de-la-commission.html`. Si ce bloc
n'existe pas (ou n'est pas alimenté en temps réel), R41-AY prend le
relais : la vidéothèque liste systématiquement chaque audition une
fois publiée, avec la **date complète (année incluse)** et l'URL du
player vidéo Sénat — meilleur UX dans le digest qu'un simple lien
agenda.

Format de carte (extrait observé sur AFCL.p1, audit 2026-05-10) :

    <div class="swiper-slide">
        <div class="card card-default card-reduced">
            <figure class="card-figure"><img …/></figure>
            <div class="card-header">
                <div class="card-duration">1 h 06</div>
                <div class="card-icon ms-auto">…</div>
            </div>
            <div class="card-body">
                <h3 class="card-title">
                    <a href="https://videos.senat.fr/video.5814747_69f98208892ca.crise-…"
                       class="stretched-link"
                       title="Crise des droits TV du football : Nicolas de Tavernost">
                        Crise des droits TV du football : Nicolas de Tavernost
                    </a>
                </h3>
                <p class="card-subtitle"></p>
                <time class="card-time">Mercredi 6 mai 2026</time>
            </div>
        </div>
    </div>

Distinction critique : le footer expose aussi des `swiper-slide` mais
avec la classe `card-slim` (pas `card-reduced`). On filtre sur
`card-reduced` pour ne capter que les vidéos de la commission.

Paramètres YAML supportés (cf. `sources.yml`) :
    url               : str, URL de la page p1 (ex. .../commission.AFCL.p1)
    id                : str, identifiant stable (ex. `senat_videos_culture`)
    category          : str, catégorie destination (usuellement `agenda`)
    commission_label  : str, nom humain affiché en préfixe des items
    commission_organe : str, code PO optionnel — injecté dans `raw.organe`
                        pour permettre au bypass organe de matcher (R27)
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Iterable

from bs4 import BeautifulSoup

from ._common import fetch_text
from ..models import Item
from .. import textclean as _textclean

log = logging.getLogger(__name__)

# Date FR : « Mercredi 6 mai 2026 ». L'attribut `<time class="card-time">`
# ne porte pas de `datetime` ISO sur cette page — il faut parser le texte.
_DATE_RE = re.compile(
    r'(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)?\s*'
    r'(?P<d>\d{1,2})\s+'
    r'(?P<m>janvier|f[ée]vrier|mars|avril|mai|juin|juillet|'
    r'ao[ûu]t|septembre|octobre|novembre|d[ée]cembre)\s+'
    r'(?P<y>\d{4})',
    re.IGNORECASE,
)

# Extrait l'ID stable de la vidéo depuis l'URL (ex.
# `video.5814747_69f98208892ca.crise-des-droits-tv...`). On retient
# la partie `ID_HASH` (avant le slug littéral) pour bâtir un UID
# robuste aux changements de slug côté Sénat.
_VIDEO_ID_RE = re.compile(r'/video\.(?P<vid>\d+_[a-f0-9]+)\.', re.IGNORECASE)

_MOIS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3,
    "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    "decembre": 12,
}


def _parse_french_date(txt: str) -> datetime | None:
    """« Mercredi 6 mai 2026 » → datetime(2026, 5, 6, 0, 0).

    Tolérant aux accents manquants (« fevrier », « decembre ») et au
    jour-de-semaine optionnel/absent. L'heure n'est jamais portée par
    cette source — on défaut à minuit, conforme à `Item.published_at`
    naïf (convention R11f).
    """
    if not txt:
        return None
    m = _DATE_RE.search(txt.strip())
    if not m:
        return None
    try:
        day = int(m.group("d"))
        year = int(m.group("y"))
    except ValueError:
        return None
    month = _MOIS_FR.get(m.group("m").strip().lower())
    if not month or not (1 <= day <= 31) or not (1900 <= year <= 2100):
        return None
    try:
        return datetime(year, month, day, 0, 0)
    except ValueError:
        return None


def _video_uid(sid: str, video_url: str, fallback_key: str) -> str:
    """UID stable basé sur l'ID vidéo Sénat si extractible, sinon hash url+title.

    L'ID `5814747_69f98208892ca` est stable côté Sénat (vu sur AFCL :
    composé d'un identifiant numérique + un suffixe hex 13 chars). Si
    l'URL change de format un jour, on retombe sur un hash de la clé
    fallback (typiquement `url|title|date_iso`) pour rester idempotent.
    """
    m = _VIDEO_ID_RE.search(video_url)
    if m:
        key = f"{sid}:{m.group('vid')}"
    else:
        key = f"{sid}:{fallback_key}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _parse_page(html: str) -> list[dict]:
    """Extrait la liste des vidéos de p1 (10 cartes attendues).

    Sélecteur : `div.swiper-slide div.card.card-reduced` — exclut les
    `card-slim` du footer "Le Sénat, c'est aussi".
    """
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("div.swiper-slide div.card.card-reduced")
    events: list[dict] = []
    for card in cards:
        a = card.select_one("h3.card-title a.stretched-link")
        if not a:
            continue
        url = (a.get("href") or "").strip()
        if not url or "/video." not in url:
            continue
        title_attr = (a.get("title") or "").strip()
        title_txt = a.get_text(strip=True)
        title = (title_attr or title_txt).strip()
        if len(title) < 5:
            continue

        time_el = card.select_one("time.card-time")
        date_text = time_el.get_text(" ", strip=True) if time_el else ""
        event_dt = _parse_french_date(date_text)
        if event_dt is None:
            continue

        dur_el = card.select_one("div.card-duration")
        duration = dur_el.get_text(strip=True) if dur_el else ""

        events.append({
            "title": title[:220],
            "url": url,
            "event_dt": event_dt,
            "duration": duration[:40],
        })
    return events


def fetch_source(src: dict) -> list[Item]:
    """Scrape la page videos.senat.fr/commission.{CODE}.p1.

    Paramètres YAML — cf. docstring du module.
    """
    sid = src["id"]
    url = src["url"]
    cat = src.get("category", "agenda")
    commission_label = (src.get("commission_label") or "").strip()
    commission_organe = (src.get("commission_organe") or "").strip()

    try:
        body = fetch_text(url)
    except Exception as e:
        log.warning("Sénat vidéos commission %s : fetch KO %s : %s", sid, url, e)
        return []

    events = _parse_page(body)
    if not events:
        log.info(
            "Sénat vidéos commission %s : 0 vidéo parsée (page vide ou format changé)",
            sid,
        )
        return []

    items: list[Item] = []
    for ev in events:
        title = ev["title"]
        # Préfixer par le nom de la commission, comme R35-E / R41-AX.
        display_title = (
            f"{commission_label} — {title}" if commission_label else title
        )
        summary_parts = ["Vidéo Sénat"]
        if ev["duration"]:
            summary_parts.append(f"durée {ev['duration']}")
        if commission_label:
            summary_parts.append(commission_label)
        summary = " — ".join(summary_parts)[:2000]

        raw = {
            "path": "senat:videos_commission_html",
            "commission": commission_label,
            "duration": ev["duration"],
            "video_url": ev["url"],
        }
        if commission_organe:
            raw["organe"] = commission_organe

        fallback_key = f"{ev['url']}|{ev['event_dt'].isoformat()}|{title}"
        items.append(Item(
            source_id=sid,
            uid=_video_uid(sid, ev["url"], fallback_key),
            category=cat,
            chamber="Senat",
            title=display_title[:220],
            url=ev["url"],
            published_at=ev["event_dt"],
            summary=summary,
            raw=raw,
        ))

    log.info(
        "Sénat vidéos commission %s : %d vidéo(s) depuis %s",
        sid, len(items), url,
    )
    return items
