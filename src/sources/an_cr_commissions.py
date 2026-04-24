"""R35-B (2026-04-24) — Scraper des comptes rendus de commissions AN.

Ingère le corps complet (PDF → texte) des CR de commissions permanentes AN
pour alimenter le `haystack_body` du matcher keyword.

Contexte : le pipeline agenda (`assemblee._normalize_agenda`) capte les
RÉUNIONS de commission via `Agenda.json.zip` mais uniquement avec le
titre ODJ. Le CORPS du compte rendu n'est publié qu'a posteriori
(~quelques jours après la réunion) sous forme de PDF à l'URL :

    /dyn/17/comptes-rendus/{slug}/l17{slug}{SS}{NNN}_compte-rendu.pdf

où `{slug}` = slug court de la commission (ex. cion-cedu), `{SS}` =
année-session (ex. 2526 pour octobre 2025 → septembre 2026), `{NNN}` = n°
séquentiel de réunion dans la session.

Le numéro séquentiel {NNN} n'est PAS dans le JSON Agenda (compteRenduRef
est null pour les commissions). On itère donc par force brute par
commission, à partir du dernier n° connu (état persisté dans
data/an_cr_state.json). Les runs suivants ne refont qu'un petit delta.

Cas déclencheur (Cyril, R35-B) : commission cion-cedu réunion 58
(2026-04-22), « Table ronde sur la gouvernance des autres sports que le
football » — le titre agenda ne cite pas « sport » explicitement côté
ODJ, mais le PDF en cite 15+ occurrences. Avant R35-B : non matché.
Après : matché via haystack_body.
"""
from __future__ import annotations

import io
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import httpx

from ..models import Item
from ._common import _client

log = logging.getLogger(__name__)

# Commissions permanentes AN 17e législature. Mapping slug → libellé long.
# Les slugs sont ceux exposés dans les URLs publiques /dyn/17/comptes-rendus/
# (convention AN). Liste figée ici volontairement : on ne scrape QUE les
# commissions qui sont susceptibles d'aborder le sport (toutes y touchent
# via PLF/PLFSS et auditions transverses). Pour élargir, étendre le dict.
_DEFAULT_COMMISSIONS: dict[str, str] = {
    "cion-cedu":  "Commission des affaires culturelles et de l'éducation",
    "cion-soc":   "Commission des affaires sociales",
    "cion-etran": "Commission des affaires étrangères",
    "cion-def":   "Commission de la défense nationale et des forces armées",
    "cion-dvp":   "Commission du développement durable",
    "cion-eco":   "Commission des affaires économiques",
    "cion-fin":   "Commission des finances",
    "cion-lois":  "Commission des lois",
}

# State file : mémorise par session { slug: { last_num: int } }. Sans ça,
# un run CI scannerait intégralement 1..N pour les 8 commissions à chaque
# itération (~400 requêtes/run, la plupart en 404). Avec state : on reprend
# au dernier n° connu + delta court (≤ max_new_per_run).
STATE_PATH = Path("data/an_cr_state.json")

# Regex parsing
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_DATE_FR_RE = re.compile(
    r"(\d{1,2})\s+(janvier|février|mars|avril|mai|juin|juillet|août|"
    r"septembre|octobre|novembre|décembre)\s+(\d{4})",
    re.IGNORECASE,
)
_MOIS_FR = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12,
}


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("an_cr_state.json illisible (%s), reset", e)
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _session_code(d: datetime) -> str:
    """Code session AN : 'SS' pour année session-1 + 'SS' pour session.

    Session parlementaire AN : ouverture 1er octobre → clôture 30 septembre.
    Ex. octobre 2025 → septembre 2026 : session 2025-2026 → code "2526".
    """
    y = d.year
    if d.month >= 10:
        return f"{y % 100:02d}{(y + 1) % 100:02d}"
    return f"{(y - 1) % 100:02d}{y % 100:02d}"


def _extract_pdf_text(pdf_bytes: bytes, max_chars: int = 10000) -> str:
    """Extrait le texte brut d'un PDF avec pypdf, tronque à max_chars.

    Si pypdf indisponible (dépendance non installée), renvoie "" sans
    planter — le matcher retombe alors sur le titre du CR seul.
    """
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        log.warning("an_cr_commissions : pypdf non installé, pas d'extraction")
        return ""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        log.debug("pypdf parse KO: %s", e)
        return ""
    out: list[str] = []
    total = 0
    for p in reader.pages:
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        if not t:
            continue
        out.append(t)
        total += len(t)
        if total >= max_chars:
            break
    merged = re.sub(r"\s+", " ", " ".join(out)).strip()
    return merged[:max_chars]


def _parse_title(html_text: str, commission_label: str, num: int) -> str:
    """Titre humain depuis <title> HTML. Formate en 'Commission — n° X'."""
    m = _TITLE_RE.search(html_text)
    if not m:
        return f"CR {commission_label} — n° {num}"[:220]
    raw = re.sub(r"\s+", " ", m.group(1)).strip()
    # Titre AN type : "Compte rendu de réunion n° 58 - Commission des affaires
    # culturelles et de l'éducation - Session 2025 – 2026 - 17e législature -
    # Assemblée nationale". On garde les 2 premières sections (CR + comm).
    parts = [p.strip() for p in raw.split(" - ")]
    if len(parts) >= 2:
        return f"{parts[0]} — {parts[1]}"[:220]
    return raw[:220]


def _parse_date(text: str) -> datetime | None:
    """Première date FR trouvée dans le texte (ex. '22 avril 2026')."""
    m = _DATE_FR_RE.search(text or "")
    if not m:
        return None
    day_s, mois_s, year_s = m.groups()
    mois = _MOIS_FR.get(mois_s.lower())
    if mois is None:
        return None
    try:
        return datetime(int(year_s), mois, int(day_s))
    except ValueError:
        return None


def _fetch_silent(url: str, timeout: float = 20.0) -> tuple[int, bytes]:
    """GET silencieux : renvoie (status, bytes) sans lever sur 4xx.

    On NE passe PAS par `_common.fetch_bytes` pour éviter les logs ERROR
    massifs sur les 404 attendus du brute-force (plusieurs dizaines par run).
    """
    try:
        with _client() as c:
            r = c.get(url, timeout=timeout)
            return r.status_code, r.content
    except httpx.RequestError as e:
        log.debug("GET %s : erreur réseau %s", url, e)
        return 0, b""


def _fetch_cr(slug: str, session: str, num: int,
              commission_label: str) -> Item | None:
    """Tente de récupérer un CR (slug, session, num). None si inexistant."""
    base = (
        f"https://www.assemblee-nationale.fr/dyn/17/comptes-rendus/"
        f"{slug}/l17{slug}{session}{num:03d}_compte-rendu"
    )
    html_url = base
    pdf_url = base + ".pdf"

    # 1) Vérifier que la page HTML existe (200). 404 → CR pas publié.
    status, html_content = _fetch_silent(html_url)
    if status != 200 or not html_content:
        return None
    html_text = html_content.decode("utf-8", errors="replace")

    # 2) Récupérer le PDF (source du corps). Best-effort : si 404 ou erreur,
    #    on expose quand même un item avec haystack_body vide plutôt que de
    #    rien renvoyer (la page HTML existe, donc le CR est référencé).
    pdf_status, pdf_bytes = _fetch_silent(pdf_url, timeout=30.0)
    body = _extract_pdf_text(pdf_bytes) if pdf_status == 200 else ""

    # 3) Date : on cherche d'abord dans le PDF (page de garde), sinon dans
    #    le HTML, sinon on pose "aujourd'hui" (le CR vient d'être publié).
    dt = (_parse_date(body[:2000] if body else "")
          or _parse_date(html_text)
          or datetime.utcnow().replace(microsecond=0))

    title = _parse_title(html_text, commission_label, num)

    # UID stable : ne se basera jamais sur le titre (qui pourrait varier
    # si le CR est republié avec une refonte AMO).
    uid = f"an-cr-{slug}-{session}-{num:03d}"

    # Summary : début du corps pour l'affichage site (fallback: titre).
    summary = (body[:2000] if body else title).strip()

    return Item(
        source_id="an_cr_commissions",
        uid=uid,
        category="comptes_rendus",
        chamber="AN",
        title=title,
        url=html_url,
        published_at=dt,
        summary=summary,
        raw={
            "path": "an_cr_commissions",
            "slug": slug,
            "session": session,
            "num": num,
            "pdf_url": pdf_url,
            # Exposé au KeywordMatcher (cf. keywords.apply, R26) :
            "haystack_body": body,
        },
    )


def fetch_source(src: dict) -> list[Item]:
    """Scrape les CR de commissions AN par force brute incrémentale.

    Paramètres supportés dans src :
      - commissions     : dict {slug: label} (défaut : _DEFAULT_COMMISSIONS)
                          ou liste de slugs (labels = slug).
      - session         : code session (ex. "2526"). Défaut : déduit de
                          la date courante.
      - max_new_per_run : nb max de CR nouveaux scrapés par commission
                          par run (défaut 10, évite les runaway au bootstrap).
      - miss_tolerance  : nb de 404 consécutifs avant d'arrêter une commission
                          (défaut 3).
      - max_num         : n° max absolu testé (garde-fou, défaut 99).
    """
    raw_comm = src.get("commissions") or _DEFAULT_COMMISSIONS
    if isinstance(raw_comm, list):
        commissions = {s: s for s in raw_comm}
    else:
        commissions = dict(raw_comm)

    max_new = int(src.get("max_new_per_run", 10))
    miss_tolerance = int(src.get("miss_tolerance", 3))
    max_num = int(src.get("max_num", 99))
    session = str(src.get("session") or _session_code(datetime.utcnow()))

    state = _load_state()
    session_state = state.setdefault(session, {})
    items: list[Item] = []

    for slug, label in commissions.items():
        slug_state = session_state.setdefault(slug, {"last_num": 0})
        start = max(1, int(slug_state.get("last_num", 0)) + 1)
        num = start
        miss = 0
        new_count = 0
        while miss < miss_tolerance and new_count < max_new and num <= max_num:
            it = _fetch_cr(slug, session, num, label)
            if it is not None:
                items.append(it)
                slug_state["last_num"] = num
                new_count += 1
                miss = 0
            else:
                miss += 1
            num += 1
        session_state[slug] = slug_state
        log.info(
            "an_cr_commissions %s session=%s : +%d items (last=%d, scan %d..%d)",
            slug, session, new_count,
            slug_state["last_num"], start, num - 1,
        )

    _save_state(state)
    return items
