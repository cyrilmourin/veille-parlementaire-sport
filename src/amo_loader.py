"""Résolution des identifiants AMO (PAxxx / POxxx) vers des libellés lisibles.

Le dump AMO de l'Assemblée (~80 Mo) est trop gros pour être chargé à chaque
run du pipeline. Le script `scripts/refresh_amo_cache.py` le télécharge
et produit un JSON compact `data/amo_resolved.json` (~100 Ko), versionné
dans le repo, régénéré ~hebdomadairement par le workflow GitHub Actions.

Ce module fournit l'interface côté pipeline :

    from src.amo_loader import resolve_acteur, resolve_organe, format_auteur

    name = resolve_acteur("PA720770")       # "Marie Dupont"
    full = format_auteur("PA720770")        # "Mme Marie Dupont (LFI-NFP)"
    org  = resolve_organe("PO838901")       # "Commission des affaires culturelles"

En cas d'absence de cache (premier run, dev local sans refresh), les
fonctions retournent des libellés génériques (« Député PAxxx » / « POxxx »)
plutôt que de lever — le pipeline doit rester tolérant.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_CACHE_PATH_ENV = "VEILLE_AMO_CACHE"
_DEFAULT_CACHE = Path("data/amo_resolved.json")
_STALE_WARN_DAYS = 30  # log WARN au-delà, mais on continue

_lock = threading.Lock()
_loaded: dict | None = None
_load_error: str | None = None

# Cache auxiliaire : mapping `texteLegislatifRef` (ex: "PIONANR5L17BTC2335")
# → titre humain du dossier parent. Alimenté par `_normalize_dosleg` lors
# de l'ingestion quotidienne de `Dossiers_Legislatifs.json.zip`. Utilisé
# par `_normalize_amendement` pour enrichir le haystack de matching des
# amendements avec le thème du dossier parent (essentiel pour que les
# mots-clés du sujet — "JO 2024", "clubs sportifs" — ressortent même
# quand l'amendement lui-même ne les cite pas littéralement).
_TXT_CACHE_ENV = "VEILLE_AN_TEXTE_DOSSIER_CACHE"
_DEFAULT_TXT_CACHE = Path("data/an_texte_to_dossier.json")
_txt_lock = threading.Lock()
_txt_loaded: dict | None = None


def _resolve_path() -> Path:
    env = os.environ.get(_CACHE_PATH_ENV)
    if env:
        return Path(env)
    return _DEFAULT_CACHE


def _resolve_txt_path() -> Path:
    env = os.environ.get(_TXT_CACHE_ENV)
    if env:
        return Path(env)
    return _DEFAULT_TXT_CACHE


def load_cache(path: Path | None = None, force_reload: bool = False) -> dict:
    """Charge le cache AMO. Thread-safe, lazy."""
    global _loaded, _load_error
    with _lock:
        if _loaded is not None and not force_reload:
            return _loaded
        target = path or _resolve_path()
        if not target.exists():
            _load_error = f"Cache AMO introuvable : {target}"
            log.warning("%s — libellés PAxxx/POxxx resteront bruts", _load_error)
            _loaded = {"acteurs": {}, "organes": {}, "generated_at": None}
            return _loaded
        try:
            data = json.loads(target.read_text())
        except Exception as exc:
            _load_error = f"Cache AMO corrompu : {exc}"
            log.error(_load_error)
            _loaded = {"acteurs": {}, "organes": {}, "generated_at": None}
            return _loaded

        # Vérif staleness (purement informatif)
        ts = data.get("generated_at")
        if ts:
            try:
                gen = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - gen).days
                if age_days > _STALE_WARN_DAYS:
                    log.warning("Cache AMO ancien (%dj) — relancer refresh_amo_cache", age_days)
            except Exception:
                pass

        data.setdefault("acteurs", {})
        data.setdefault("organes", {})
        _loaded = data
        log.info("Cache AMO chargé : %d acteurs, %d organes (gen %s)",
                 len(data["acteurs"]), len(data["organes"]), ts or "?")
        return _loaded


def reset() -> None:
    """Utile dans les tests."""
    global _loaded, _load_error, _txt_loaded
    with _lock:
        _loaded = None
        _load_error = None
    with _txt_lock:
        _txt_loaded = None


def _load_txt_cache(path: Path | None = None) -> dict:
    """Charge le cache `texteLegislatifRef → dossier_title`.

    Tolère l'absence du fichier (premier run, dev local) — retourne
    un dict vide qui produira "" pour toutes les résolutions.
    """
    global _txt_loaded
    with _txt_lock:
        if _txt_loaded is not None:
            return _txt_loaded
        target = path or _resolve_txt_path()
        if not target.exists():
            log.info("Cache texte→dossier introuvable (%s) — amendements "
                     "sans titre dossier parent", target)
            _txt_loaded = {"textes": {}, "generated_at": None}
            return _txt_loaded
        try:
            data = json.loads(target.read_text())
        except Exception as exc:
            log.warning("Cache texte→dossier corrompu (%s) : %s", target, exc)
            _txt_loaded = {"textes": {}, "generated_at": None}
            return _txt_loaded
        data.setdefault("textes", {})
        _txt_loaded = data
        log.info("Cache texte→dossier chargé : %d entrées (gen %s)",
                 len(data["textes"]), data.get("generated_at") or "?")
        return _txt_loaded


def write_texte_dossier_cache(textes: dict[str, str], path: Path | None = None) -> Path:
    """Persiste le mapping `texteLegislatifRef → dossier_title`.

    Appelé par `_normalize_dosleg` (ou par un script dédié) après ingestion
    du dump dossiers. Le fichier est versionné dans data/ et lu par les
    runs ultérieurs jusqu'au prochain refresh.
    """
    global _txt_loaded
    target = path or _resolve_txt_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "textes": dict(textes),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    with _txt_lock:
        _txt_loaded = payload
    log.info("Cache texte→dossier écrit : %d entrées → %s",
             len(textes), target)
    return target


def resolve_texte_dossier(texte_ref: str) -> str:
    """Renvoie le titre humain du dossier parent pour un `texteLegislatifRef`.

    Ex : `resolve_texte_dossier("PIONANR5L17BTC2335")` →
         "visant à permettre aux salariés de certains établissements …"

    Si inconnu : "" (l'appelant décide du fallback).
    """
    if not texte_ref or not isinstance(texte_ref, str):
        return ""
    data = _load_txt_cache()
    return data["textes"].get(texte_ref.strip(), "") or ""


# ---------------------------------------------------------------------------
# Resolveurs publics
# ---------------------------------------------------------------------------


def resolve_acteur(pa_uid: str, *, with_civ: bool = True) -> str:
    """Renvoie un libellé lisible pour un PAxxx.

    Si inconnu : retourne "" (l'appelant décide du fallback).
    """
    if not pa_uid or not isinstance(pa_uid, str):
        return ""
    uid = pa_uid.strip()
    if not uid.startswith("PA"):
        return ""
    data = load_cache()
    rec = data["acteurs"].get(uid)
    if not rec:
        return ""
    prenom = rec.get("prenom", "").strip()
    nom = rec.get("nom", "").strip()
    civ = rec.get("civ", "").strip()
    bits = []
    if with_civ and civ:
        bits.append(civ)
    if prenom:
        bits.append(prenom)
    if nom:
        bits.append(nom)
    return " ".join(bits)


def resolve_groupe(pa_uid: str) -> str:
    """Renvoie le groupe politique (abrégé) d'un acteur, ou ""."""
    if not pa_uid or not isinstance(pa_uid, str):
        return ""
    data = load_cache()
    rec = data["acteurs"].get(pa_uid.strip())
    if not rec:
        return ""
    return rec.get("groupe", "") or ""


def resolve_qualites(pa_uid: str, limit: int = 3) -> list[str]:
    """Renvoie les qualités notables (président, rapporteur…) d'un acteur."""
    if not pa_uid or not isinstance(pa_uid, str):
        return []
    data = load_cache()
    rec = data["acteurs"].get(pa_uid.strip())
    if not rec:
        return []
    qs = rec.get("qualites") or []
    return list(qs[:limit])


def resolve_organe(po_uid: str, *, prefer_long: bool = True) -> str:
    """Renvoie le libellé d'un POxxx.

    Si `prefer_long` est True (défaut), renvoie le libellé long ;
    sinon privilégie l'abrégé / acronyme quand il est lisible.
    """
    if not po_uid or not isinstance(po_uid, str):
        return ""
    uid = po_uid.strip()
    if not uid.startswith("PO"):
        return ""
    data = load_cache()
    rec = data["organes"].get(uid)
    if not rec:
        return ""
    if prefer_long:
        return rec.get("libelle") or rec.get("libelle_abrege") or rec.get("libelle_abrev") or ""
    return rec.get("libelle_abrev") or rec.get("libelle_abrege") or rec.get("libelle") or ""


# ---------------------------------------------------------------------------
# Helpers de présentation (formatage de titres)
# ---------------------------------------------------------------------------


def format_auteur(pa_uid: str, *, default_role: str = "Député") -> str:
    """Formate un acteur pour un titre : "Mme Marie Dupont (LFI-NFP)".

    Si inconnu : retourne "{default_role} {pa_uid}".
    """
    name = resolve_acteur(pa_uid)
    if not name:
        return f"{default_role} {pa_uid}" if pa_uid else default_role
    groupe = resolve_groupe(pa_uid)
    return f"{name} ({groupe})" if groupe else name


def format_organe(po_uid: str, *, default_prefix: str = "Organe") -> str:
    """Formate un organe pour un titre : "Commission des affaires culturelles".

    Si inconnu : "{default_prefix} {po_uid}".
    """
    lib = resolve_organe(po_uid)
    if not lib:
        return f"{default_prefix} {po_uid}" if po_uid else default_prefix
    return lib


def stats() -> dict:
    """Renvoie des statistiques sur le cache (pour diagnostic / logs)."""
    data = load_cache()
    return {
        "acteurs": len(data.get("acteurs", {})),
        "organes": len(data.get("organes", {})),
        "generated_at": data.get("generated_at"),
        "load_error": _load_error,
    }
