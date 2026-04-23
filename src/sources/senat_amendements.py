"""Connecteur Sénat — amendements via URLs per-texte.

Le dump AMELI bulk (`data.senat.fr/data/ameli/ameli.zip`, 149 Mo) est un
dump PostgreSQL plain (un seul fichier `var/opt/opendata/ameli.sql`),
non exploitable sans parser SQL. Le loader `senat.py` attendait un zip
de CSV et n'ingérait donc rien (filtre `.csv` ligne 133 → 0 item).

Pour alimenter la veille temps-réel, on utilise à la place les CSV
unitaires exposés par le Sénat pour chaque texte amendé :

  https://www.senat.fr/amendements/<session>/<num>/jeu_complet_<session>_<num>.csv
  https://www.senat.fr/amendements/commissions/<session>/<num>/jeu_complet_commission_<session>_<num>.csv

Doc officielle : `data.senat.fr/ameli/` + exemples validés en live
avril 2026 sur 2024-2025/300 (commission) et 2025-2026/200 (séance).

Approche :
1. Fetch `https://www.senat.fr/akomantoso/depots.xml` (index des textes)
2. Filtre par `since_days` (`lastModifiedDateTime`)
3. Ne garde que les types déposables (pjl, ppl, ppr, plf, plfss, pjlo, pplo)
4. Convertit session Sénat 2 chiffres → session CSV 4-4 (ex: 25 → 2025-2026)
5. Fetch `.akn.xml` unitaire pour récupérer le titre humain du dossier
   (réutilisé pour enrichir le summary amendement — R11b)
6. Fetch CSV séance + CSV commission (404 silencieux = texte non amendé)
7. Parse CSV : délimiteur TAB, première ligne `sep=\\t` skippée, cp1252
   fallback utf-8
8. Normalise chaque ligne en Item amendement avec :
   - title : "Amendement n°X — Auteur · sur « Titre dossier »"
   - summary : "Dossier : ... || Objet ... || Dispositif ..."
     (titre dossier en premier pour permettre au matcher de retomber sur
      le thème du dossier, cf R11b côté AN)

Colonnes CSV observées (validées sur 2024-2025/300 commission et
2025-2026/200 séance, 18 avril 2026) :
    Nature | Numéro | Subdivision | Alinéa | Auteur | Au nom de |
    Date de dépôt | Dispositif | Objet | Sort | Date de saisie du sort |
    Url amendement | Fiche Sénateur

Dispositif et Objet arrivent en HTML avec entités (`&#233;` etc) : on
dégraisse via `_strip_html` pour alimenter le matcher proprement.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import logging
import re
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

import httpx

from ..models import Item
from ._common import USER_AGENT, CONTACT_EMAIL, fetch_bytes
from .senat_akn import _AKN_URL_RE, _parse_last_modified, parse_bill

log = logging.getLogger(__name__)


# Plafonds : depots.xml contient ~750 entrées remontant à 2023 ; on se
# limite aux plus récentes par `since_days`, avec un garde-fou global.
_MAX_TEXTS_PER_RUN = 300
# Un gros PLF peut dépasser 3 000 amendements. On cap à 2 000 / texte
# pour borner la mémoire et laisser le pipeline tourner dans les 10 min
# habituelles (300 × 2 000 = 600 000 Items max, jamais atteint en pratique).
_MAX_AMDT_PER_TEXTE = 2000

# Types de textes réellement amendables (les `tas`/`td` sont des sorties
# de navette, leur numéro ne mappe pas vers un CSV amendements).
_AMENDABLE_TYPES = {"pjl", "ppl", "ppr", "plf", "plfss", "pjlo", "pplo"}


def _session_to_csv(session2: str) -> str:
    """Convertit `25` (format Sénat 2 chiffres) → `2025-2026` (format CSV)."""
    if not session2 or len(session2) != 2 or not session2.isdigit():
        return ""
    yr = 2000 + int(session2)
    return f"{yr}-{yr + 1}"


def _strip_html(s: str) -> str:
    """R32 (2026-04-24) : délégué à `src.textclean.strip_html` (audit §4.2).

    Les champs Dispositif/Objet arrivent en HTML (`<body><p>…`). Sans
    dégraissage le matcher reçoit du bruit (`<p>`, `&#233;`) plutôt que
    les mots-clés utiles. `textclean.strip_html` gère aussi les espaces
    insécables (\\u00a0, \\u202f, etc.).
    """
    from .. import textclean
    return textclean.strip_html(s)


def _decode(payload: bytes) -> str:
    """Décode un CSV Sénat. L'encodage historique est cp1252 ; quelques
    textes récents passent en UTF-8. On tente l'UTF-8 strict d'abord
    (décode clean), puis cp1252 en fallback (toujours valide sur bytes)."""
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return payload.decode("cp1252")
    except UnicodeDecodeError:
        pass
    return payload.decode("utf-8", errors="replace")


def _read_amendements_csv(payload: bytes) -> list[dict]:
    """Parse un `jeu_complet_<session>_<num>.csv` tab-delimited.

    Format observé :
      - Ligne 1 : `sep=\\t` (hint Excel, à skipper)
      - Ligne 2 : en-tête (colonnes séparées par tabulation)
      - Lignes 3+ : données
    """
    text = _decode(payload)
    # Skip la ligne "sep=..." si présente
    lines = text.split("\n", 1)
    if lines and lines[0].strip().lower().startswith("sep="):
        text = lines[1] if len(lines) > 1 else ""

    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    rows: list[dict] = []
    for row in reader:
        clean: dict[str, str] = {}
        for k, v in (row or {}).items():
            key = (k or "").strip()
            val = (v or "").strip() if isinstance(v, str) else ""
            if key:
                clean[key] = val
        # Skip lignes vides (toutes valeurs vides)
        if any(clean.values()):
            rows.append(clean)
    return rows


# httpx direct : pour ne pas logger un WARN sur chaque 404 (le hit-rate
# des CSV per-texte est d'environ 10-20 % → la majorité des appels sont
# des 404 "silencieux" normaux). `fetch_bytes` du module partagé logge
# systématiquement + retry, ce qui génère trop de bruit sur ces routes.
def _try_fetch(url: str, *, timeout: float = 30.0) -> bytes | None:
    """Fetch silencieux : renvoie None sur 404, bytes sinon.

    Log DEBUG sur 404 (flood possible), WARN sur autres erreurs.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "From": CONTACT_EMAIL,
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as c:
            r = c.get(url)
            if r.status_code == 404:
                log.debug("404 (attendu) : %s", url)
                return None
            r.raise_for_status()
            return r.content
    except httpx.HTTPStatusError as e:
        log.warning("fetch KO %s → %s", url, e.response.status_code)
    except Exception as e:
        log.warning("fetch KO %s (%s)", url, e)
    return None


def _build_item(
    src: dict,
    row: dict,
    session_csv: str,
    num: str,
    route: str,  # "seance" | "commission"
    titre_dossier: str,
    dossier_url: str,
) -> Item | None:
    """Convertit une ligne CSV en `Item` amendement."""
    sid = src["id"]
    cat = src["category"]

    # Colonnes principales (fallback sans accents au cas où)
    nature = (row.get("Nature") or "").strip()
    numero = (row.get("Numéro") or row.get("Numero") or "").strip()
    if not numero:
        return None
    auteur = (row.get("Auteur") or "").strip()
    au_nom_de = (row.get("Au nom de") or "").strip()
    date_depot = (
        row.get("Date de dépôt") or row.get("Date de depot") or ""
    ).strip()
    dispositif = _strip_html(row.get("Dispositif") or "")
    objet = _strip_html(row.get("Objet") or "")
    sort = (row.get("Sort") or "").strip()
    url_amdt = (row.get("Url amendement") or "").strip()
    subdivision = (row.get("Subdivision") or "").strip()
    alinea = (row.get("Alinéa") or row.get("Alinea") or "").strip()

    # R23-C5 (2026-04-23) : URL fiche sénateur + URL photo portrait
    # construite depuis le slug senfic (ex. wattebled_dany19585h).
    # La colonne "Fiche Sénateur" arrive typiquement sous la forme
    # `//www.senat.fr/senfic/<slug>.html` (pas de schema). On normalise
    # pour expose un auteur_url https et un auteur_photo_url via
    # amo_loader.build_photo_url_senat (pattern /senimg/<slug>_carre.jpg).
    fiche_senateur = (row.get("Fiche Sénateur") or row.get("Fiche Senateur") or "").strip()
    auteur_url = ""
    auteur_photo_url = ""
    if fiche_senateur:
        # Normalisation du schema (//www… → https://www…)
        if fiche_senateur.startswith("//"):
            auteur_url = "https:" + fiche_senateur
        elif fiche_senateur.startswith("http"):
            auteur_url = fiche_senateur
        else:
            # chemin relatif rare : on prefixe senat.fr
            auteur_url = "https://www.senat.fr" + (
                fiche_senateur if fiche_senateur.startswith("/") else f"/{fiche_senateur}"
            )
        # L'URL photo se deduit du slug, meme si on n'a pas pu normaliser
        # l'auteur_url (build_photo_url_senat accepte le format raw).
        from ..amo_loader import build_photo_url_senat
        auteur_photo_url = build_photo_url_senat(fiche_senateur) or ""

    published_at: datetime | None = None
    if date_depot:
        try:
            published_at = datetime.strptime(date_depot, "%Y-%m-%d")
        except ValueError:
            pass
    if published_at is None:
        published_at = datetime.utcnow()

    # UID déterministe — stable entre runs, dédup par store.
    uid_src = f"{sid}:{session_csv}:{num}:{route}:{numero}"
    uid = hashlib.sha1(uid_src.encode()).hexdigest()[:16]

    # Titre : "Amdt n°42 rect. · sur « Titre dossier »"
    # R13-G : "Amdt" au lieu de "Amendement".
    # R13-O (2026-04-21) : auteur retiré du titre (affiché avant via
    # .auteur-inline côté template). Cohérent avec R13-L sur les questions.
    title_bits = [f"Amdt n°{numero}"]
    if titre_dossier:
        tr = titre_dossier[:80].rstrip()
        if len(titre_dossier) > 80:
            tr += "…"
        title_bits.append(f"· sur « {tr} »")
    title = " ".join(title_bits)[:220]

    # Summary : titre dossier EN PREMIER (priorité matcher R11b), puis
    # objet (résumé humain de l'amendement), puis dispositif (texte
    # juridique pour enrichir le haystack mots-clés).
    parts: list[str] = []
    if titre_dossier:
        parts.append(f"Dossier : {titre_dossier}")
    if objet:
        parts.append(objet[:900])
    if dispositif:
        parts.append(dispositif[:900])
    if sort:
        parts.append(f"Sort : {sort}")
    if au_nom_de:
        parts.append(f"Au nom de : {au_nom_de}")
    summary = " || ".join(parts)[:3000]

    url_final = url_amdt or dossier_url or (
        f"https://www.senat.fr/dossier-legislatif/ppl{session_csv[2:4]}-{num}.html"
    )

    return Item(
        source_id=sid,
        uid=uid,
        category=cat,
        chamber="Senat",
        title=title,
        url=url_final,
        published_at=published_at,
        summary=summary,
        raw={
            "path": "senat:amendements_per_texte",
            "session": session_csv,
            "num_texte": num,
            "route": route,
            "nature": nature,
            "numero": numero,
            "auteur": auteur,
            "au_nom_de": au_nom_de,
            "sort": sort,
            "subdivision": subdivision,
            "alinea": alinea,
            "dossier_titre": titre_dossier,
            "dossier_url": dossier_url,
            # R23-C5 (2026-04-23) : photo portrait senateur (pattern
            # /senimg/<slug>_carre.jpg) + URL fiche senateur avec https.
            # Consommes cote site_export pour exposer dans le frontmatter.
            "auteur_url": auteur_url,
            "auteur_photo_url": auteur_photo_url,
        },
    )


def fetch_source(src: dict) -> list[Item]:
    """Itère `depots.xml` et fetche les CSV amendements per-texte.

    Param `src` attendu (sources.yml) :
      - id            : source_id (ex. "senat_amendements")
      - category      : "amendements"
      - format        : "akn_discussion"
      - since_days    : fenêtre de filtrage sur lastModifiedDateTime (défaut 90)
      - index_url     : override facultatif (défaut depots.xml officiel)
    """
    sid = src["id"]
    since_days = int(src.get("since_days") or 90)
    cutoff = datetime.now() - timedelta(days=since_days)

    idx_url = src.get("index_url") or "https://www.senat.fr/akomantoso/depots.xml"
    log.info("Sénat %s : fetch index %s (since %dj)", sid, idx_url, since_days)
    try:
        idx_bytes = fetch_bytes(idx_url)
    except Exception as e:
        log.exception("Sénat %s : index KO %s", sid, e)
        return []

    try:
        idx_root = ET.fromstring(idx_bytes)
    except ET.ParseError as e:
        log.warning("Sénat %s : index XML KO (%s)", sid, e)
        return []

    entries: list[tuple[str, datetime | None, str, str, str]] = []
    for te in idx_root.findall(".//text"):
        u_el = te.find("url")
        dt_el = te.find("lastModifiedDateTime")
        u = (u_el.text or "").strip() if u_el is not None and u_el.text else ""
        dt = _parse_last_modified(dt_el.text if dt_el is not None else None)
        if not u:
            continue
        m = _AKN_URL_RE.search(u)
        if not m:
            continue
        btype = (m.group("type") or "").lower()
        if btype not in _AMENDABLE_TYPES:
            continue
        session2 = m.group("session")
        num_full = m.group("num")
        # Strip suffixes rectifs (ex. "561rec", "561recbis") — l'URL CSV
        # utilise toujours le numéro principal.
        m_num = re.match(r"^(\d+)", num_full)
        num = m_num.group(1) if m_num else num_full
        if dt and dt < cutoff:
            continue
        entries.append((u, dt, btype, session2, num))

    # Dédup par (session, num) : un même texte peut apparaître en
    # plusieurs manifestations (rec, recbis) — on garde la plus récente.
    best: dict[tuple[str, str], tuple[str, datetime | None, str, str, str]] = {}
    for e in entries:
        key = (e[3], e[4])
        cur = best.get(key)
        if cur is None or (e[1] and (not cur[1] or e[1] > cur[1])):
            best[key] = e
    entries = list(best.values())

    entries.sort(key=lambda x: x[1] or datetime(1970, 1, 1), reverse=True)
    entries = entries[:_MAX_TEXTS_PER_RUN]
    log.info(
        "Sénat %s : %d textes candidats (après since_days + dédup)",
        sid, len(entries),
    )

    items: list[Item] = []
    stats = {
        "bill_ok": 0,
        "bill_ko": 0,
        "seance_hits": 0,
        "commission_hits": 0,
        "amendements": 0,
    }

    for u, _dt, _btype, session2, num in entries:
        session_csv = _session_to_csv(session2)
        if not session_csv:
            continue

        # Fetch bill.akn.xml pour titre dossier (enrichit le haystack)
        titre_dossier = ""
        dossier_url = ""
        try:
            xml = fetch_bytes(u)
            bill = parse_bill(xml, u)
            if bill:
                titre_dossier = bill.get("titre", "") or ""
                dossier_url = bill.get("url_senat", "") or u
                stats["bill_ok"] += 1
            else:
                stats["bill_ko"] += 1
        except Exception as e:
            log.warning("Sénat %s : bill %s KO (%s)", sid, u, e)
            stats["bill_ko"] += 1

        # Fetch CSV séance + CSV commission — 404 = pas encore amendé.
        for route, csv_url in (
            (
                "seance",
                f"https://www.senat.fr/amendements/{session_csv}/{num}"
                f"/jeu_complet_{session_csv}_{num}.csv",
            ),
            (
                "commission",
                f"https://www.senat.fr/amendements/commissions/{session_csv}/{num}"
                f"/jeu_complet_commission_{session_csv}_{num}.csv",
            ),
        ):
            payload = _try_fetch(csv_url)
            if not payload:
                continue
            try:
                rows = _read_amendements_csv(payload)
            except Exception as e:
                log.warning("Sénat %s : CSV %s KO (%s)", sid, csv_url, e)
                continue
            if not rows:
                continue
            stats[f"{route}_hits"] += 1
            for row in rows[:_MAX_AMDT_PER_TEXTE]:
                it = _build_item(
                    src, row, session_csv, num, route,
                    titre_dossier, dossier_url,
                )
                if it is not None:
                    items.append(it)
                    stats["amendements"] += 1

    log.info(
        "Sénat %s : %d items produits (%s)",
        sid, len(items),
        ", ".join(f"{k}={v}" for k, v in stats.items()),
    )
    return items
