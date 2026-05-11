"""Connecteur HTML générique — ministères, autorités, Matignon, info.gouv…

Stratégie : on télécharge la page d'atterrissage (listing presse / actualités),
on sélectionne les <a> portant un titre lisible, et on retient la date si on
la trouve dans le lien parent ou via un <time datetime>.

R13-M (2026-04-21) : ajout dispatch RSS (Conseil d'État, Conseil Constitutionnel)
et sitemap (CNOSF). Permet d'ingérer des sources tierces sans créer un
connecteur dédié par site.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup
from lxml import etree

from ..models import Item
from ._common import fetch_bytes, fetch_text, parse_iso

log = logging.getLogger(__name__)


# R13-M : cutoff ~120j pour les sources RSS/sitemap (cohérent avec la
# fenêtre communiques 90j côté site_export + marge pour absorber les
# retards d'ingestion).
_RSS_SITEMAP_CUTOFF_DAYS = 120


# R41-AS (2026-05-10) — Stop-list de slugs sitemap qui correspondent à
# des pages techniques / institutionnelles permanentes, pas à des
# actualités. Cas observé : sitemap CNOSF qui exposait `/accueil` avec
# un lastmod récent → l'item remontait avec titre = « Accueil ».
# Le filtre `url_filter: ["/"]` (ouvert) retient ces pages, et le slug
# reconstitue un titre trompeur. Filtre côté scraper, pas blocklist
# (générique : couvre les futurs sites Drupal/WP avec mêmes pages
# techniques).
_SITEMAP_STOP_SLUGS: frozenset[str] = frozenset({
    "accueil", "home", "index",
    "contact", "contacts", "nous-contacter",
    "mentions-legales", "mentions", "legal",
    "qui-sommes-nous", "presentation", "a-propos",
    "rgpd", "cgu", "cgv", "politique-confidentialite",
    "plan-du-site", "sitemap",
    "newsletter", "newsletters",
    "recherche", "search",
    "accessibilite", "credits",
})


_ANS_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s*-\s*(\d{1,2}):(\d{2})")


def _parse_ans_date(s: str) -> datetime | None:
    """Parse une date ANS Drupal au format `mer 01/04/2026 - 12:00`.

    Le RSS de l'ANS (agencedusport.fr/flux-rss) sort les pubDate au
    format jour-FR/JJ/MM/AAAA - HH:MM au lieu du RFC822, ce qui empêche
    feedparser de poser `published_parsed`. On parse manuellement la
    partie numérique. Retourne None si format inconnu (soft-fail)."""
    if not s:
        return None
    m = _ANS_DATE_RE.search(s)
    if not m:
        return None
    d, mo, y, h, mi = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None


_MIN_SPORTS_IGESR_PDF_RE = re.compile(
    r"/sites/default/files/(\d{4})-(\d{2})/([^/]+\.(?:pdf|PDF))$"
)


def _from_min_sports_igesr_html(src: dict) -> list[Item]:
    """R42-BJ (2026-05-11) — Handler dédié à la page MinSports listant
    les rapports IGESR « dans le champ du sport ».

    URL : https://www.sports.gouv.fr/rapports-de-l-igesr-dans-le-champ-du-sport-1703

    La page liste ~28 rapports IGESR sport sous forme de liens PDF directs
    vers `/sites/default/files/<YYYY-MM>/<slug>.pdf`. La date est extraite
    de l'URL (le PDF est archivé dans le dossier du mois où il a été mis
    en ligne sur le site MinSports — date d'archivage, suffisante pour
    le tri éditorial).

    Le titre est le texte du `<a>` (déjà bien formulé par le site MinSports,
    incluant souvent le numéro de rapport et le mois en clair).

    Chamber forcée MinSports. Cyril : page peu actualisée (1-3 rapports
    nouveaux par an), pas besoin de fenêtre courte → 1095j (3 ans) côté
    `WINDOW_DAYS_BY_SOURCE_ID`. Couvre tous les rapports archivés depuis
    2023.
    """
    impersonate = bool(src.get("impersonate", False))
    try:
        html = fetch_text(src["url"], impersonate=impersonate)
    except Exception as e:
        log.warning("HTML KO %s : %s", src["id"], e)
        return []
    soup = BeautifulSoup(html, "html.parser")
    chamber = src.get("chamber", "MinSports")
    seen: set[str] = set()
    out: list[Item] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = _MIN_SPORTS_IGESR_PDF_RE.search(href)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue
        url_abs = href if href.startswith("http") else urljoin(src["url"], href)
        if url_abs in seen:
            continue
        seen.add(url_abs)
        try:
            published_at = datetime(year, month, 1)
        except ValueError:
            published_at = None
        out.append(Item(
            source_id=src["id"],
            uid=url_abs[:200],
            category=src["category"],
            chamber=chamber,
            title=title[:220],
            url=url_abs,
            published_at=published_at,
            summary="",
            raw={"path": "min_sports_igesr_html"},
        ))
    log.info("%s : %d rapports IGESR sport extraits", src["id"], len(out))
    return out


# R42-BK (2026-05-11) — Regex extraction date depuis l'URL de l'image
# (WordPress range les uploads dans `/wp-content/uploads/YYYY/MM/`).
_INJEP_IMG_DATE_RE = re.compile(r"/wp-content/uploads/(\d{4})/(\d{2})/")


def _from_injep_sport_publications_html(src: dict) -> list[Item]:
    """R42-BK (2026-05-11) — Handler dédié à la page INJEP publications sport.

    URL : https://injep.fr/sport/les-publications-sport/

    Page WordPress avec ~92 publications sport (rapports, INJEP Analyses
    & synthèses, baromètres, études). Chaque publication est un
    `<li class="publication ...">` avec :
      - <h2><a href="/publication/<slug>/">Titre</a></h2>
      - <img src=".../wp-content/uploads/YYYY/MM/..."> → date approximative

    La date est extraite de l'URL de l'image de couverture (date de
    mise en ligne du visuel = date de publication à ±quelques jours,
    suffisante pour le tri et le filtre fenêtre).

    Cyril a validé l'abandon du RSS injep.fr/feed/ (= flux actualités,
    pas flux publications). Cette page couvre tout le catalogue sport
    INJEP. Fenêtre 1095j (3 ans) côté WINDOW_DAYS_BY_SOURCE_ID.

    Chamber forcée INJEP. La source est dans BYPASS_KEYWORDS_SOURCES
    (cf. R25-H) → tous les items remontent sans matching keyword (la
    page est déjà filtrée éditorialement « sport » par l'INJEP).
    """
    impersonate = bool(src.get("impersonate", False))
    try:
        html = fetch_text(src["url"], impersonate=impersonate)
    except Exception as e:
        log.warning("HTML KO %s : %s", src["id"], e)
        return []
    soup = BeautifulSoup(html, "html.parser")
    chamber = src.get("chamber", "INJEP")
    out: list[Item] = []
    seen: set[str] = set()
    for li in soup.select("li.publication"):
        # Titre + lien depuis le <h2><a>
        title_a = li.select_one("h2 a") or li.select_one("h3 a")
        if not title_a:
            continue
        title = title_a.get_text(" ", strip=True)
        href = (title_a.get("href") or "").strip()
        if not title or not href:
            continue
        url_abs = href if href.startswith("http") else urljoin(src["url"], href)
        if url_abs in seen:
            continue
        seen.add(url_abs)
        # Date depuis l'URL de l'image de couverture (uploads/YYYY/MM/).
        published_at: datetime | None = None
        img = li.find("img")
        if img:
            for attr in ("src", "data-src", "srcset"):
                v = img.get(attr) or ""
                m = _INJEP_IMG_DATE_RE.search(v)
                if m:
                    try:
                        published_at = datetime(int(m.group(1)), int(m.group(2)), 1)
                        break
                    except ValueError:
                        pass
        # Collection (INJEP Analyses & synthèses, JEUNESSES études…) pour
        # information éditoriale dans raw.
        collection = ""
        col_a = li.select_one("a[href*='/collection/']")
        if col_a:
            collection = col_a.get_text(" ", strip=True)[:120]
        out.append(Item(
            source_id=src["id"],
            uid=url_abs[:200],
            category=src["category"],
            chamber=chamber,
            title=title[:220],
            url=url_abs,
            published_at=published_at,
            summary=collection,  # affiché comme sous-titre/snippet sur les cards
            raw={"path": "injep_sport_publications_html",
                 "collection": collection},
        ))
    log.info("%s : %d publications sport INJEP extraites", src["id"], len(out))
    return out


def _from_ans_rss(src: dict) -> list[Item]:
    """R42-BG (2026-05-11) — Handler dédié au RSS Drupal de l'ANS
    (agencedusport.fr/flux-rss). 3 spécificités vs RSS standard :

    - `<title>` contient un `<a href="...">Texte</a>` au lieu du titre
      en clair → on strippe pour récupérer texte + href.
    - `<link>` retourne `…/view` parce que feedparser prend le texte du
      `<a>` interne, pas le href → on utilise l'href du title.
    - `<pubDate>` au format `mer 01/04/2026 - 12:00` (non RFC822) →
      `published_parsed` est None côté feedparser, on parse via
      `_parse_ans_date`.

    + dédup par GUID : le feed Drupal renvoie chaque item 2-3 fois.

    Chamber forcée à ANS (cohérent avec la source HTML scraping qu'on
    remplace via R42-BG). Le filtre cutoff RSS standard
    (_RSS_SITEMAP_CUTOFF_DAYS) reste appliqué pour cohérence avec les
    autres flux RSS.
    """
    impersonate = bool(src.get("impersonate", False))
    try:
        payload = fetch_bytes(src["url"], impersonate=impersonate)
    except Exception as e:
        log.warning("RSS KO %s : %s", src["id"], e)
        return []
    d = feedparser.parse(payload)

    chamber = src.get("chamber", "ANS")
    cutoff = datetime.utcnow() - timedelta(days=_RSS_SITEMAP_CUTOFF_DAYS)
    seen_guids: set[str] = set()
    out: list[Item] = []
    for e in d.entries:
        guid = (getattr(e, "id", "") or "").strip()
        if guid and guid in seen_guids:
            continue
        if guid:
            seen_guids.add(guid)

        # Title + lien : extraire depuis le <a> interne au <title>.
        raw_title = (getattr(e, "title", "") or "").strip()
        if not raw_title:
            continue
        soup = BeautifulSoup(raw_title, "html.parser")
        a = soup.find("a")
        if a is not None:
            title = a.get_text(" ", strip=True)
            href = (a.get("href") or "").strip()
            link = urljoin(src["url"], href) if href else ""
        else:
            title = raw_title
            link = (getattr(e, "link", "") or "").strip()
        if not title or not link:
            continue

        # Date FR : « mer 01/04/2026 - 12:00 ».
        dt = _parse_ans_date(getattr(e, "published", "") or "")
        if dt and dt < cutoff:
            continue
        out.append(Item(
            source_id=src["id"],
            uid=(guid or link)[:200],
            category=src["category"],
            chamber=chamber,
            title=title[:220],
            url=link,
            published_at=dt,
            summary="",
            raw={"path": "ans_rss", "guid": guid},
        ))
    log.info("%s : %d items RSS ANS (cutoff %dj)",
             src["id"], len(out), _RSS_SITEMAP_CUTOFF_DAYS)
    return out


def _from_rss_generic(src: dict) -> list[Item]:
    """Parse un flux RSS 2.0 / Atom — titre + lien + date + description.

    Tolérant : utilise feedparser (même lib que senat._normalize_rss).
    Chamber résolu via `_chamber(domain)` pour cohérence avec les badges
    d'affichage existants.

    R42-BB (2026-05-11) : `impersonate: true` propagé pour les flux RSS
    protégés par anti-bot (ex. insep.fr/fr/actualites.xml retourne HTTP
    418 « I'm a teapot » sans impersonate côté GHA).
    """
    impersonate = bool(src.get("impersonate", False))
    try:
        # R19-A : passer bytes à feedparser pour respecter l'encoding
        # déclaré (PI XML ou header Content-Type). Avec str + UTF-8 forcé,
        # les flux ISO-8859-15 comme les RSS thématiques Sénat produisent
        # du mojibake "ï¿œ" sur les caractères accentués / signes comme °.
        payload = fetch_bytes(src["url"], impersonate=impersonate)
    except Exception as e:
        log.warning("RSS KO %s : %s", src["id"], e)
        return []
    d = feedparser.parse(payload)
    domain = urlparse(src["url"]).netloc
    chamber = _chamber(domain)
    out: list[Item] = []
    cutoff = datetime.utcnow() - timedelta(days=_RSS_SITEMAP_CUTOFF_DAYS)
    for e in d.entries:
        uid = getattr(e, "id", None) or getattr(e, "link", "")
        if not uid:
            continue
        link = (getattr(e, "link", "") or "").strip()
        title = (getattr(e, "title", "") or "").strip()
        if not title:
            continue
        summary = (getattr(e, "summary", "") or getattr(e, "description", "") or "").strip()
        # Date : published_parsed / updated_parsed → datetime naïf UTC.
        dt = None
        for key in ("published_parsed", "updated_parsed"):
            t = getattr(e, key, None)
            if t:
                try:
                    dt = datetime(*t[:6])
                    break
                except Exception:
                    pass
        if dt and dt < cutoff:
            continue
        out.append(Item(
            source_id=src["id"],
            uid=uid[:200],
            category=src["category"],
            chamber=chamber,
            title=title[:220],
            url=link or src["url"],
            published_at=dt,
            summary=summary[:2000],
            raw={"path": "rss_generic", "link": link},
        ))
    log.info("%s : %d items RSS (cutoff %dj)", src["id"], len(out), _RSS_SITEMAP_CUTOFF_DAYS)
    return out


def _from_sitemap_generic(src: dict) -> list[Item]:
    """Parse un sitemap XML standard (schemas.sitemaps.org/0.9).

    Pour chaque <url>, récupère <loc> (URL), <lastmod> (date). Le titre est
    reconstruit depuis le dernier segment de l'URL (slug → mots). Filtre :
    cutoff date + motif d'URL ("actualites" / "actualite" / "news") pour
    exclure les pages statiques (mentions légales, etc.).
    """
    try:
        text = fetch_text(src["url"])
    except Exception as e:
        log.warning("Sitemap KO %s : %s", src["id"], e)
        return []
    try:
        root = etree.fromstring(text.encode("utf-8"))
    except Exception as e:
        log.warning("Sitemap XML KO %s : %s", src["id"], e)
        return []
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    domain = urlparse(src["url"]).netloc
    chamber = _chamber(domain)
    cutoff = datetime.utcnow() - timedelta(days=_RSS_SITEMAP_CUTOFF_DAYS)
    out: list[Item] = []
    # Motif optionnel de filtre URL (défaut : mots-clés d'actualité courants).
    url_filter_patterns = src.get("url_filter") or [
        "actualites", "actualite", "actu-", "news", "communique", "presse",
    ]
    # R22c (2026-04-23) : certaines sources (CNOSF/Drupal) publient un
    # sitemap avec <loc> en protocol-relative (`domaine.tld/slug`, sans
    # https://). On détecte et préfixe `https://` pour obtenir une URL
    # absolue exploitable en aval (Hugo, recherche, etc.).
    for url_node in root.findall("s:url", ns):
        loc = (url_node.findtext("s:loc", namespaces=ns) or "").strip()
        if not loc:
            continue
        # Normalisation protocol-relative → https://
        if loc.startswith("//"):
            loc = "https:" + loc
        elif not loc.startswith(("http://", "https://")):
            # Soit "/slug" (chemin absolu) → préfixer schema + domaine du sitemap.
            # Soit "domaine.tld/slug" (CNOSF) → préfixer "https://".
            if loc.startswith("/"):
                loc = f"https://{domain}{loc}"
            else:
                loc = "https://" + loc
        # Filtre : doit matcher au moins un pattern "actualité-like".
        low = loc.lower()
        if not any(p in low for p in url_filter_patterns):
            continue
        last = parse_iso((url_node.findtext("s:lastmod", namespaces=ns) or "").strip())
        # R22c : skip si pas de lastmod (évite d'ingérer les pages racines,
        # /en, /accueil sans date → published_at=None pollue les tris).
        if not last:
            continue
        if last < cutoff:
            continue
        # Titre reconstruit depuis le slug du dernier segment.
        slug = loc.rstrip("/").rsplit("/", 1)[-1]
        # R41-AS : skip les slugs techniques (accueil, contact, mentions
        # légales…) — ce ne sont pas des actualités même si elles ont un
        # lastmod récent dans le sitemap (ex. CNOSF /accueil → titre
        # « Accueil » polluait la page Publications).
        if slug.lower() in _SITEMAP_STOP_SLUGS:
            continue
        title = slug.replace("-", " ").replace("_", " ").strip()[:200]
        if not title:
            continue
        title = title[:1].upper() + title[1:]
        uid = hashlib.sha1(loc.encode("utf-8")).hexdigest()[:16]
        out.append(Item(
            source_id=src["id"],
            uid=uid,
            category=src["category"],
            chamber=chamber,
            title=title[:220],
            url=loc,
            published_at=last,
            summary="",
            raw={"path": "sitemap_generic", "loc": loc,
                 "lastmod": str(last) if last else ""},
        ))
    log.info("%s : %d items sitemap (cutoff %dj)", src["id"], len(out),
             _RSS_SITEMAP_CUTOFF_DAYS)
    # R22e-2 : enrichissement meta description (opt-in par source).
    # Les sitemaps XML ne donnent que <loc>+<lastmod> → summary="" toujours.
    # Activer fetch_meta permet au matcher de voir le corps de l'article
    # (CNOSF : slug reconstitué ne suffit pas toujours au matching).
    if src.get("fetch_meta"):
        limit = int(src.get("fetch_meta_limit", 60))
        impersonate = bool(src.get("impersonate", False))
        _enrich_with_meta(out, impersonate=impersonate, limit=limit)
    return out


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
# R22e-1 (2026-04-23) : format numérique FR "16/04/2026" (jour d'abord).
# ANJ /communiques-de-presse expose les dates en <li> sous la forme
# `<a>titre</a> (16/04/2026)` → _DATE_FR_PAT (mois littéral) et _DATE_PAT
# (année d'abord) ne matchaient pas → 120/131 items ANJ avaient
# published_at=None. Aussi usité par d'autres sites FR (AFP, préfectures).
# On accepte aussi "16.04.2026" (séparateur point) et "16-04-2026"
# (séparateur tiret avec année à 4 chiffres en 3e position).
_DATE_FR_NUMERIC_PAT = re.compile(
    r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b"
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
    # R22d (2026-04-23) : profondeur étendue à 4 pour couvrir les layouts
    # Drupal Views où la date siège dans un div cousin profond. Ex. ANS :
    # <a> → span → .field-content → .views-field-views-field-title → .views-row
    # (5 niveaux). Les 4 parents retenus remontent jusqu'au .views-row qui
    # englobe toute la vignette (image, date, titre, corps), permettant à
    # _DATE_FR_PAT.search() de trouver "01 avril 2026" dans le texte voisin.
    _STOP_TAGS = {"body", "html", "main"}
    parents = []
    for anc in a.parents:
        if anc is None:
            break
        if getattr(anc, "name", None) in _STOP_TAGS:
            break
        parents.append(anc)
        if len(parents) >= 4:
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
    #    des 4 premiers parents (on récupère leur texte complet).
    #    R22d : 2 → 4 pour atteindre la "row" Drupal Views (ANS) où la
    #    date cohabite avec le lien titre à travers un cousin profond.
    texts = [a.get_text(" ", strip=True) or ""]
    for anc in parents[:4]:
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
    # 3bis. Format numérique français "DD/MM/YYYY" (ANJ, AFP, préfectures).
    #       R22e-1 (2026-04-23). On place APRÈS le format littéral pour
    #       éviter qu'une date du type "16 avril 2026" soit mal lue par
    #       cette regex moins précise (le séparateur de _DATE_FR_NUMERIC
    #       n'accepte que /.- donc pas de risque réel de collision, mais
    #       l'ordre reflète la priorité sémantique).
    for text in texts:
        m = _DATE_FR_NUMERIC_PAT.search(text)
        if m:
            day = int(m.group(1))
            month = int(m.group(2))
            year = int(m.group(3))
            # Garde-fou : rejette si jour > 31 ou mois > 12 (évite de
            # matcher un ID "1234/56/7890" qui aurait la bonne forme).
            if 1 <= day <= 31 and 1 <= month <= 12:
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
    if "afd.fr" in d:
        # R41-AN (2026-05-10) — Agence Française de Développement.
        # Volet « Sport et développement » : bilan annuel + actions
        # programmatiques sport pour tous, JO 2024, CAN, etc. Le matcher
        # keyword filtre naturellement le bruit non-sport (climat, agri…).
        return "AFD"
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
    # R19+ (2026-04-23) — INJEP (WordPress injep.fr), opérateur MinSports.
    # Ajout à cette place (avant le bloc *.gouv.fr) car le domaine est
    # injep.fr (non gouv.fr).
    if "injep.fr" in d:
        return "INJEP"
    # R23-I (2026-04-23) — INSEP (Institut National du Sport, de l'Expertise
    # et de la Performance). Drupal 11, pas de flux RSS sur /feed mais
    # /fr/actualites.xml expose un RSS 2.0 propre (Drupal Views). Opérateur
    # MinSports (établissement public national). Domaine insep.fr (non gouv).
    if "insep.fr" in d:
        return "INSEP"
    # R23-J (2026-04-23) — FDSF (Fondation du Sport Français, reconnue
    # d'utilité publique, adossée au CNOSF). Site Squarespace — feed RSS
    # natif via `?format=rss` sur la page blog `/web/fsf/actualites`.
    if "fondation-du-sport-francais.fr" in d:
        return "FDSF"
    # R13-J (2026-04-21) : senat_agenda scraper via html_generic (pas de flux
    # JSON/XML officiel pour l'agenda Sénat). Le domaine www.senat.fr ne
    # matche aucun des blocs spécifiques plus haut.
    if "senat.fr" in d:
        return "Senat"
    # R13-M (2026-04-21) : hautes juridictions ajoutées en publications.
    # R22 (2026-04-23) : Cassation sortie du scope (site JS-only, pas de
    # flux officiel) — mapping retiré. AdlC ajoutée en remplacement.
    if "conseil-etat.fr" in d:
        return "CE"
    if "conseil-constitutionnel.fr" in d:
        return "CC"
    if "autoritedelaconcurrence.fr" in d:
        return "AdlC"
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


# R22e-2 (2026-04-23) : enrichissement `summary` via meta description
# de la page article. Motivation : les listings HTML (ANJ, ministères,
# AAI…) n'exposent qu'un <a> + titre, pas de chapo → le KeywordMatcher
# ne travaille que sur le titre et rate les articles dont le sujet sport
# ne figure que dans le corps. Exemple : ANJ "Bilan 2025 du marché des
# jeux d'argent" ne cite pas "paris sportifs" dans le titre mais dans
# la balise <meta name="description"> ("…les paris sportifs en ligne
# dont le chiffre d'affaires a progressé de plus de 10%…").
#
# Activation par flag `fetch_meta: true` dans sources.yml (opt-in, pour
# ne pas tripler le volume de requêtes des sources qui marchent déjà).
# Borne `fetch_meta_limit` (défaut 60) : on enrichit les N premiers items
# du listing (les plus récents) pour capper le coût réseau par run.
_META_DESC_RE = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:description|og:description|twitter:description)'
    r'["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Certains sites mettent l'attribut `content` avant `name` → regex symétrique.
_META_DESC_RE_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']'
    r'(?:description|og:description|twitter:description)["\']',
    re.IGNORECASE,
)


def _extract_meta_description(html: str) -> str:
    """Extrait la balise <meta name=description> (ou og/twitter fallback).

    Parcourt dans l'ordre : description (standard) > og:description >
    twitter:description. Renvoie "" si aucune balise trouvée ou si le
    contenu est trop court (< 30 chars — probablement du boilerplate).
    """
    for rx in (_META_DESC_RE, _META_DESC_RE_REV):
        m = rx.search(html)
        if m:
            desc = m.group(1).strip()
            # Décode les entités HTML basiques (&amp; &#x27; …)
            import html as _html_lib
            desc = _html_lib.unescape(desc)
            if len(desc) >= 30:
                return desc[:2000]
    return ""


def _enrich_with_meta(items: list[Item], *, impersonate: bool, limit: int = 60) -> None:
    """Enrichit `item.summary` in-place pour les N premiers items sans
    summary, via un fetch ciblé de la page article + extraction meta.
    """
    fetched = 0
    for it in items:
        if fetched >= limit:
            break
        if it.summary:
            continue
        try:
            page = fetch_text(it.url, impersonate=impersonate)
        except Exception as e:
            log.debug("fetch_meta KO %s : %s", it.url, e)
            continue
        desc = _extract_meta_description(page)
        if desc:
            it.summary = desc
            fetched += 1
    log.info("fetch_meta : %d items enrichis (limite %d)", fetched, limit)


def fetch_source(src: dict) -> list[Item]:
    # R13-M (2026-04-21) : dispatch format rss / sitemap en amont du HTML
    # classique. Permet d'activer Conseil d'État / Conseil Constitutionnel
    # (RSS officiel) et CNOSF (sitemap.xml Drupal) sans connecteur dédié.
    fmt = (src.get("format") or "html").lower()
    if fmt == "rss":
        return _from_rss_generic(src)
    if fmt == "ans_rss":
        return _from_ans_rss(src)
    if fmt == "min_sports_igesr_html":
        return _from_min_sports_igesr_html(src)
    if fmt == "injep_sport_publications_html":
        return _from_injep_sport_publications_html(src)
    if fmt == "sitemap":
        return _from_sitemap_generic(src)

    # R18 : `impersonate=True` dans le YAML → fetch via curl_cffi pour passer
    # les protections Cloudflare (education/interieur/economie/info.gouv…).
    impersonate = bool(src.get("impersonate", False))
    try:
        html = fetch_text(src["url"], impersonate=impersonate)
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
    # R41-AD (2026-05-09) : ajout des sélecteurs Drupal Views (cas ANS
    # agencedusport.fr — les actualités sont dans `div.views-row` avec
    # le titre dans `div.views-field-title > a`). Couvre aussi les autres
    # sites Drupal de notre veille qui exposent des Views.
    selectors = [
        "article a", "h2 a", "h3 a",
        "a.fr-card__link", "a.news-item__link",
        # Drupal Views (R41-AD)
        "div.views-field-title a", "div.views-field-view-node a",
        "div.views-row > a", "div.views-row h2 a", "div.views-row h3 a",
        # Patterns d'URL génériques
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
    # R22e-2 : enrichissement meta description (opt-in par source).
    if src.get("fetch_meta"):
        limit = int(src.get("fetch_meta_limit", 60))
        _enrich_with_meta(out, impersonate=impersonate, limit=limit)
    return out
