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

R42-B (2026-05-10) : extension de la profondeur de matching keyword au
corps des PDF. Avant : matching sur `title + summary[:2000]` uniquement
→ ratait les rapports dont le titre est générique (« Rapport sur le PJL
n°… ») mais dont le corps mentionnait des keywords sport spécifiques
(Pass'Sport, dopage, ANS…). Après : pour chaque rapport ayant un
`url_pdf`, on fetch le PDF, on extrait le texte avec `pypdf` (helper
réutilisé de `an_cr_commissions._extract_pdf_text`), tronqué à 50 000
chars (vs 200k pour les CR — argument Cyril : le sommaire d'un rapport
est en début de PDF, 50k = ~50 pages, suffit pour la majorité des
rapports). Le texte est posé dans `raw.haystack_body[:50000]` et
consommé par `KeywordMatcher.apply()`. Compromis : la fenêtre temps
des rapports est réduite de 1095j → 730j en parallèle (cf.
WINDOW_DAYS_BY_SOURCE_ID dans site_export).

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
# R42-B (2026-05-10) : réutilisation du helper PDF de an_cr_commissions
# pour extraire le corps des rapports. Le helper gère pypdf optionnel
# (no-op si absent) et nettoie l'entête institutionnel.
from .an_cr_commissions import _extract_pdf_text

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

# `data-id="OMC_RAPPANR5L17B2396"` — préfixe selon le type de document AN :
# - RAPP : rapports de commission (sur PPL/PJL, ou OPECST « au nom de
#          l'office »). Listing : `?type=rapports&legis=17`.
# - RINF : rapports d'information (missions d'info, évaluations, délégation
#          aux droits des femmes, etc.). Listing : `?type=rapports-information&legis=17`.
# - AVIS : avis budgétaires (PLF) ou avis sur projet/proposition de loi.
#          Listing : `?type=avis&legis=17`.
# R42-AJ (2026-05-11) : élargissement RAPP → (RAPP|RINF|AVIS). Avant, le
# scraper ne gardait que RAPP — donc 147 RINF + 18 AVIS récents n'étaient
# JAMAIS ingérés, dont notamment le rapport d'évaluation de la loi
# du 2 mars 2022 « démocratiser le sport » (RINF B2465), l'OPECST « science
# dans la mêlée pour une nation sportive » (RAPP B2074), les avis PLF
# sport-J&VA (n°1906 pour 2026, n°324 pour 2025), etc. Les textes
# (PRJL/PION/PNRE) restent volontairement exclus — ils sont ingérés par
# `an_dossiers_legislatifs` (catégorie `dossiers_legislatifs`), pas
# `communiques` (Publications).
_DATA_ID_RE = re.compile(r"^OMC_(RAPP|RINF|AVIS)")


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

    R42-AV (2026-05-11) — Exclusion des « Texte comparatif » : ce sont
    des versions cosmétiques jumelles du rapport principal (data-id avec
    suffixe `-COMPA`, ou titre se terminant par « Texte comparatif »).
    Cyril : « les "texte comparatif" m'intéressent pas, c'est du
    doublon ». On filtre ici au plus tôt pour éviter de polluer DB +
    matcher + export Hugo. Le rapport principal (sans -COMPA) reste
    ingéré normalement.
    """
    data_id = (li.get("data-id") or "").strip()
    if not data_id or not _DATA_ID_RE.match(data_id):
        return None
    # R42-AV : exclusion des « Texte comparatif » (data-id `-COMPA*`).
    if "-COMPA" in data_id:
        return None

    # Numéro du rapport depuis data-id : OMC_RAPPANR5L17B2396 → 2396
    # (suffixe -COMPA possible pour les "textes comparatifs", on strippe
    #  pour dédup avec la version principale).
    num_m = re.search(r"B(\d+)", data_id)
    num = num_m.group(1) if num_m else ""

    # R42-AJ (2026-05-11) — type extrait depuis le préfixe data-id.
    # Permet au caller d'enrichir raw + de distinguer RAPP/RINF/AVIS dans
    # le frontmatter Hugo si besoin (étiquette différenciée sur les cards).
    type_m = re.match(r"^OMC_(RAPP|RINF|AVIS)", data_id)
    doc_type = type_m.group(1) if type_m else ""

    h3 = li.find("h3")
    title = h3.get_text(" ", strip=True) if h3 else ""
    # R42-AV (2026-05-11) — exclusion défense en profondeur : si pour une
    # raison quelconque le data-id `-COMPA` n'a pas matché (variante de
    # nommage AN, futur changement), on rejette aussi par marqueur titre.
    # Normalisation case-insensitive + ignore l'espace insécable U+00A0
    # parfois utilisé par l'AN entre « Texte » et « comparatif ».
    if title:
        title_norm = re.sub(r"\s+", " ", title).lower()
        if "texte comparatif" in title_norm:
            return None

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
        "doc_type": doc_type,  # R42-AJ : "RAPP" | "RINF" | "AVIS"
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


def _fetch_pdf_haystack(url_pdf: str, max_chars: int = 50000) -> str:
    """Fetch le PDF d'un rapport et extrait le texte tronqué à `max_chars`.

    R42-B (2026-05-10). Soft-fail systématique : tout échec réseau, parse
    pypdf KO, ou pypdf indisponible retourne `""` sans planter. Le matcher
    retombe alors sur `title + summary` seul — comportement R28 historique.
    """
    if not url_pdf:
        return ""
    try:
        pdf_bytes = fetch_bytes(url_pdf)
    except Exception as e:
        log.debug("an_rapports : fetch PDF KO %s (%s)", url_pdf, e)
        return ""
    if not pdf_bytes:
        return ""
    try:
        text = _extract_pdf_text(pdf_bytes, max_chars=max_chars)
    except Exception as e:
        log.debug("an_rapports : extract PDF KO %s (%s)", url_pdf, e)
        return ""
    return text or ""


def _paginate_url(base_url: str, offset: int, limit: int) -> str:
    """R42-AK (2026-05-11) — Ajoute (ou remplace) les params `offset`/`limit`
    sur l'URL de listing AN. La pagination AN se fait via `?offset=N&limit=N`
    (param `&page=` est ignoré). Préserve les params existants (type, legis,
    type_tri…).
    """
    if offset == 0 and "offset=" not in base_url:
        return base_url
    from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
    parts = urlparse(base_url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["offset"] = str(offset)
    q["limit"] = str(limit)
    return urlunparse(parts._replace(query=urlencode(q)))


def fetch_source(src: dict) -> list[Item]:
    """Scrape la page AN listing rapports et construit les Item.

    Config YAML attendue :
        - id: an_rapports
          category: communiques
          url: https://www2.assemblee-nationale.fr/documents/liste?type=rapports&legis=17
          format: an_rapports_html
          # R42-Q (2026-05-11) : 50k → 200k pour rattraper les rapports
          # AN volumineux dont le keyword sport tombe au-delà du seuil
          # initial. Symétrie avec senat_rapports + senat_cr_commissions.
          body_max_chars: 200000
          # R42-AK (2026-05-11) — pagination par offset (param AN officiel,
          # ?offset=N&limit=N). Défaut max_pages=8 = jusqu'à 1200 items, ce
          # qui couvre largement les 2 ans de fenêtre `WINDOW_DAYS_BY_SOURCE_ID`
          # (rythme typique ~50-100 rapports/mois pour les rapports
          # législatifs, moins pour RINF/AVIS). Backward-compat : max_pages=1
          # = comportement R42-AJ (1 fetch).
          max_pages: 8
          page_size: 150

    R42-B (2026-05-10) + R42-Q (2026-05-11) : pour chaque rapport ayant
    `url_pdf`, fetch + extract PDF avec pypdf, tronque à `body_max_chars`
    (défaut 200000), pose dans `raw.haystack_body`. Le matcher
    (`KeywordMatcher.apply`) consomme automatiquement cette clé (cf. R26
    et R40-G/H pour les CR).
    """
    sid = src["id"]
    url = src["url"]
    cat = src.get("category", "communiques")
    body_max = int(src.get("body_max_chars", 200000))  # R42-B + R42-Q
    # R42-AK → R42-BT (2026-05-13) — Pagination dynamique.
    # Nominal (cron quotidien) : 1 page (150 items récents) — les
    # rapports plus anciens sont déjà en DB et restent exposés via la
    # fenêtre statique d'affichage 730j (WINDOW_DAYS_BY_SOURCE_ID).
    # Full (RUN_MODE=full / reset) : prend le `max_pages` YAML (typique 8).
    from ..run_mode import is_full_mode
    yaml_max_pages = int(src.get("max_pages", 1))
    max_pages = yaml_max_pages if is_full_mode() else 1
    page_size = int(src.get("page_size", 150))

    # R42-AK : boucle de pagination. Stop sur :
    # - HTTP KO (réseau, 4xx, 5xx)
    # - page vide (parseur retourne [])
    # - page sans NOUVEAU data-id (déjà tous vus → AN renvoie même top en
    #   boucle, défense contre param non honoré)
    seen_data_ids: set[str] = set()
    reports: list[dict] = []
    for page_idx in range(max_pages):
        page_url = _paginate_url(url, offset=page_idx * page_size,
                                 limit=page_size)
        try:
            payload = fetch_bytes(page_url)
        except Exception as e:
            log.warning("%s : fetch KO page %d (%s)", sid, page_idx, e)
            break
        html_body = payload.decode("utf-8", errors="replace")
        page_reports = _extract_reports(html_body)
        if not page_reports:
            if page_idx == 0:
                log.warning("%s : aucun rapport extrait (layout AN changé ?)", sid)
            break
        new_count = 0
        for r in page_reports:
            did = r.get("data_id")
            if did and did not in seen_data_ids:
                seen_data_ids.add(did)
                reports.append(r)
                new_count += 1
        # Pas de nouveau item → l'AN ne pagine plus (peu importe la raison),
        # on s'arrête pour ne pas multiplier les fetches inutiles.
        if new_count == 0:
            log.debug("%s : page %d sans nouveau item, arrêt", sid, page_idx)
            break
    if not reports:
        return []

    items: list[Item] = []
    pdf_hits = 0
    for r in reports:
        # R42-B : extraction du corps PDF si url_pdf disponible.
        haystack_body = _fetch_pdf_haystack(r["url_pdf"], max_chars=body_max)
        if haystack_body:
            pdf_hits += 1
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
                # R42-AJ : "RAPP" | "RINF" | "AVIS". Vide pour les anciens
                # items en DB pré-R42-AJ (champ ajouté). Le frontmatter Hugo
                # peut afficher une étiquette différenciée si souhaité plus
                # tard, sans casser la rétrocompat (fallback affichage si
                # vide = "Rapport").
                "doc_type": r.get("doc_type", ""),
                "url_dossier": r["url_dossier"],
                "url_pdf": r["url_pdf"],
                # R42-B : corps PDF tronqué à body_max chars pour matcher.
                # Vide si fetch/parse KO ou pypdf absent — graceful degrade.
                "haystack_body": haystack_body[:body_max],
            },
        ))
    # R42-AJ : log la répartition par type (RAPP/RINF/AVIS) pour suivre
    # la couverture après l'élargissement du regex et le dispatch des
    # trois listings AN distincts (rapports / rapports-information / avis).
    by_type: dict[str, int] = {}
    for it in items:
        dt = (it.raw.get("doc_type") or "?")
        by_type[dt] = by_type.get(dt, 0) + 1
    type_summary = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
    log.info(
        "%s : %d documents extraits (%d avec corps PDF, max %d chars, "
        "%d page(s) fetchée(s)) — %s",
        sid, len(items), pdf_hits, body_max, max_pages, type_summary,
    )
    return items
