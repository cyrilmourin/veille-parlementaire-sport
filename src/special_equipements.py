"""R42-CS (2026-05-15) — Page spéciale PPL Équipements sportifs.

Cyril 2026-05-15 : « on l'applique sur le dossier
https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N54138 (et
donc les liens de la page d'accueil (dont les dernières 24) et dans
dosleg renvoient vers cette nouvelle page dédiée également) ».

Dossier visé : Proposition de loi visant à encourager les partenariats
entre les collectivités territoriales et les personnes morales de
droit privé en matière d'acquisition, de réalisation ou de rénovation
d'équipements sportifs. AN n° 2667, dossier DLR5L17N54138.

Réutilise les helpers génériques de `special_ppl.py` (build_wordcloud,
_row_to_payload, _build_extract, _group_amdt_by_article, etc.) en y
passant ses propres constantes via paramètres ou en redéfinissant
juste la fonction de matching.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import special_ppl as _spp

# ---------------------------------------------------------------------------
# Constantes — identifiants stables de la PPL équipements sportifs
# ---------------------------------------------------------------------------

PPL_KEY = "ppl-partenariats-equipements-sportifs"
PPL_TITLE = "Spécial PPL Équipements sportifs"
PPL_SLUG_PATH = "/ppl-partenariats-equipements-sportifs/"
DATA_KEY = "special_ppl_equip"  # site.Data.special_ppl_equip
DATA_FILENAME = "special_ppl_equip.json"

# R42-CV (2026-05-15) — Intitulé exact de la PPL pour le titre de la
# page (avant : « Proposition de loi » générique).
HERO_TITLE = (
    "Proposition de loi visant à encourager les partenariats entre les "
    "collectivités territoriales et les personnes morales de droit privé "
    "en matière d'acquisition, de réalisation ou de rénovation "
    "d'équipements sportifs"
)
HERO_SUBTITLE = "AN n° 2667 · dossier DLR5L17N54138"

# Identifiants techniques AN. Dossier DLR5L17N54138 = PPL n° 2667.
# Texte de référence : PIONANR5L17B2667 (dépôt initial AN).
# Pas de texte commission à ce jour (dossier au stade « dépôt »).
AN_TEXTE_REF = "PIONANR5L17B2667"
ALL_TEXTE_REFS = frozenset({AN_TEXTE_REF})
AN_TEXTE_NUM = "2667"
AN_DOSSIER_ID = "DLR5L17N54138"

# Mots significatifs du titre — utilisés pour matcher les items qui n'ont
# pas de texte_ref (ex. CR séance, communiqués). Tous doivent être présents.
TITLE_REQUIRED_WORDS = frozenset({
    "partenariats", "collectivites", "equipements", "sportifs",
})
# Variante avec accents (le matcher normalise déjà avec unidecode, mais on
# double-checke).

URL_AN_TEXTE = (
    "https://www.assemblee-nationale.fr/dyn/17/textes/"
    "l17b2667_proposition-loi"
)
URL_AN_DOSSIER = (
    "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N54138"
)
# Pas de dossier Sénat (PPL d'initiative AN).
URL_SENAT_DOSSIER = ""
URL_AMDT_LISTE_AN = (
    "https://www.assemblee-nationale.fr/dyn/17/amendements?"
    "dossier_legislatif=DLR5L17N54138"
)

# Rapporteurs : pas encore nommés (dossier au stade dépôt). Liste vide
# → le bloc Rapporteurs côté template ne s'affichera pas.
RAPPORTEURS: list[dict] = []


def row_matches_special_equipements(row: dict) -> bool:
    """True si le row est lié à la PPL Équipements sportifs (DLR5L17N54138).

    Critères (OR) :
    - `raw.texte_ref` ∈ ALL_TEXTE_REFS
    - `raw.dossier_id` == AN_DOSSIER_ID
    - URL contient « 2667 » ET « proposition-loi » (AN textes)
    - URL contient « DLR5L17N54138 » (dossier AN)
    - Titre contient TOUS les mots de TITLE_REQUIRED_WORDS
    """
    raw = row.get("raw") or {}
    if isinstance(raw, dict):
        for k in ("texte_ref", "texteRef", "dossier_id", "signet"):
            v = raw.get(k)
            if isinstance(v, str) and v in ALL_TEXTE_REFS:
                return True
            if isinstance(v, str) and v == AN_DOSSIER_ID:
                return True
    url = (row.get("url") or "").lower()
    if "dlr5l17n54138" in url:
        return True
    if "l17b2667" in url:
        return True
    title = row.get("title") or ""
    if "(n° 2667)" in title or "(no 2667)" in title.lower():
        return True
    words = _spp._norm_words(title)
    if TITLE_REQUIRED_WORDS <= words:
        return True
    return False


def collect_special_equipements(rows: list[dict]) -> dict:
    """Variante de `special_ppl.collect_special_ppl` qui matche sur le
    dossier équipements sportifs (DLR5L17N54138)."""
    out: dict[str, list[dict]] = {
        "dosleg": [], "agenda": [],
        "amdt_commission": [], "amdt_seance": [],
        "comptes_rendus": [], "communiques": [], "questions": [],
    }
    for r in rows:
        if not row_matches_special_equipements(r):
            continue
        cat = (r.get("category") or "").strip()
        if cat == "dossiers_legislatifs":
            out["dosleg"].append(r)
        elif cat == "agenda":
            # R42-CX (2026-05-15) — Skip réunions reportées/annulées
            # (cf. special_ppl.collect_special_ppl).
            _raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
            _etat = (_raw.get("etat") or "").lower() if isinstance(_raw, dict) else ""
            if _etat and ("report" in _etat or "annul" in _etat):
                continue
            out["agenda"].append(r)
        elif cat == "amendements":
            title = r.get("title") or ""
            m = _spp._AMDT_NUM_PREFIX_RE.search(title)
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
    for k in out:
        out[k].sort(key=lambda r: r.get("published_at") or "", reverse=True)
    return out


def build_payload(buckets: dict) -> dict:
    """Construit le payload JSON exposé via site/data/special_ppl_equip.json.

    Réutilise les helpers génériques de `special_ppl` (build_wordcloud,
    build_sort_stats, build_groupe_stats, _row_to_payload,
    _group_amdt_by_article) — la seule chose qui change est la méta.
    """
    # R42-CV (2026-05-15) — Cap 200 → 5000, idem special_ppl.
    LIMITS = {
        "dosleg": 5, "agenda": 30,
        "amdt_commission": 5000, "amdt_seance": 5000,
        "comptes_rendus": 30, "communiques": 30, "questions": 30,
    }
    payload = {
        "meta": {
            "key": PPL_KEY,
            "title": PPL_TITLE,
            "slug_path": PPL_SLUG_PATH,
            "hero_title": HERO_TITLE,
            "hero_subtitle": HERO_SUBTITLE,
            "url_an_texte": URL_AN_TEXTE,
            "url_an_dossier": URL_AN_DOSSIER,
            "url_senat_dossier": URL_SENAT_DOSSIER,
            "url_amdt_liste_an": URL_AMDT_LISTE_AN,
            "rapporteurs": list(RAPPORTEURS),
            "an_num": AN_TEXTE_NUM,
            "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        },
    }
    for bucket, items in buckets.items():
        limit = LIMITS.get(bucket, 50)
        payload[bucket] = [_spp._row_to_payload(r) for r in items[:limit]]
    payload["counts"] = {k: len(v) for k, v in buckets.items()}
    payload["amdt_commission_by_article"] = _spp._group_amdt_by_article(
        payload["amdt_commission"]
    )
    payload["amdt_seance_by_article"] = _spp._group_amdt_by_article(
        payload["amdt_seance"]
    )
    payload["wordcloud_commission"] = _spp._build_wordcloud(
        payload["amdt_commission"]
    )
    payload["sort_stats_commission"] = _spp._build_sort_stats(
        payload["amdt_commission"]
    )
    payload["groupe_stats_commission"] = _spp._build_groupe_stats(
        payload["amdt_commission"]
    )
    # R42-CV (2026-05-15) — Bloc analyse manuelle (idem special_ppl).
    payload["analysis"] = _spp.load_analysis(payload["meta"]["key"])
    return payload


def write_data_file(site_data_dir: Path, payload: dict) -> None:
    """Écrit `site/data/special_ppl_equip.json`."""
    site_data_dir.mkdir(parents=True, exist_ok=True)
    (site_data_dir / DATA_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_page_stub(content_dir: Path) -> None:
    """Écrit `site/content/ppl-partenariats-equipements-sportifs.md` (page racine)."""
    content_dir.mkdir(parents=True, exist_ok=True)
    fm = [
        "---",
        f'title: "{PPL_TITLE}"',
        f"date: {datetime.now():%Y-%m-%d}",
        'description: "Suivi de la proposition de loi n° 2667 visant à '
        "encourager les partenariats entre les collectivités territoriales "
        'et le privé en matière d\'équipements sportifs."',
        "type: page",
        "layout: ppl-partenariats-equipements",
        f'url: "{PPL_SLUG_PATH}"',
        "---",
        "",
    ]
    (content_dir / "ppl-partenariats-equipements-sportifs.md").write_text(
        "\n".join(fm), encoding="utf-8"
    )


def export(rows: list[dict], site_root: Path) -> dict:
    """Point d'entrée appelé depuis `site_export.export()`. Génère le
    fichier de données + la page stub pour la PPL Équipements sportifs.
    """
    buckets = collect_special_equipements(rows)
    payload = build_payload(buckets)
    site_root = Path(site_root)
    write_data_file(site_root / "data", payload)
    write_page_stub(site_root / "content")
    return payload
