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
# Note R41-N : pas d'URL canonique du « dossier législatif AN » — le slug
# n'est pas connu de manière stable depuis l'open data, on utilisait un
# slug deviné qui renvoyait vers une autre page. On expose seulement le
# texte AN (URL stable) et le dossier Sénat (URL stable ppl24-456).
URL_SENAT_DOSSIER = (
    "https://www.senat.fr/dossier-legislatif/ppl24-456.html"
)


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
    """Clé de tri pour ordonner les groupes d'articles : Article 1ER → 2 →
    2 BIS → 3 ... Articles « additionnels après ... » en queue.

    Stratégie : extraire le 1er nombre, mettre les "ADDITIONNEL" en haute
    valeur, "SANS ARTICLE" en toute fin.
    """
    if not label:
        return (99999, 9, "")
    up = label.upper()
    if "SANS ARTICLE" in up or label == "Sans article":
        return (99999, 9, label)
    is_additional = "ADDITIONNEL" in up
    m = re.search(r"(\d+)", label)
    num = int(m.group(1)) if m else 9999
    # Bis/Ter/Quater pondèrent le tri secondaire
    suffix = 0
    if "BIS" in up: suffix = 1
    elif "TER" in up: suffix = 2
    elif "QUATER" in up: suffix = 3
    elif "QUINQUIES" in up: suffix = 4
    additional_weight = 1 if is_additional else 0
    return (num, suffix + 5 * additional_weight, label)


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
    return {
        "title": (r.get("title") or "")[:max_title],
        "url": _safe_url(r, raw),
        "chamber": r.get("chamber") or "",
        "date": (r.get("published_at") or "")[:10],
        "source_id": r.get("source_id") or "",
        "auteur": raw.get("auteur") or "",
        "groupe": raw.get("groupe") or "",
        "status_label": raw.get("status_label") or raw.get("status") or "",
        # R41-P : sort (« adopté », « rejeté », « irrecevable », « retiré »,
        # « tombé »…) exposé pour le filtre UI sur la page dédiée.
        "sort": raw.get("sort") or "",
        "stage": raw.get("stage") or "",
        "step": raw.get("step") or "",
        # R41-P : extrait du corps (max 400 chars), sans le titre.
        "extract": _build_extract(r, raw),
        # R41-Q : article ciblé par l'amdt (« ARTICLE 5 », « ARTICLE 1ER A »,
        # « ARTICLE 2 BIS »...). Vide pour les autres types d'items.
        "article": _extract_article_label(r.get("title") or ""),
    }


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
            "url_senat_dossier": URL_SENAT_DOSSIER,
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


def export(rows: list[dict], site_root: Path) -> dict:
    """Point d'entrée appelé depuis `site_export.export()`. Génère le
    fichier de données + la page stub. Retourne le payload pour debug."""
    buckets = collect_special_ppl(rows)
    payload = build_payload(buckets)
    site_root = Path(site_root)
    write_data_file(site_root / "data", payload)
    write_page_stub(site_root / "content")
    return payload
