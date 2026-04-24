"""R35-E (2026-04-24) — Scraper d'agenda HTML d'une commission Sénat.

Contexte : le format `senat_agenda_daily` (R15) vise
`https://www.senat.fr/agenda/Global/agl{DDMMYYYY}Print.html` mais ces
pages renvoient toutes 404 "Accès restreint" depuis la sandbox comme
depuis CI (WAF serveur spécifique au chemin `/agenda/*`). Le source
`senat_agenda` est donc `enabled: false` depuis R15.

Alternative trouvée (audit 2026-04-24) : chaque commission expose une
page HTML dédiée `agenda-de-la-commission.html` sous
`/travaux-parlementaires/commissions/{slug}/agenda-de-la-commission.html`
qui répond en HTTP 200 normal et contient :

    <h3>Prochaines réunions</h3>
    <ul class="list-group list-group-flush">
        <li class="list-group-item">
            <div class="row">
                <div class="col-2">
                    <div class="d-flex flex-column">
                        <span class="display-4 ff-alt lh-1">28</span>
                        <span class="mt-n1 fw-semibold lh-1">avril</span>
                    </div>
                </div>
                <div class="col-10 d-flex flex-column">
                    <h4 class="list-group-title ..." title="...">…</h4>
                    <p class="list-group-subtitle">Salle A131 - 1er étage Ouest</p>
                    <time datetime="9:00"><i …></i> 9h00</time>
                </div>
            </div>
        </li>
        …
    </ul>

Pour la veille sport, la commission clé est la **Commission de la
culture, de l'éducation, de la communication et du sport** (Sénat).
R35-D a acté que les autres commissions (affaires sociales, finances)
produisent trop de bruit off-topic — on n'en ajoute pas ici, mais le
handler est générique, il suffit de dupliquer une entrée YAML pour
couvrir une autre commission si besoin plus tard.

Paramètres YAML supportés (cf. `sources.yml`) :
    url:             https://www.senat.fr/travaux-parlementaires/…/agenda-de-la-commission.html
    commission_label: "Commission culture/éducation/communication/sport"
                     (affiché en préfixe de chaque item pour clarifier
                     l'origine au lecteur du digest)
    commission_organe: "PO211490"
                     (code organe pour cohérence avec assemblee_organes;
                     injecté dans item.raw pour permettre au bypass
                     organe de matcher côté Sénat — R27 symétrie)
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Iterable

from ._common import fetch_text
from ..models import Item
from .. import textclean as _textclean

log = logging.getLogger(__name__)

# Bloc "Prochaines réunions" sur la page agenda de commission. On cible
# spécifiquement la <ul class="list-group list-group-flush"> qui suit
# le <h3>Prochaines réunions</h3> pour éviter de capturer les autres
# list-group du template (footer, sidebar…).
_BLOCK_RE = re.compile(
    r'<h3[^>]*>\s*Prochaines\s+r[ée]unions\s*</h3>.*?'
    r'<ul[^>]+class="[^"]*list-group[^"]*"[^>]*>'
    r'(?P<body>.*?)'
    r'</ul>',
    re.DOTALL | re.IGNORECASE,
)

# Un item réunion. Le HTML Sénat est très verbeux (plein de <div>
# vides), donc on découpe par bornes `<li class="list-group-item">`
# plutôt que par regex récursive sur des balises équilibrées.
_LI_RE = re.compile(
    r'<li[^>]+class="[^"]*list-group-item[^"]*"[^>]*>(?P<body>.*?)</li>',
    re.DOTALL | re.IGNORECASE,
)

# Jour (2 chiffres), dans la première colonne de la card.
_DAY_RE = re.compile(
    r'<span[^>]+class="[^"]*display-4[^"]*"[^>]*>\s*(?P<d>\d{1,2})\s*</span>',
    re.IGNORECASE,
)

# Mois (texte) : "avril", "mai", etc.
_MONTH_RE = re.compile(
    r'<span[^>]+class="[^"]*fw-semibold[^"]*"[^>]*>\s*(?P<m>[A-Za-zéèêëâàçûîô]+)\s*</span>',
    re.IGNORECASE,
)

# Titre de la réunion : balise <h4 class="list-group-title ..." title="...">
# On privilégie l'attribut `title` qui contient le libellé complet,
# sinon on prend le contenu texte (qui peut être tronqué par
# line-clamp-3 côté affichage).
_TITLE_RE = re.compile(
    r'<h4[^>]+class="[^"]*list-group-title[^"]*"[^>]*?'
    r'(?:title="(?P<attr>[^"]*)")?[^>]*>'
    r'(?P<text>.*?)'
    r'</h4>',
    re.DOTALL | re.IGNORECASE,
)

# Sous-titre : salle / lieu. Facultatif.
_SUBTITLE_RE = re.compile(
    r'<p[^>]+class="[^"]*list-group-subtitle[^"]*"[^>]*>(?P<s>.*?)</p>',
    re.DOTALL | re.IGNORECASE,
)

# Heure : attribut datetime="HH:MM" ou contenu "9h00" / "9h".
_TIME_ATTR_RE = re.compile(
    r'<time[^>]+datetime="(?P<dt>\d{1,2}:\d{2})"',
    re.IGNORECASE,
)

_MOIS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3,
    "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    "decembre": 12,
}


def _parse_mois(txt: str) -> int | None:
    """Normalise `'Avril'` / `'avril'` / `'AOÛT'` → entier 1..12.

    Tolérant aux accents présents/absents pour être robuste aux pages
    où les caractères seraient encodés autrement (très rare mais vu sur
    quelques templates TYPO3 avec charset cassé).
    """
    if not txt:
        return None
    key = txt.strip().lower()
    return _MOIS_FR.get(key)


def _resolve_date(
    day: int,
    month: int,
    time_str: str | None,
    now: datetime,
) -> datetime | None:
    """Combine (day, month, time_str) + `now` pour inférer l'année.

    Règle métier : la page Sénat n'affiche JAMAIS l'année sur les
    "Prochaines réunions" (design compact). On fait donc l'inférence :
    - Si la date (D/M/now.year) est >= now.date() → année courante
    - Sinon (le mois est déjà passé) → année suivante
    Exception : si on est à 3 semaines ou moins dans l'année suivante
    après un événement daté de fin décembre, on accepte aussi l'année
    courante si la date est dans les 30 jours passés (évite qu'une
    réunion du 31 déc vue le 2 janv bascule sur l'année d'après).

    Retourne un datetime naïf (convention R11f).
    """
    try:
        h, mm = 0, 0
        if time_str:
            h, mm = (int(x) for x in time_str.split(":", 1))
        candidate = datetime(now.year, month, day, h, mm)
    except ValueError:
        return None
    # Heuristique bascule année
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if candidate.date() < today.date():
        # Date dans le passé sur l'année courante :
        # si très proche (< 30j) on accepte, sinon on bascule year+1.
        if (today.date() - candidate.date()).days <= 30:
            return candidate
        try:
            return candidate.replace(year=now.year + 1)
        except ValueError:
            return candidate
    return candidate


def _parse_event_block(
    body: str, *, now: datetime,
) -> dict | None:
    """Parse un `<li class="list-group-item">` et renvoie le dict d'event.

    Retourne None si l'un des champs clés (titre, jour, mois) manque
    — un item agenda sans date ou sans titre n'a aucune valeur pour
    le digest, on préfère le jeter que d'émettre un Item dégradé qui
    polluerait la DB.
    """
    d_m = _DAY_RE.search(body)
    if not d_m:
        return None
    try:
        day = int(d_m.group("d"))
    except ValueError:
        return None
    if not 1 <= day <= 31:
        return None

    mo_m = _MONTH_RE.search(body)
    month = _parse_mois(mo_m.group("m")) if mo_m else None
    if not month:
        return None

    ti_m = _TITLE_RE.search(body)
    title_attr = (ti_m.group("attr") if ti_m else "") or ""
    title_txt = _textclean.strip_html(ti_m.group("text")) if ti_m else ""
    # On privilégie title="..." (jamais tronqué), sinon le texte.
    title = (title_attr or title_txt).strip()
    if len(title) < 5:
        return None

    sub_m = _SUBTITLE_RE.search(body)
    salle = _textclean.strip_html(sub_m.group("s")).strip() if sub_m else ""

    time_attr_m = _TIME_ATTR_RE.search(body)
    time_str = time_attr_m.group("dt") if time_attr_m else None

    event_dt = _resolve_date(day, month, time_str, now)
    if event_dt is None:
        return None

    return {
        "title": title[:220],
        "salle": salle[:200],
        "time_hhmm": time_str or "",
        "event_dt": event_dt,
    }


def _parse_page(html: str, *, now: datetime) -> list[dict]:
    """Extrait les events d'une page agenda-de-la-commission.html.

    Retourne une liste (potentiellement vide — page "Aucun événement
    n'est actuellement inscrit à l'agenda" pendant les inter-sessions).
    """
    block_m = _BLOCK_RE.search(html)
    if not block_m:
        return []
    body = block_m.group("body")
    events: list[dict] = []
    for li in _LI_RE.finditer(body):
        parsed = _parse_event_block(li.group("body"), now=now)
        if parsed:
            events.append(parsed)
    return events


def _item_uid(sid: str, ev: dict) -> str:
    """UID stable basé sur (sid, date ISO, titre).

    Idempotent : si la page est re-fetchée quotidiennement (cron), un
    event déjà connu ne sera pas ré-inséré (contrainte hash_key DB).
    Le choix de l'ISO complet (avec heure) permet de distinguer deux
    réunions le même jour à heures différentes, cas fréquent en période
    budgétaire.
    """
    key = f"{sid}:{ev['event_dt'].isoformat()}:{ev['title']}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def fetch_source(src: dict) -> list[Item]:
    """Scrape une page agenda-de-la-commission.html et émet des Item.

    Paramètres YAML :
        url               : str, URL complète de la page HTML
        id                : str, identifiant stable (ex. `senat_agenda_culture`)
        category          : str, catégorie destination (usuellement `agenda`)
        commission_label  : str, nom humain affiché en préfixe des items
                            et dans `raw.commission`
        commission_organe : str, code PO optionnel — injecté dans
                            `raw.organe` pour permettre au bypass organe
                            côté main de matcher si la commission est
                            sport-relevant (R27 / R35-D).
    """
    sid = src["id"]
    url = src["url"]
    cat = src.get("category", "agenda")
    commission_label = (src.get("commission_label") or "").strip()
    commission_organe = (src.get("commission_organe") or "").strip()

    try:
        body = fetch_text(url)
    except Exception as e:
        log.warning("Sénat commission agenda %s : fetch KO %s : %s", sid, url, e)
        return []

    if "Aucun événement" in body or "aucun événement" in body:
        log.info("Sénat commission agenda %s : aucun événement publié", sid)
        return []

    now = datetime.now().replace(microsecond=0)
    events = _parse_page(body, now=now)
    if not events:
        log.info(
            "Sénat commission agenda %s : 0 event parsé (page sans bloc)", sid,
        )
        return []

    items: list[Item] = []
    for ev in events:
        title = ev["title"]
        # Préfixer le titre par le libellé de commission aide le lecteur
        # du digest : sans ça on ne saurait pas à quelle commission se
        # rattache la réunion (juste badge "Sénat" indistinct).
        display_title = (
            f"{commission_label} — {title}" if commission_label else title
        )
        summary_parts = []
        if ev["time_hhmm"]:
            summary_parts.append(ev["time_hhmm"].replace(":", "h"))
        if ev["salle"]:
            summary_parts.append(f"Lieu : {ev['salle']}")
        if commission_label:
            summary_parts.append(commission_label)
        summary = " — ".join(summary_parts)[:2000]

        raw = {
            "path": "senat:commission_agenda_html",
            "commission": commission_label,
            "salle": ev["salle"],
            "heure": ev["time_hhmm"],
        }
        if commission_organe:
            raw["organe"] = commission_organe

        items.append(Item(
            source_id=sid,
            uid=_item_uid(sid, ev),
            category=cat,
            chamber="Senat",
            title=display_title[:220],
            url=url,
            published_at=ev["event_dt"],
            summary=summary,
            raw=raw,
        ))

    log.info(
        "Sénat commission agenda %s : %d event(s) depuis %s",
        sid, len(items), url,
    )
    return items
