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

R38-A (2026-04-24) : le strip HTML cible désormais le bloc `<main>` (ou
`<article>` en fallback) pour exclure le header de navigation Sénat
(galaxie Sénat, réseaux sociaux, menus langue…) qui polluait le
haystack + le snippet. On retire aussi le breadcrumb initial « Voir le
fil d'Ariane … Comptes rendus » et on décode TOUTES les entités HTML
via `html.unescape` (les &eacute; / &agrave; / … résiduels).
"""
from __future__ import annotations

import hashlib
import html as html_lib
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
_MAIN_BLOCK_RE = re.compile(r"<main[^>]*>([\s\S]*?)</main>", re.IGNORECASE)
_ARTICLE_BLOCK_RE = re.compile(
    r"<article[^>]*>([\s\S]*?)</article>", re.IGNORECASE
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_SCRIPT_RE = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_HTML_STYLE_RE = re.compile(r"<style[\s\S]*?</style>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

# Préambule breadcrumb du Sénat : « Voir le fil d'Ariane Accueil
# Commissions <slug> Comptes rendus ». Retiré avant le texte réel.
# Le motif « COMPTES RENDUS DE LA COMMISSION … » en majuscules marque
# la frontière entre breadcrumb et contenu. Si le motif est absent
# (template différent), on retombe sur le texte stripé complet.
_BREADCRUMB_END_RE = re.compile(
    r"COMPTES\s+RENDUS\s+DE\s+LA\s+COMMISSION\b",
    re.IGNORECASE,
)

# R39-E (2026-04-25) / R39-I (2026-04-25 fix) : retire la ligne d'entête
# « COMPTES RENDUS DE LA COMMISSION DE LA CULTURE, DE L'EDUCATION, DE LA
# COMMUNICATION ET DU SPORT » (et le numéro de législature côté AN), pour
# aller direct au contenu (« Mardi 14 avril 2026 Mission d'information…»,
# « Audition de M. … »).
#
# R39-I : précédente regex utilisait `[A-ZÀÉÈÊÎÔÛÇ\s,'’\-]` qui n'incluait
# PAS l'apostrophe ASCII `'` (présente après `html.unescape("&#039;")`),
# ce qui faisait échouer le match au niveau de « DE L'EDUCATION ». Et la
# classe ne couvrait pas les minuscules (le bloc d'entête contient parfois
# des minuscules dans les sous-titres). Réécriture en `.{1,400}?` non-greedy
# + DOTALL pour gérer tous les cas y compris le retour ligne et apostrophes.
_SENAT_HEADER_RE = re.compile(
    r"COMPTES\s+RENDUS\s+DE\s+LA\s+COMMISSION\b"
    r".{1,400}?"
    # R39-I (2026-04-25 fix v2) : `Communication` retiré du lookahead
    # parce qu'il apparaît systématiquement dans le libellé Sénat
    # « DE LA COMMUNICATION ET DU SPORT » et coupait au mauvais endroit.
    # Si un CR commence par « Communication de M. … » (rare), il sera
    # capturé via la suite naturelle de l'extrait — pas un cas typique.
    r"(?=\s+(?:Mardi|Mercredi|Jeudi|Vendredi|Lundi|Samedi|Dimanche|"
    r"Présidence|Mission|Audition|Examen|Table|Réunion|"
    r"Constitution|Désignation|Discussion|Suite|Nomination|"
    r"Approbation|Adoption)\b)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(html: str) -> str:
    """Extrait le texte utile d'une page CR Sénat.

    R38-A (2026-04-24) : on cible `<main>` (ou `<article>` en fallback)
    avant de stripper les tags, pour laisser de côté le header de
    navigation + footer Sénat qui polluaient autrefois le snippet.
    On décode toutes les entités HTML via `html.unescape` (gère tous
    les &xxx; / &#xxx;). Enfin on retire le breadcrumb initial pour
    ne garder que le corps du CR lui-même.
    """
    if not html:
        return ""
    # Cible le bloc principal. Si <main> absent, fallback <article>,
    # sinon garde le full HTML (comportement legacy).
    m = _MAIN_BLOCK_RE.search(html)
    if m is None:
        m = _ARTICLE_BLOCK_RE.search(html)
    block = m.group(1) if m is not None else html
    block = _HTML_SCRIPT_RE.sub(" ", block)
    block = _HTML_STYLE_RE.sub(" ", block)
    text = _HTML_TAG_RE.sub(" ", block)
    # Décode TOUTES les entités (&eacute;, &agrave;, &#039;, &laquo;,
    # &#x2019;, etc.). Plus fiable qu'une liste de remplacements ad-hoc.
    text = html_lib.unescape(text)
    # Normalise whitespace.
    text = _WS_RE.sub(" ", text).strip()
    # Retire le préambule breadcrumb si le motif est détecté — on
    # coupe AVANT la tête « COMPTES RENDUS DE LA COMMISSION ».
    br = _BREADCRUMB_END_RE.search(text)
    if br is not None:
        text = text[br.start():].strip()
    # R39-E (2026-04-25) : retire aussi la ligne d'entête majuscule
    # « COMPTES RENDUS DE LA COMMISSION DE LA CULTURE… » pour aller
    # direct au contenu réel. Coupe avant le premier verbe d'auditon
    # / mission / examen identifié.
    hdr = _SENAT_HEADER_RE.match(text)
    if hdr is not None:
        text = text[hdr.end():].strip()
    return text


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
      - body_max_chars  : taille max du haystack_body (défaut 200000 —
                          R40-G, voir doc dans an_cr_commissions.py
                          `_extract_pdf_text`)
    """
    sid = src["id"]
    cat = src.get("category") or "comptes_rendus"
    listing_url = src["url"]
    commission_label = (src.get("commission_label") or "").strip()
    commission_organe = (src.get("commission_organe") or "").strip()
    max_new = int(src.get("max_new_per_run", 8))
    body_max = int(src.get("body_max_chars", 200000))

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

        # Titre : juste "Semaine du D MOIS YYYY" — le libellé de commission
        # n'est PAS injecté ici (R38-E, 2026-04-24). Raison : le label
        # officiel « Commission culture, éducation, communication et sport »
        # fait matcher automatiquement chaque CR hebdo via le keyword
        # « sport » même quand le contenu ne traite pas de sport cette
        # semaine-là → bruit. On expose le libellé en `raw.commission`
        # (exploité par le template pour un sous-titre/tag d'affichage)
        # mais il ne participe pas au matching lexical qui se fait
        # désormais UNIQUEMENT sur le body réel du CR (haystack_body).
        week_label = ev["label"]
        # Normalise le "Semaine\n du 13 avril 2026" multi-ligne observé
        # sur le listing en "Semaine du 13 avril 2026".
        week_label = _WS_RE.sub(" ", week_label).strip()
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
