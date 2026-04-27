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
# R36-A (2026-04-24) — ajout du groupe d'études Sport. Les GE publient leurs
# comptes rendus / bulletins sous le même chemin `/dyn/17/comptes-rendus/<slug>/`
# que les commissions, sur le portail AN. Cyril a confirmé le gap : les GE
# (Sport en priorité) n'étaient pas couverts. Le slug `ge-sport` est le slug
# officiel AN pour le groupe d'études Sport (vérifié sur /dyn/17/organes/ge-sport
# qui existe).
_DEFAULT_COMMISSIONS: dict[str, str] = {
    "cion-cedu":  "Commission des affaires culturelles et de l'éducation",
    "cion-soc":   "Commission des affaires sociales",
    "cion-etran": "Commission des affaires étrangères",
    "cion-def":   "Commission de la défense nationale et des forces armées",
    "cion-dvp":   "Commission du développement durable",
    "cion-eco":   "Commission des affaires économiques",
    "cion-fin":   "Commission des finances",
    "cion-lois":  "Commission des lois",
    # R36-A (2026-04-24) — groupe d'études Sport (GE Sport).
    "ge-sport":   "Groupe d'études Sport",
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


def _extract_pdf_text(pdf_bytes: bytes, max_chars: int = 200000) -> str:
    """Extrait le texte brut d'un PDF avec pypdf, tronque à max_chars.

    Si pypdf indisponible (dépendance non installée), renvoie "" sans
    planter — le matcher retombe alors sur le titre du CR seul.

    R39-E (2026-04-25) : nettoyage du préambule institutionnel des CR AN
    (« 1 7 e L É G I S L A T U R E A S S E M B L É E N A T I O N A L E
    Compte rendu Commission des affaires sociales – Examen… »). PyPDF
    extrait les titres de page avec espacement caractère par caractère
    (`1 7 e L É G I S L A T U R E`), bruit visible en début d'extrait
    sur le site (capture Cyril 2026-04-25). On collapse ces séquences
    en un seul mot puis on coupe avant le premier mot de contenu réel
    (« Examen », « Audition », « Mission », etc.).

    R40-G (2026-04-26) : limite passée de 10 000 à 200 000 caractères
    (×10). Avant : 5/9 CR test rataient le matching keyword côté veille
    Lidl pour cause de troncature trop précoce — les keywords cibles
    apparaissaient au-delà du 10 000ᵉ caractère sur les CR longs (60-180k
    chars). Symétrie inter-repos : signalé par Cyril 2026-04-26. Pour la
    veille sport, le risque équivalent existe sur la commission culture
    qui examine plusieurs sujets dans une même séance (audiovisuel +
    école + ESR + sport + JOP) : le bloc sport peut être en 4ᵉ position,
    au-delà du 10k. Trade-off : 200k = ~200 pages PDF stripées, couvre
    >95 % des CR sport-relevants ; coût mémoire ~200k × 32 CR/run = ~6 Mo
    de haystack en mémoire, ~6 Mo supplémentaires en SQLite (négligeable
    sur la DB ~100 Mo). Configurable par l'appelant si besoin de borner
    plus serré sur une commission spécifique.
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
    merged = _strip_an_pdf_preamble(merged)
    return merged[:max_chars]


# R39-E (2026-04-25) / R39-I (2026-04-25 fix) — pattern préambule
# institutionnel des CR AN PDF. PyPDF rend les titres de page
# « 1 7 e   L É G I S L A T U R E   A S S E M B L É E   N A T I O N A L E
# Compte rendu   Commission des affaires sociales – ». On coupe avant
# le premier verbe d'audition / examen / mission qui marque le début
# du contenu réel.
#
# R39-I : retrait de `re.IGNORECASE` — il neutralisait la classe négative
# `[^A-ZÀÂÄÉÈÊËÎÏÔÖÛÜÇ]` (avec IGNORECASE, la négation exclut aussi les
# minuscules, donc ` des affaires sociales` ne matchait plus). Sans
# IGNORECASE, on garde l'exclusion sur les majuscules uniquement, donc
# tout le segment ` des affaires sociales – ` (entre "Commission" et le
# verbe en majuscule "Examen") est bien capturé. On utilise désormais
# `.{1,400}?` pour plus de tolérance.
_AN_PREAMBLE_RE = re.compile(
    r"^\s*\d?\s*\d\s*e\s+L\s*[ÉE]\s*G\s*I\s*S\s*L\s*A\s*T\s*U\s*R\s*E\s+"
    r"A\s*S\s*S\s*E\s*M\s*B\s*L\s*[ÉE]\s*E\s+N\s*A\s*T\s*I\s*O\s*N\s*A\s*L\s*E\s+"
    r"Compte\s+rendu\s+Commission\b"
    r".{1,400}?"
    r"(?=\b(?:Examen|Audition|Mission|Communication|Table|Présidence|Réunion|"
    r"Constitution|Désignation|Discussion|Suite|Nomination|Approbation)\b)",
    re.DOTALL,
)


def _strip_an_pdf_preamble(text: str) -> str:
    """Retire l'entête institutionnelle des CR AN extraits via pypdf.

    Idempotent : si le pattern n'est pas détecté, retourne le texte
    inchangé.
    """
    if not text:
        return text
    m = _AN_PREAMBLE_RE.match(text)
    if m is not None:
        return text[m.end():].strip()
    return text


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
              commission_label: str) -> tuple[Item | None, bool]:
    """Tente de récupérer un CR (slug, session, num).

    Returns :
        Tuple `(item, has_body)` :
        - `item` : Item si la page HTML existe (200), None si 404
        - `has_body` : True si le PDF a été extrait avec un body
          significatif (≥ 200 chars). Sert à la boucle `fetch_source`
          pour décider si le num doit être marqué `scanned` ou laissé
          en attente d'un futur run.

    R41-G (2026-04-27) — port côté Sport du fix Lidl. Avant, la signature
    était `Item | None` et la boucle `fetch_source` marquait `scanned`
    dès qu'un Item était produit. Conséquence : si le HTML était 200
    mais le PDF 404 (cas typique : page mise en ligne quelques jours
    après l'audition, transcript publié 1-2 semaines plus tard),
    l'Item était ingéré avec `haystack_body=""` → matched_keywords=[]
    → exclu du site, mais `num` marqué scanned → JAMAIS re-essayé.
    Cas concret signalé : audition cion-eco N076 du 14/04/2026 (côté
    Lidl, équivalent attendu côté Sport sur des CR transverses).

    Le fix laisse la 1ère partie inchangée (HTML check + Item produit
    pour la trace), mais ajoute le flag `has_body` qui devient le
    critère pour marquer scanned.
    """
    base = (
        f"https://www.assemblee-nationale.fr/dyn/17/comptes-rendus/"
        f"{slug}/l17{slug}{session}{num:03d}_compte-rendu"
    )
    html_url = base
    pdf_url = base + ".pdf"

    # 1) Vérifier que la page HTML existe (200). 404 → CR pas publié
    #    du tout. On retourne (None, False) pour que la boucle
    #    décrémente miss_streak. Compat ascendante : `result is None`
    #    devient `result == (None, False)` → tester `it is None` après
    #    destructuring fait toujours sens.
    status, html_content = _fetch_silent(html_url)
    if status != 200 or not html_content:
        return (None, False)
    html_text = html_content.decode("utf-8", errors="replace")

    # 2) Récupérer le PDF (source du corps). Best-effort : si 404 ou erreur,
    #    on expose quand même un item avec haystack_body vide plutôt que de
    #    rien renvoyer (la page HTML existe, donc le CR est référencé).
    pdf_status, pdf_bytes = _fetch_silent(pdf_url, timeout=30.0)
    body = _extract_pdf_text(pdf_bytes) if pdf_status == 200 else ""

    # R41-G : `has_body` distingue un PDF "audition vidéo seulement"
    # (3 pages, ~1500 chars d'intro déjà ingestible) d'un PDF absent.
    # Seuil 200 chars retenu côté Lidl, porté tel quel côté Sport.
    has_body = pdf_status == 200 and len(body) >= 200

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

    item = Item(
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
    return (item, has_body)


def fetch_source(src: dict) -> list[Item]:
    """Scrape les CR de commissions AN par force brute incrémentale.

    Paramètres supportés dans src :
      - commissions     : dict {slug: label} (défaut : _DEFAULT_COMMISSIONS)
                          ou liste de slugs (labels = slug).
      - session         : code session (ex. "2526"). Défaut : déduit de
                          la date courante.
      - max_new_per_run : nb max de CR nouveaux scrapés par commission
                          par run (défaut 10).
      - miss_tolerance  : nb de 404 consécutifs avant d'arrêter une commission
                          (défaut 5).
      - max_num         : n° max absolu testé (garde-fou, défaut 99).

    R37-B (2026-04-24) — stratégie de scan inversée. On descend depuis
    `max_num` vers le bas et on s'arrête dès qu'on a attrapé `max_new`
    CR OU qu'on a enchaîné `miss_tolerance` 404 consécutifs DANS la zone
    déjà vue. Ça attrape les CR les plus récents en priorité et évite
    l'effet « scraper coincé au n°10 parce que le state n'est pas
    persisté et que max_new=10 limite le progrès ». Cyril (2026-04-24) :
    le CR 58 de cion-cedu manquait en prod parce que le scan ascendant
    repartait de 0 à chaque run et butait sur miss_tolerance=3 après
    quelques numéros absents.
    """
    raw_comm = src.get("commissions") or _DEFAULT_COMMISSIONS
    if isinstance(raw_comm, list):
        commissions = {s: s for s in raw_comm}
    else:
        commissions = dict(raw_comm)

    max_new = int(src.get("max_new_per_run", 10))
    miss_tolerance = int(src.get("miss_tolerance", 5))
    max_num = int(src.get("max_num", 99))
    session = str(src.get("session") or _session_code(datetime.utcnow()))

    state = _load_state()
    session_state = state.setdefault(session, {})
    items: list[Item] = []

    for slug, label in commissions.items():
        slug_state = session_state.setdefault(slug, {
            "last_num": 0,       # plus grand num jamais vu (historique)
            "scanned": [],       # nums déjà ingérés, pour skip rapide
        })
        scanned = set(slug_state.get("scanned") or [])
        num = max_num
        miss_streak = 0
        new_count = 0
        local_max = slug_state.get("last_num", 0)
        found_first = False
        # Scan descendant en DEUX PHASES (R38-J, 2026-04-24) :
        # - Phase 1 (avant le 1er hit de ce run) : on tolère TOUS les
        #   misses jusqu'à trouver le premier CR publié. Nécessaire
        #   parce que `max_num=99` ne correspond jamais au n° le plus
        #   haut réellement publié (ex. cion-cedu session 2526 plafonne
        #   à ~58). Sans cette phase, le scan s'arrêtait à 96 après 3
        #   misses et n'atteignait jamais le 58, faisant manquer tous
        #   les CR cion-cedu en prod.
        # - Phase 2 (après 1er hit) : `miss_tolerance` s'applique
        #   normalement pour stopper quand on sort de la zone active
        #   des CR publiés (les n° d'avant le début de session).
        # Les nums déjà dans `scanned` sont skippés sans consommer de
        # miss (ils ne sont pas 404, juste déjà ingérés).
        while num >= 1 and new_count < max_new:
            if num in scanned:
                num -= 1
                continue
            result = _fetch_cr(slug, session, num, label)
            # R41-G (2026-04-27) — destructure le tuple (item, has_body).
            # Compat ascendante : si quelqu'un wrappe _fetch_cr et
            # retourne encore l'ancien `Item | None`, on tolère.
            if isinstance(result, tuple):
                it, has_body = result
            else:
                # Forme legacy : Item | None (avant R41-G)
                it, has_body = result, (result is not None)
            if it is not None:
                items.append(it)
                # R41-G — ne marquer scanned QUE si le PDF a été
                # effectivement extrait (≥ 200 chars de body). Sinon
                # l'item est ingéré pour la trace mais le num reste
                # « pending » : il sera ré-essayé au prochain run, ce
                # qui permet de capter le contenu PDF dès qu'il est
                # publié (parfois 1-2 semaines après la page HTML).
                if has_body:
                    scanned.add(num)
                if num > local_max:
                    local_max = num
                new_count += 1
                miss_streak = 0
                found_first = True
            else:
                miss_streak += 1
                # Phase 2 : stop si on a déjà trouvé un CR et qu'on
                # enchaîne trop de misses.
                if found_first and miss_streak >= miss_tolerance:
                    break
                # Phase 1 : pas de stop. On continue à descendre.
            num -= 1
        slug_state["last_num"] = local_max
        # On borne la liste scanned pour éviter une croissance indéfinie
        # dans le JSON d'état : on garde les 200 derniers (largement plus
        # que le nombre de réunions par commission par session, 50-80).
        slug_state["scanned"] = sorted(scanned)[-200:]
        session_state[slug] = slug_state
        log.info(
            "an_cr_commissions %s session=%s : +%d items "
            "(last_num=%d, scanned=%d, phase1_scan=%s)",
            slug, session, new_count, local_max, len(scanned),
            "depassée" if found_first else "inaboutie",
        )

    _save_state(state)
    return items
