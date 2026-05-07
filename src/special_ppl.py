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

import json
from datetime import datetime
from pathlib import Path

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
URL_AN_DOSSIER = (
    "https://www.assemblee-nationale.fr/dyn/17/dossiers/"
    "sport-professionnel-organisation-gestion-financement"
)
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
            raw = r.get("raw") or {}
            stage_hint = ""
            url_hint = (r.get("url") or "").lower()
            if isinstance(raw, dict):
                stage_hint = (raw.get("stage") or "").lower()
            # AMANR... = AN amendements ; CION-CEDU dans URL = commission
            if "cion-" in url_hint or "commission" in stage_hint:
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


def _row_to_payload(r: dict, max_title: int = 220) -> dict:
    """Réduit un row à ses champs utiles pour le rendu Hugo."""
    raw = r.get("raw") or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "title": (r.get("title") or "")[:max_title],
        "url": r.get("url") or "",
        "chamber": r.get("chamber") or "",
        "date": (r.get("published_at") or "")[:10],
        "source_id": r.get("source_id") or "",
        "auteur": raw.get("auteur") or "",
        "groupe": raw.get("groupe") or "",
        "status_label": raw.get("status_label") or raw.get("status") or "",
        "stage": raw.get("stage") or "",
        "step": raw.get("step") or "",
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
            "url_an_dossier": URL_AN_DOSSIER,
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
        "fullwidth: true",
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
