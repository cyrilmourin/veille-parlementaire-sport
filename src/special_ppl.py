"""R41-M (2026-05-07) — Module dédié de la PPL sport professionnel.

Demande Cyril : un module temporaire spécifique à la « Proposition de loi
relative à l'organisation, à la gestion et au financement du sport
professionnel » (PPL Sénat 24-456 → AN n° 1560), avec :

1. Carte sur la page d'accueil (à droite du module 24 h)
2. Page dédiée `/ppl-sport-professionnel/` :
   - Lien vers le texte AN à date
   - Étapes de la procédure (timeline)
   - Amendements en commission (2 colonnes)
   - Amendements en séance (2 colonnes)
3. Sidebar « 5 derniers amendements PPL sport pro » sur :
   - accueil
   - /items/dossiers_legislatifs/
   - /items/amendements/

Module orienté DATA → tout est exposé via `site/data/special_ppl.json`,
Hugo s'occupe du rendu via les layouts/partials.
"""
from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime
from pathlib import Path

# R41-P (2026-05-08) : préfixe alphabétique du n° d'amendement = signe
# fiable d'un amdt de commission. Exemples observés :
#   - « Amdt n°AC118 ... » → AC = commission affaires culturelles
#   - « Amdt n°CL77 ... »   → CL = commission lois
#   - « Amdt n°118 ... »    → numéro pur = séance plénière
_AMDT_NUM_PREFIX_RE = re.compile(
    r"Amdt\s+n[°o]\s*([A-Z]{1,3})?(\d+)", re.IGNORECASE
)

# R41-Q (2026-05-08) : extraction de l'article depuis le titre
# (« Amdt n°AC118 · art. ARTICLE 5 · sur... » → « ARTICLE 5 »).
_AMDT_ARTICLE_RE = re.compile(
    r"art\.\s+([^·]+?)\s*(?:·|$)", re.IGNORECASE
)
# R41-Q : nettoyage du `summary` qui contient un préfixe « Dossier : ... »
# (le titre du dossier parent répété, redondant car identique sur tous les
# amdt PPL) puis les blocs « — Auteur : ... — Statut : ... — Article : ... »
# en fin (déjà affichés via les champs structurés du payload).
_DOSSIER_PREFIX_RE = re.compile(r"^Dossier\s*:\s*[^—]+—\s*", re.IGNORECASE)
_METADATA_TAIL_RE = re.compile(
    r"\s*—\s*(?:Auteur|Statut|Article|Sort|État)\s*:.*$",
    re.IGNORECASE | re.DOTALL,
)
# Strip des balises HTML — `corps.contenuAuteur.dispositif/exposeSommaire`
# AN sont typés XHTML donc remontent avec <p>, <i>, &nbsp;…
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Compactage des espaces multiples (incluant \n, \t, &nbsp; déjà décodé).
_WS_RE = re.compile(r"\s+")

# R41-R (2026-05-09) : URL Sénat « dossier législatif » canonique au
# format `(ppl|pjl)<session>-<numéro>.html`. Tout autre format est jugé
# malformé (ex. CSV historique `dossiers-legislatifs.csv` qui expose
# parfois des identifiants internes type `s92930456` qui renvoient vers
# un texte sans rapport — vérifié 2026-05-09 sur la PPL sport pro :
# `s92930456` → page « Épargne »).
_SENAT_URL_CANONIQUE_RE = re.compile(
    r"/dossier-legislatif/(?:ppl|pjl)\d{2}-\d{2,4}",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Constantes — identifiants stables de la PPL sport pro
# ---------------------------------------------------------------------------

PPL_KEY = "ppl-sport-professionnel"
PPL_TITLE = "Spécial PPL Sport professionnel"
PPL_SLUG_PATH = "/ppl-sport-professionnel/"

# Identifiants techniques des textes (AN n° 1560 + Sénat S459 B0456 / BTC0670 / BTA0137)
AN_TEXTE_REF = "PIONANR5L17B1560"
SENAT_TEXTE_REFS = frozenset({
    "PIONSNR5S459B0456",
    "PIONSNR5S459BTC0670",
    "PIONSNR5S459BTA0137",
})
ALL_TEXTE_REFS = frozenset({AN_TEXTE_REF} | set(SENAT_TEXTE_REFS))
AN_TEXTE_NUM = "1560"

# Mots significatifs du titre — utilisés pour matcher les items qui n'ont
# pas de texte_ref (ex. CR séance, communiqués). Tous doivent être présents
# dans le titre normalisé pour activer le match « par titre ».
TITLE_REQUIRED_WORDS = frozenset({
    "organisation", "gestion", "financement",
    "sport", "professionnel",
})

# URLs canoniques du texte (pour la carte accueil et la page dédiée)
URL_AN_TEXTE = (
    "https://www.assemblee-nationale.fr/dyn/17/textes/"
    "l17b1560_proposition-loi"
)
# R41-X (2026-05-09) : URL « raw » de la PPL pour extraction des
# articles. C'est l'iframe HTML server-rendered contenant le texte
# complet (cf. assnat9ArticleNum / assnatLoiTexte). Plus stable et
# plus rapide à parser que la page wrapper qui charge l'iframe via JS.
URL_AN_TEXTE_RAW = (
    "https://www.assemblee-nationale.fr/dyn/docs/PIONANR5L17B1560.raw"
)

# R41-T (2026-05-09) : URL dossier législatif AN. Format slug stable
# `<sujet-mots-cles>` derrière `/dyn/17/dossiers/`. Vérifié 200 OK le
# 2026-05-09. C'est la page index officielle du dossier sur le site AN.
# R41-AB (2026-05-09) : URL canonique par dossier_id `DLR5L17N51732`
# (vérifiée 200 OK le 2026-05-09). L'ancien slug deviné
# `sport-professionnel-organisation-gestion-financement` rendait 404.
URL_AN_DOSSIER = (
    "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N51732"
)
URL_SENAT_DOSSIER = (
    "https://www.senat.fr/dossier-legislatif/ppl24-456.html"
)

# R41-W (2026-05-09) — Lien vers la page AN de listing des amendements
# pour la PPL n° 1560 (commission CCE = PO419604, examen EXANR5L17PO419604B1560P0D1).
# Permet à l'utilisateur de cliquer « Créer une liasse » sur l'AN et
# télécharger le PDF complet à la demande. Plus stable que le lien PDF
# direct (qui contient un hash + une date et expire — vérifié 2026-05-09 :
# le PDF généré pointe vers `/dyn/17/amendements/liasse/<date>/<hash>.pdf`,
# cache temporaire AN). Trier par ordre_passage,asc côté AN pour matcher
# notre tri par article.
URL_AMDT_LISTE_AN = (
    "https://www.assemblee-nationale.fr/dyn/17/amendements?"
    "dossier_legislatif=DLR5L17N51732&"
    "examen=EXANR5L17PO419604B1560P0D1&"
    "order=ordre_passage,asc&"
    "page=1"
)

# R41-T (2026-05-09) : 4 rapporteurs nommés sur la PPL n° 1560
# (commission affaires culturelles AN, examen 12-13 mai 2026).
# Triés par ORDRE ALPHABÉTIQUE sur le NOM (demande Cyril). Photos
# AN format `/static/tribun/17/photos/carre/<digits>.jpg`.
RAPPORTEURS = [
    {
        "prenom": "Belkhir",
        "nom": "Belhaddad",
        "groupe": "SOC",
        "fiche_url": "https://www.assemblee-nationale.fr/dyn/deputes/PA720362",
        "photo_url": "https://www2.assemblee-nationale.fr/static/tribun/17/photos/carre/720362.jpg",
        "pa_id": "PA720362",
    },
    {
        # R41-AB : PA870009 = Lionel Duparay (DR), pas Royer-Perreault
        # (l'agent d'identification avait extrapolé un nom). Vérifié
        # 2026-05-09 via cache AMO refresh : civ=M. prenom=Lionel
        # nom=Duparay (groupe DR — Droite Républicaine).
        "prenom": "Lionel",
        "nom": "Duparay",
        "groupe": "DR",
        "fiche_url": "https://www.assemblee-nationale.fr/dyn/deputes/PA870009",
        "photo_url": "https://www2.assemblee-nationale.fr/static/tribun/17/photos/carre/870009.jpg",
        "pa_id": "PA870009",
    },
    {
        "prenom": "Sophie",
        "nom": "Mette",
        "groupe": "DEM",
        "fiche_url": "https://www.assemblee-nationale.fr/dyn/deputes/PA719640",
        "photo_url": "https://www2.assemblee-nationale.fr/static/tribun/17/photos/carre/719640.jpg",
        "pa_id": "PA719640",
    },
    {
        "prenom": "Véronique",
        "nom": "Riotton",
        "groupe": "RE",
        "fiche_url": "https://www.assemblee-nationale.fr/dyn/deputes/PA721426",
        "photo_url": "https://www2.assemblee-nationale.fr/static/tribun/17/photos/carre/721426.jpg",
        "pa_id": "PA721426",
    },
]
# Re-tri sécurité (au cas où l'ordre source serait modifié)
RAPPORTEURS = sorted(RAPPORTEURS, key=lambda x: x["nom"].upper())


# ---------------------------------------------------------------------------
# Détection : un row est-il lié à la PPL sport pro ?
# ---------------------------------------------------------------------------


def _norm_words(title: str) -> set[str]:
    """Mots normalisés (sans accent, lower, ≥3 chars)."""
    if not title:
        return set()
    try:
        from unidecode import unidecode as _uni
        s = _uni(title).lower()
    except Exception:
        s = title.lower()
    import re
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return {w for w in s.split() if len(w) >= 3}


def row_matches_special_ppl(row: dict) -> bool:
    """True si le row est lié à la PPL sport pro.

    Critères (OR) :
    - `raw.texte_ref` ∈ ALL_TEXTE_REFS
    - `raw.dossier_id` ∈ ALL_TEXTE_REFS
    - URL contient « 1560 » ET « proposition-loi » (AN textes)
    - URL contient « ppl24-456 » (Sénat)
    - Titre contient TOUS les mots de TITLE_REQUIRED_WORDS
    """
    raw = row.get("raw") or {}
    if isinstance(raw, dict):
        for k in ("texte_ref", "texteRef", "dossier_id", "signet"):
            v = raw.get(k)
            if isinstance(v, str) and v in ALL_TEXTE_REFS:
                return True
    url = (row.get("url") or "").lower()
    if "ppl24-456" in url:
        return True
    if "l17b1560" in url:
        return True
    title = row.get("title") or ""
    if "(n° 1560)" in title or "(no 1560)" in title.lower():
        return True
    words = _norm_words(title)
    if TITLE_REQUIRED_WORDS <= words:
        return True
    return False


# ---------------------------------------------------------------------------
# Collecte + tri par bucket
# ---------------------------------------------------------------------------


def collect_special_ppl(rows: list[dict]) -> dict:
    """Filtre et range les rows liés à la PPL en buckets exploitables.

    Retourne :
      {
        "dosleg": [...],
        "agenda": [...],
        "amdt_commission": [...],   # category=amendements, stage=commission
        "amdt_seance": [...],       # category=amendements, stage≠commission
        "comptes_rendus": [...],
        "communiques": [...],
        "questions": [...],
      }
    Chaque bucket est trié par date desc.
    """
    out: dict[str, list[dict]] = {
        "dosleg": [], "agenda": [],
        "amdt_commission": [], "amdt_seance": [],
        "comptes_rendus": [], "communiques": [], "questions": [],
    }
    for r in rows:
        if not row_matches_special_ppl(r):
            continue
        cat = (r.get("category") or "").strip()
        if cat == "dossiers_legislatifs":
            out["dosleg"].append(r)
        elif cat == "agenda":
            out["agenda"].append(r)
        elif cat == "amendements":
            # R41-P (2026-05-08) : distinction commission / séance fiable
            # via le préfixe alphabétique du n° d'amendement dans le titre
            # (« AC118 » → commission, « 118 » → séance). L'URL AN ne
            # contient pas « cion-* » dans le format actuel — l'organe est
            # codé en PO<id> ce qui n'est pas portable. Le titre reste le
            # signal le plus stable et lisible.
            title = r.get("title") or ""
            m = _AMDT_NUM_PREFIX_RE.search(title)
            has_letter_prefix = bool(m and m.group(1))
            raw = r.get("raw") or {}
            stage_hint = ""
            if isinstance(raw, dict):
                stage_hint = (raw.get("stage") or "").lower()
            if has_letter_prefix or "commission" in stage_hint:
                out["amdt_commission"].append(r)
            else:
                out["amdt_seance"].append(r)
        elif cat == "comptes_rendus":
            out["comptes_rendus"].append(r)
        elif cat == "communiques":
            out["communiques"].append(r)
        elif cat == "questions":
            out["questions"].append(r)
    # Tri date desc dans chaque bucket
    def _date_of(r):
        return r.get("published_at") or ""
    for k in out:
        out[k].sort(key=_date_of, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Sérialisation pour Hugo (data/special_ppl.json)
# ---------------------------------------------------------------------------


def _build_extract(row: dict, raw: dict, max_chars: int = 400) -> str:
    """R41-P (2026-05-08) — Extrait du corps de l'amendement (≤ 400 chars).

    Source : `raw.haystack_body` (corps complet déposé par le parser AN
    en R26) si présent, sinon `summary`.

    R41-Q (2026-05-08) — Nettoyages successifs :
      1. Strip du préfixe « Dossier : <titre dossier> — » (titre du
         dossier parent, identique sur tous les amdt PPL → redondant)
      2. Strip de la queue méta « — Auteur : ... — Statut : ... —
         Article : ... » (déjà affichée via les champs structurés)
      3. Décodage des entités HTML (&nbsp;, &amp;…) puis suppression
         des balises XHTML (<p>, <i>…) issues du dispositif/exposé AN
      4. Strip du titre s'il préfixe le body (cas legacy)
      5. Compactage des espaces et troncature à 400 chars
    """
    extract = ""
    if isinstance(raw, dict):
        extract = (raw.get("haystack_body") or "").strip()
    if not extract:
        extract = (row.get("summary") or "").strip()

    # 1. Strip "Dossier : ... — " en tête (titre dosleg parent répétitif)
    extract = _DOSSIER_PREFIX_RE.sub("", extract)
    # 2. Strip queue métadonnées (Auteur / Statut / Article / Sort / État)
    extract = _METADATA_TAIL_RE.sub("", extract)
    # 3. Décodage entités + suppression balises XHTML
    extract = _html.unescape(extract)
    extract = _HTML_TAG_RE.sub(" ", extract)
    # 4. Strip titre en préfixe (cas legacy — la R41-Q masque déjà via 1)
    title = (row.get("title") or "").strip()
    if title and extract.startswith(title):
        extract = extract[len(title):]
    extract = extract.lstrip(" :—-·\n\t")
    # 5. Compactage espaces + troncature
    extract = _WS_RE.sub(" ", extract).strip()
    if len(extract) > max_chars:
        extract = extract[:max_chars].rstrip() + "…"
    return extract


def _extract_article_label(title: str) -> str:
    """R41-Q (2026-05-08) — Extrait le libellé d'article depuis le titre
    de l'amdt (« Amdt n°AC118 · art. ARTICLE 5 · sur... » → « ARTICLE 5 »).
    Retourne "" si pas trouvé (cas non-amdt ou titre incomplet)."""
    if not title:
        return ""
    m = _AMDT_ARTICLE_RE.search(title)
    if not m:
        return ""
    return m.group(1).strip()


def _article_sort_key(label: str) -> tuple:
    """Clé de tri pour ordonner les groupes d'articles dans l'ordre de
    passage AN.

    R41-W (2026-05-09) — Refonte multi-critères pour respecter l'ordre
    réel d'examen :
      ARTICLE 1ER  →  APRÈS ART. 1ER  →  ARTICLE 1ER A  →
      APRÈS 1ER A  →  ARTICLE 1ER AA  →  ARTICLE 1ER B  →
      APRÈS 1ER B  →  ARTICLE 1ER C  →  APRÈS 1ER C  →
      ARTICLE 2  →  APRÈS ART. 2  →  ARTICLE 2 BIS  →
      APRÈS ART. 2 BIS  →  ARTICLE 3 ...

    Stratégie : tuple (num, sub_letter, suffix_bis, position).
      - num         : numéro d'article (1, 2, 3…)
      - sub_letter  : lettre de sous-article (A, AA, B, C…) ou "" pour
                      l'article principal (qui passe avant les sous-arts)
      - suffix_bis  : 0 = base, 1 = bis, 2 = ter, 3 = quater, 4 = quinquies
      - position    : 0 = avant, 1 = sur l'article, 2 = après
    """
    if not label:
        return (99999, "", 0, 9, "")
    up = label.upper()
    if "SANS ARTICLE" in up or label == "Sans article":
        return (99999, "", 0, 9, label)

    # Numéro principal (1, 2, 3…)
    m = re.search(r"(\d+)", label)
    num = int(m.group(1)) if m else 9999

    # Suffixe latin (BIS/TER/QUATER/QUINQUIES) — détecté avant la
    # sub-letter pour ne pas le confondre.
    suffix_weight = 0
    if "QUINQUIES" in up:
        suffix_weight = 4
    elif "QUATER" in up:
        suffix_weight = 3
    elif "TER" in up:
        suffix_weight = 2
    elif "BIS" in up:
        suffix_weight = 1

    # Sub-letter après le numéro (ex. « 1ER A » → A, « 1ER AA » → AA,
    # « 2 C » → C). Filtrer si c'est en réalité un suffixe latin.
    sub_letter = ""
    sub_m = re.search(
        r"\d+\s*(?:ER|ÈRE)?\s+([A-Z]{1,3})\b", label, re.IGNORECASE
    )
    if sub_m:
        candidate = sub_m.group(1).upper()
        if candidate not in ("BIS", "TER", "QUATER", "QUINQUIES"):
            sub_letter = candidate

    # Position avant / sur / après l'article
    if "AVANT" in up:
        position = 0
    elif "APRÈS" in up or "APRES" in up:
        position = 2
    else:
        position = 1

    return (num, sub_letter, suffix_weight, position, label)


def _group_amdt_by_article(amdt_payload: list[dict]) -> list[dict]:
    """R41-Q (2026-05-08) — Groupe une liste d'amdt (déjà rendus par
    `_row_to_payload`) par article, triés.

    Retourne `[{"article": "ARTICLE 1ER", "items": [...]}, ...]`. Items
    de chaque groupe triés par date desc.
    """
    groups: dict[str, list[dict]] = {}
    for it in amdt_payload:
        art = it.get("article") or "Sans article"
        groups.setdefault(art, []).append(it)
    result = []
    for art, items in groups.items():
        items.sort(key=lambda x: x.get("date") or "", reverse=True)
        result.append({"article": art, "items": items})
    result.sort(key=lambda g: _article_sort_key(g["article"]))
    return result


def _detect_meeting_kind(row: dict) -> str:
    """R41-T (2026-05-09) — Pour un item agenda, détermine si la réunion
    est une « Séance publique » ou une « Commission ».

    R42-H (2026-05-10) : terminologie alignée — on dit « séance publique »
    (terme officiel AN/Sénat) et non « plénière ».

    Stratégie en 3 niveaux :
      1. URL d'organe d'origine : `PO838901` = AN séance publique,
         `PO4xxxxx` ou `PO7xxxxx` = commission AN. Sénat équivalent
         (organe 100 = séance, autres = commissions).
      2. Heuristique titre (« Discussion »/« Suite de la discussion »
         → plénière ; « Examen »/« Désignation »/« Audition »/« Table
         ronde » → commission).
      3. Fallback : "" (pas de préfixe).

    Doit être appelé AVANT `_safe_url` (qui réécrit l'URL d'organe en
    `/items/agenda/`) sinon on perd l'info.
    """
    if (row.get("category") or "") != "agenda":
        return ""
    url = (row.get("url") or "").lower()
    title_low = (row.get("title") or "").lower()
    # 1. Codes d'organe
    if "/organes/po838901" in url:
        return "Séance publique"
    if re.search(r"/organes/po[47]\d{5}", url):
        return "Commission"
    # 2. Heuristique titre
    if any(w in title_low for w in (
        "suite de la discussion",
        "discussion de la proposition",
        "discussion en séance",
        "séance publique",
    )):
        return "Séance publique"
    if any(w in title_low for w in (
        "examen de la proposition", "examen du projet",
        "désignation du rapporteur", "audition", "table ronde",
        "examen du texte", "examen pour avis",
    )):
        return "Commission"
    return ""


def _safe_url(row: dict, raw: dict) -> str:
    """R41-P (2026-05-08) — Retourne l'URL du row, en remplaçant les URLs
    AN d'organe `/dyn/17/organes/POXXXX` (qui mènent vers la fiche
    générique de la commission, pas vers la réunion datée) par un lien
    interne `/items/agenda/`.

    R41-R (2026-05-09) — Pour les dossiers législatifs Sénat dont l'URL
    sort du format canonique `(ppl|pjl)SS-NNN.html` (ex. CSV historique
    qui expose `s92930456` renvoyant vers un texte sans rapport), on
    bascule vers `URL_SENAT_DOSSIER` — l'URL canonique connue du module.
    Le module est dédié à UNE PPL ; tous les rows liés sont garantis
    pointer vers le même dossier, donc la substitution est sûre.
    """
    url = (row.get("url") or "").strip()
    cat = (row.get("category") or "").strip()
    if cat == "agenda":
        if "/dyn/17/organes/PO" in url or "/organes/PO" in url:
            return "/items/agenda/"
        return url
    if cat == "dossiers_legislatifs":
        chamber = (row.get("chamber") or "").strip().lower()
        if chamber in ("senat", "sénat"):
            # URL Sénat doit matcher le pattern canonique ; sinon on
            # bascule sur l'URL connue de la PPL.
            if not _SENAT_URL_CANONIQUE_RE.search(url):
                return URL_SENAT_DOSSIER
        elif chamber == "an":
            # Pour AN, le boost R41-K reroute déjà vers /dyn/17/textes/
            # l17bNNNN_<type>, qu'on garde tel quel.
            pass
    return url


def _row_to_payload(r: dict, max_title: int = 220) -> dict:
    """Réduit un row à ses champs utiles pour le rendu Hugo."""
    raw = r.get("raw") or {}
    if not isinstance(raw, dict):
        raw = {}
    # R41-W (2026-05-09) : cascade sort > sous_etat > etat > statut.
    # Le `sort` officiel AN n'est posé qu'après le vote (Adopté, Rejeté,
    # Irrecevable, Retiré, Retiré avant publication, Tombé, Non soutenu,
    # Non examiné, Article 40…). Avant le vote, l'item AN expose un
    # « statut » procédural (« En traitement », « À discuter »…). On
    # remonte ce statut comme sort visible — par défaut « En traitement »
    # qui se substitue à l'ancien « Inconnu ».
    sort_value = (
        raw.get("sort") or raw.get("sous_etat")
        or raw.get("etat") or raw.get("statut") or ""
    ).strip()
    if not sort_value and (r.get("category") or "") == "amendements":
        sort_value = "En traitement"
    return {
        "title": (r.get("title") or "")[:max_title],
        # R41-T : meeting_kind calculé AVANT _safe_url qui réécrit l'URL
        # d'organe en /items/agenda/ — on a besoin de l'URL d'origine pour
        # la détection.
        "meeting_kind": _detect_meeting_kind(r),
        "url": _safe_url(r, raw),
        "chamber": r.get("chamber") or "",
        "date": (r.get("published_at") or "")[:10],
        "source_id": r.get("source_id") or "",
        "auteur": raw.get("auteur") or "",
        "groupe": raw.get("groupe") or "",
        "status_label": raw.get("status_label") or raw.get("status") or "",
        # R41-P/W : sort résultat-vote ou statut procédural (cf. cascade
        # ci-dessus). Exposé pour le filtre UI sur la page dédiée.
        "sort": sort_value,
        "stage": raw.get("stage") or "",
        "step": raw.get("step") or "",
        # R41-P : extrait du corps (max 400 chars), sans le titre.
        "extract": _build_extract(r, raw),
        # R41-Q : article ciblé par l'amdt (« ARTICLE 5 », « ARTICLE 1ER A »,
        # « ARTICLE 2 BIS »...). Vide pour les autres types d'items.
        "article": _extract_article_label(r.get("title") or ""),
    }


_WC_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ\-]{4,}", re.UNICODE)
# Stopwords FR + bruits procéduraux. Tous accentués comme l'écrit l'AN.
_WC_STOPWORDS: frozenset[str] = frozenset({
    # Mots vides FR classiques
    "alors", "ainsi", "aprés", "après", "aucun", "aucune", "aussi", "autre",
    "autres", "avant", "avec", "avoir", "cela", "cela", "celle", "celles",
    "celui", "cent", "cette", "ceux", "chaque", "comme", "comment", "dans",
    "depuis", "deux", "dont", "donc", "elle", "elles", "encore", "entre",
    "etre", "être", "eux", "fait", "faire", "fois", "hors", "ici", "ils",
    "jusqu", "leur", "leurs", "lors", "lorsque", "mais", "mêmes", "même",
    "mois", "moins", "nous", "notre", "notamment", "outre", "parce", "pour",
    "pourquoi", "plus", "près", "puis", "quand", "quel", "quelle", "quels",
    "quelles", "rien", "sans", "sera", "ses", "seul", "seule", "seuls",
    "seules", "sous", "soit", "sont", "sous", "sur", "tant", "tels",
    "telles", "tous", "toute", "toutes", "très", "trop", "vers", "vous",
    "votre", "vos", "celle-ci", "celui-ci", "qu'il", "qu'elle", "qu'ils",
    "afin", "doit", "doivent", "peut", "peuvent", "ainsi", "cas", "été",
    "été", "telle", "tel", "etc",
    # Articles & prépositions multi-formes
    "des", "les", "une", "aux", "que", "qui", "ces", "cet",
    # Verbes auxiliaires fréquents
    "avait", "avaient", "aura", "auront", "avait", "aurait", "auraient",
    "était", "étaient", "sera", "seront", "serait", "seraient", "est",
    # Bruits procéduraux (déjà connus du contexte amdt)
    "amendement", "amendements", "article", "articles", "alinéa", "alinéas",
    "loi", "lois", "code", "projet", "proposition", "présent", "présente",
    "présents", "présentes", "rédaction", "rédigé", "rédigée", "rédigés",
    "rédigées", "supprimer", "remplacer", "ajouter", "insérer", "modifier",
    "complété", "complétée", "complétés", "complétées", "fin", "phrase",
    "premier", "deuxième", "troisième", "quatrième", "cinquième",
    "président", "présidente", "rapporteur", "rapporteurs", "rapporteure",
    "ministre", "ministère", "ministériel", "ministérielle",
    "national", "nationale", "nationaux", "nationales",
    "fédération", "fédérations", "fédérale", "fédéral", "fédéraux",
    # Trop génériques pour la PPL Sport pro (sinon dominent toutes les fréquences)
    "sport", "sports", "sportif", "sportifs", "sportive", "sportives",
    "professionnel", "professionnelle", "professionnels", "professionnelles",
    "société", "sociétés",
    # Connecteurs / divers
    "ainsi", "lieu", "autre", "autres", "ainsi", "même", "mêmes",
    "compte", "comptes", "raison", "raisons", "ensemble",
    "exemple", "permet", "permettre", "viser", "vise", "visant", "visent",
    "matière", "place", "mise", "mises", "mis", "rend", "rendre",
    "indique", "indiquer", "supprime", "supprimer",
    "selon", "doivent", "concerne", "concernant", "concerné", "concernée",
    "concernés", "concernées",
    "afin", "objet", "objets", "objectif", "objectifs",
    "fait", "faite", "faits", "faites",
    "tout", "toute", "tous", "toutes",
})


def _build_wordcloud(amdt_rows_payload: list[dict],
                     top_n: int = 40) -> list[dict]:
    """R42-BX (2026-05-14) — Nuage thématique des amdt commission.

    Tokenize les `extract` (issus de _build_extract) en mots ≥4 chars,
    filtre par stopwords FR (+ bruits procéduraux + mots trop génériques
    type 'sport', 'amendement', 'article'), compte les fréquences,
    retourne les `top_n` les plus fréquents avec une classe de taille
    (xl/lg/md/sm/xs) calculée par quintile.

    Format de sortie consommé côté Hugo :
      [{"word": "agrément", "count": 87, "size": "xl"}, ...]
    """
    if not amdt_rows_payload:
        return []
    counts: dict[str, int] = {}
    for r in amdt_rows_payload:
        text = (r.get("extract") or "")
        if not text:
            continue
        for tok in _WC_TOKEN_RE.findall(text):
            low = tok.lower()
            if low in _WC_STOPWORDS:
                continue
            counts[low] = counts.get(low, 0) + 1
    if not counts:
        return []
    # Top N par fréquence décroissante (tie-break alphabétique)
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    # 5 tailles par quintile sur la fréquence
    freqs = [c for _, c in items]
    if not freqs:
        return []
    f_max = max(freqs)
    f_min = min(freqs)
    span = max(f_max - f_min, 1)
    def _size(c: int) -> str:
        # Position 0..1 dans la distribution
        pos = (c - f_min) / span
        if pos >= 0.80: return "xl"
        if pos >= 0.55: return "lg"
        if pos >= 0.30: return "md"
        if pos >= 0.10: return "sm"
        return "xs"
    return [{"word": w, "count": c, "size": _size(c)} for w, c in items]


def _build_sort_stats(amdt_rows_payload: list[dict]) -> dict:
    """R42-BX (2026-05-14) — Agrège les sorts des amdt commission.

    Retourne `{"total": N, "buckets": [{"label", "count", "pct", "class"}]}`
    avec les buckets canoniques (Adopté / Rejeté / Retiré / Tombé /
    En traitement / Autre). Le `class` est utilisé côté CSS pour le
    code couleur cohérent (sort--adopte, sort--rejete, ...).
    """
    if not amdt_rows_payload:
        return {"total": 0, "buckets": []}
    # Mapping sort label brut → bucket canonique
    def _bucket_of(sort_raw: str) -> tuple[str, str]:
        s = (sort_raw or "").strip().lower()
        if not s or s == "en traitement":
            return ("En traitement", "traitement")
        if "adopt" in s:
            return ("Adopté", "adopte")
        if "rejet" in s or "irrecev" in s:
            return ("Rejeté", "rejete")
        if "retir" in s:
            return ("Retiré", "retire")
        if "tomb" in s or "non examin" in s or "non soutenu" in s or "article 40" in s:
            return ("Tombé", "tombe")
        return ("Autre", "autre")
    counter: dict[str, dict] = {}
    for r in amdt_rows_payload:
        label, cls = _bucket_of(r.get("sort"))
        if label not in counter:
            counter[label] = {"label": label, "class": cls, "count": 0}
        counter[label]["count"] += 1
    total = sum(b["count"] for b in counter.values())
    # Ordre stable : adopté > rejeté > retiré > tombé > en traitement > autre
    order = ["Adopté", "Rejeté", "Retiré", "Tombé", "En traitement", "Autre"]
    buckets = []
    for lbl in order:
        if lbl in counter:
            b = counter[lbl]
            b["pct"] = round(100 * b["count"] / total) if total else 0
            buckets.append(b)
    return {"total": total, "buckets": buckets}


def _build_groupe_stats(amdt_rows_payload: list[dict],
                        top_n: int = 8) -> list[dict]:
    """R42-BX (2026-05-14) — Top N groupes politiques (dépôts amdt).

    Retourne `[{"groupe", "count", "pct_max"}]` trié par count desc.
    `pct_max` = % par rapport au top groupe (pour bar charts).
    """
    if not amdt_rows_payload:
        return []
    counts: dict[str, int] = {}
    for r in amdt_rows_payload:
        g = (r.get("groupe") or "").strip()
        if not g:
            continue
        counts[g] = counts.get(g, 0) + 1
    if not counts:
        return []
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    top_count = items[0][1] if items else 1
    return [
        {"groupe": g, "count": c, "pct_max": round(100 * c / top_count)}
        for g, c in items
    ]


def build_payload(buckets: dict) -> dict:
    """Construit le payload JSON exposé via site/data/special_ppl.json."""
    # Limit raisonnable par bucket (page peut afficher tous, sidebar ne
    # prend que les 5 premiers — Hugo gère le slice).
    LIMITS = {
        "dosleg": 5,
        "agenda": 30,
        "amdt_commission": 200,
        "amdt_seance": 200,
        "comptes_rendus": 30,
        "communiques": 30,
        "questions": 30,
    }
    payload = {
        "meta": {
            "key": PPL_KEY,
            "title": PPL_TITLE,
            "slug_path": PPL_SLUG_PATH,
            "url_an_texte": URL_AN_TEXTE,
            "url_an_dossier": URL_AN_DOSSIER,
            "url_senat_dossier": URL_SENAT_DOSSIER,
            "url_amdt_liste_an": URL_AMDT_LISTE_AN,
            # R41-T : 4 rapporteurs nommés sur la PPL — exposés au layout
            # Hugo pour le module « Rapporteurs » à droite des étapes.
            "rapporteurs": list(RAPPORTEURS),
            "an_num": AN_TEXTE_NUM,
            "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        },
    }
    for bucket, items in buckets.items():
        limit = LIMITS.get(bucket, 50)
        payload[bucket] = [_row_to_payload(r) for r in items[:limit]]
    # Compteurs absolus (avant slice) pour les totaux UI
    payload["counts"] = {k: len(v) for k, v in buckets.items()}
    # R41-Q (2026-05-08) : versions groupées par article pour la page
    # dédiée. Hugo itère sur ces structures pour rendre un sub-heading
    # « Article X (n) » au-dessus de chaque grille de cards.
    payload["amdt_commission_by_article"] = _group_amdt_by_article(
        payload["amdt_commission"]
    )
    payload["amdt_seance_by_article"] = _group_amdt_by_article(
        payload["amdt_seance"]
    )
    # R42-BX (2026-05-14) — Agrégats pour la page mockup v3 :
    # - nuage de mots-clés (40 plus fréquents des extracts, 5 tailles)
    # - stats de sort (donut)
    # - top groupes politiques (barres horizontales)
    payload["wordcloud_commission"] = _build_wordcloud(
        payload["amdt_commission"]
    )
    payload["sort_stats_commission"] = _build_sort_stats(
        payload["amdt_commission"]
    )
    payload["groupe_stats_commission"] = _build_groupe_stats(
        payload["amdt_commission"]
    )
    return payload


def write_data_file(site_data_dir: Path, payload: dict) -> None:
    """Écrit `site/data/special_ppl.json`."""
    site_data_dir.mkdir(parents=True, exist_ok=True)
    (site_data_dir / "special_ppl.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_page_stub(content_dir: Path) -> None:
    """Écrit `site/content/ppl-sport-professionnel.md` (page racine).

    Le rendu se fait dans le layout `layouts/page/ppl-sport-pro.html`
    qui lit `site.Data.special_ppl`. La page est non-listée dans le
    menu (demande Cyril) mais accessible via la carte accueil + sidebar.

    On écrit une page racine (pas `_index.md` dans un dossier) pour que
    Hugo utilise `layouts/page/single.html` → `layouts/page/ppl-sport-pro.html`
    (chaîne de fallback type → layout).
    """
    content_dir.mkdir(parents=True, exist_ok=True)
    fm = [
        "---",
        f'title: "{PPL_TITLE}"',
        f"date: {datetime.now():%Y-%m-%d}",
        'description: "Suivi de la proposition de loi relative à '
        "l'organisation, à la gestion et au financement du sport "
        'professionnel (n° 1560)."',
        # R41-N : pas de fullwidth → la sidebar (Sideline / Recherche /
        # Agenda + bloc PPL) s'affiche sur la page dédiée comme partout.
        "type: page",
        "layout: ppl-sport-pro",
        f'url: "{PPL_SLUG_PATH}"',
        "---",
        "",
    ]
    (content_dir / "ppl-sport-professionnel.md").write_text(
        "\n".join(fm), encoding="utf-8"
    )


def fetch_an_text_articles(timeout: float = 20.0) -> dict[str, str]:
    """R41-X (2026-05-09) — Fetch le texte de la PPL n° 1560 et le
    découpe par article. Retourne `{label_normalisé: html_du_corps}`.

    Découpage : `<p class="assnat9ArticleNum">` = en-tête d'article ;
    paragraphes `<p class="assnatLoiTexte">` qui suivent jusqu'au prochain
    en-tête = corps de l'article.

    Le label est normalisé en majuscules sans parenthèses (« Article 1er
    AA (nouveau) » → « ARTICLE 1ER AA ») pour matcher `_extract_article_label`
    appliqué aux titres d'amendements.

    Retourne un dict vide en cas d'erreur réseau / parsing.
    """
    try:
        import httpx
        from bs4 import BeautifulSoup
        with httpx.Client(timeout=timeout, follow_redirects=True) as cli:
            resp = cli.get(URL_AN_TEXTE_RAW)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "R41-X : fetch articles AN échoué (%s)", e
        )
        return {}

    soup = BeautifulSoup(html, "lxml")
    out: dict[str, str] = {}
    headers = soup.find_all("p", class_="assnat9ArticleNum")
    for hdr in headers:
        label_raw = hdr.get_text(" ", strip=True)
        # « Article 1er AA (nouveau) » → « ARTICLE 1ER AA »
        label_clean = re.sub(r"\([^)]*\)", "", label_raw).strip()
        # « Article 1 er AA » → « Article 1er AA » (le « er » est dans
        # un <span>/<sup> séparé, get_text le détache avec un espace).
        label_clean = re.sub(
            r"(\d+)\s+(ER|ERE|ÈRE)\b", r"\1\2",
            label_clean, flags=re.IGNORECASE,
        )
        label_clean = re.sub(r"\s+", " ", label_clean).strip()
        label_norm = label_clean.upper()
        if not label_norm.startswith("ARTICLE"):
            continue
        # Collecte des paragraphes assnatLoiTexte qui suivent jusqu'au
        # prochain assnat9ArticleNum.
        body_parts: list[str] = []
        for sib in hdr.find_next_siblings():
            cls = sib.get("class") or []
            if "assnat9ArticleNum" in cls:
                break
            if "assnatLoiTexte" in cls:
                # Texte propre, balises retirées sauf <em> et <i> pour
                # l'italique du « bis » et autres mises en forme légères.
                txt = sib.get_text(" ", strip=True)
                if txt:
                    body_parts.append(f"<p>{txt}</p>")
        if body_parts:
            out[label_norm] = "\n".join(body_parts)
    return out


def write_articles_data_file(site_data_dir: Path,
                              articles: dict[str, str]) -> None:
    """Écrit `site/data/special_ppl_articles.json` consommé par le layout
    Hugo via `site.Data.special_ppl_articles`."""
    site_data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.utcnow().isoformat(timespec="seconds"),
        "articles": articles,
    }
    (site_data_dir / "special_ppl_articles.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def export(rows: list[dict], site_root: Path) -> dict:
    """Point d'entrée appelé depuis `site_export.export()`. Génère le
    fichier de données + la page stub. Retourne le payload pour debug."""
    buckets = collect_special_ppl(rows)
    payload = build_payload(buckets)
    site_root = Path(site_root)
    write_data_file(site_root / "data", payload)
    write_page_stub(site_root / "content")
    # R41-X : fetch + découpage du texte de la PPL en articles. Best-effort
    # (réseau, timeout 20s). Si échec, le fichier articles n'est pas écrit
    # et le layout retombe sur l'absence d'articles → boutons inactifs.
    articles = fetch_an_text_articles()
    if articles:
        write_articles_data_file(site_root / "data", articles)
    return payload
