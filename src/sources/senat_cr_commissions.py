"""R37-A (2026-04-24) — Scraper des comptes rendus hebdomadaires de
commissions Sénat.

Symétrie avec `an_cr_commissions` (R35-B) pour combler le gap de couverture
côté Sénat : les zips `senat_debats` / `senat_cri` ne couvrent QUE les
séances plénières, rien sur les commissions ni les groupes d'études. Le
Sénat publie les CR de commission sous forme de **bulletins
hebdomadaires** accessibles à l'URL :

    https://www.senat.fr/compte-rendu-commissions/<slug>.html   (listing)
    https://www.senat.fr/compte-rendu-commissions/<YYYYMMDD>/<short>.html
        (CR hebdomadaire : tous les CR de la semaine pour cette commission)

Le listing contient une suite de `<h3 id=curses><a class="link"
href="/compte-rendu-commissions/YYYYMMDD/<short>.html">Semaine du D MOIS
YYYY</a></h3>`. On parse ces liens puis on fetche chaque page hebdo pour
récupérer le texte (strip HTML) qu'on injecte en `raw.haystack_body` pour
alimenter le matcher mots-clés.

Approche prudente R35-D/R35-E : on n'active QUE la commission culture,
éducation, communication et sport (PO211490, slug Sénat `culture`).
Les autres commissions peuvent être ajoutées par entrée yaml dédiée — pas
de découverte automatique pour éviter le bruit « affaires sociales ».
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime

from ..models import Item
from ._common import fetch_text

log = logging.getLogger(__name__)


# Regex : <h3 id=curses><a class="link" href="/compte-rendu-commissions/YYYYMMDD/<short>.html">Semaine du …</a></h3>
_ENTRY_RE = re.compile(
    r'<h3[^>]*>\s*<a[^>]+href="(/compte-rendu-commissions/(\d{8})/([a-z]+)\.html)"[^>]*>\s*'
    r"([^<]{5,200}?)\s*</a>\s*</h3>",
    re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_SCRIPT_RE = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_HTML_STYLE_RE = re.compile(r"<style[\s\S]*?</style>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    if not html:
        return ""
    html = _HTML_SCRIPT_RE.sub(" ", html)
    html = _HTML_STYLE_RE.sub(" ", html)
    text = _HTML_TAG_RE.sub(" ", html)
    # Entités courantes sénat : &nbsp; &#039; &amp;
    text = (text
            .replace("&nbsp;", " ")
            .replace("&#039;", "'")
            .replace("&rsquo;", "'")
            .replace("&amp;", "&")
            .replace("&quot;", '"')
            .replace("&lt;", "<")
            .replace("&gt;", ">"))
    return _WS_RE.sub(" ", text).strip()


def _parse_week_date(yyyymmdd: str) -> datetime | None:
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d")
    except ValueError:
        return None


def _parse_listing(html: str, max_entries: int = 20) -> list[dict]:
    """Extrait jusqu'à `max_entries` liens CR hebdo du listing.

    Retourne une liste de dicts `{url, yyyymmdd, short, label, date}`.
    La première entrée de la page est la plus récente (ordre Sénat),
    on la préserve.
    """
    entries: list[dict] = []
    for m in _ENTRY_RE.finditer(html):
        rel_url = m.group(1)
        yyyymmdd = m.group(2)
        short = m.group(3)
        label_raw = _WS_RE.sub(" ", m.group(4)).strip()
        dt = _parse_week_date(yyyymmdd)
        if not dt:
            continue
        entries.append({
            "url": f"https://www.senat.fr{rel_url}",
            "yyyymmdd": yyyymmdd,
            "short": short,
            "label": label_raw,
            "date": dt,
        })
        if len(entries) >= max_entries:
            break
    return entries


def _item_uid(sid: str, yyyymmdd: str, short: str) -> str:
    return hashlib.sha1(
        f"{sid}:{yyyymmdd}:{short}".encode("utf-8")
    ).hexdigest()[:16]


def fetch_source(src: dict) -> list[Item]:
    """Scrape un listing CR commissions Sénat + fetch chaque CR hebdo.

    Paramètres src :
      - id              : identifiant source (yaml)
      - category        : 'comptes_rendus'
      - url             : URL du listing (ex. /compte-rendu-commissions/culture.html)
      - commission_label: libellé long à préfixer au titre (R35-E)
      - commission_organe: code PO pour bypass organe (R27)
      - max_new_per_run : nb max de CR hebdo fetchés par run (défaut 8)
      - body_max_chars  : taille max du haystack_body (défaut 10000)
    """
    sid = src["id"]
    cat = src.get("category") or "comptes_rendus"
    listing_url = src["url"]
    commission_label = (src.get("commission_label") or "").strip()
    commission_organe = (src.get("commission_organe") or "").strip()
    max_new = int(src.get("max_new_per_run", 8))
    body_max = int(src.get("body_max_chars", 10000))

    try:
        listing_html = fetch_text(listing_url)
    except Exception as e:
        log.warning("senat_cr_commissions %s : listing KO (%s)", sid, e)
        return []

    entries = _parse_listing(listing_html)
    if not entries:
        log.info("senat_cr_commissions %s : 0 entrée parsée (%s)",
                 sid, listing_url)
        return []

    items: list[Item] = []
    for ev in entries[:max_new]:
        try:
            body_html = fetch_text(ev["url"])
        except Exception as e:
            log.debug("senat_cr_commissions %s : CR %s KO (%s) — skip",
                      sid, ev["url"], e)
            continue
        body_text = _strip_html(body_html)[:body_max]
        if not body_text:
            continue

        # Titre : "<commission_label> — Semaine du D MOIS YYYY".
        week_label = ev["label"]
        # Normalise le "Semaine\n du 13 avril 2026" multi-ligne observé
        # sur le listing en "Semaine du 13 avril 2026".
        week_label = _WS_RE.sub(" ", week_label).strip()
        if commission_label:
            display_title = f"{commission_label} — {week_label}"
        else:
            display_title = week_label

        summary = body_text[:500]

        raw = {
            "path": "senat:cr_commissions_html",
            "commission": commission_label,
            "yyyymmdd": ev["yyyymmdd"],
            "short": ev["short"],
            # `haystack_body` : consommé par KeywordMatcher.apply via R26
            # pour matcher les mots-clés sport sur le contenu complet
            # (pas uniquement le titre).
            "haystack_body": body_text,
        }
        if commission_organe:
            # Permet au bypass organe R27 de capter ces CR côté Sénat.
            raw["organe"] = commission_organe

        items.append(Item(
            source_id=sid,
            uid=_item_uid(sid, ev["yyyymmdd"], ev["short"]),
            category=cat,
            chamber="Senat",
            title=display_title[:220],
            url=ev["url"],
            published_at=ev["date"],
            summary=summary,
            raw=raw,
        ))

    log.info(
        "senat_cr_commissions %s : %d CR hebdo ingérés (sur %d listés)",
        sid, len(items), len(entries),
    )
    return items
