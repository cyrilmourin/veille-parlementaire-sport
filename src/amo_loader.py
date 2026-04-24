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
import re
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

# R39-B (2026-04-25, import audit Lidl) — cache jumeau du précédent pour
# le haystack (libellés d'actes cumulés) du dossier parent. Consommé par
# `_normalize_amendement` pour poser `raw.libelles_haystack` sur les
# amendements, ce qui permet au matcher (R26) de voir les libellés
# d'actes du dossier parent même quand l'amendement lui-même ne cite
# pas les mots-clés. Symétrie avec `_txt_*` pour éviter les surprises
# à la maintenance.
_LIB_CACHE_ENV = "VEILLE_AN_TEXTE_LIBELLES_CACHE"
_DEFAULT_LIB_CACHE = Path("data/an_texte_to_libelles.json")
_lib_lock = threading.Lock()
_lib_loaded: dict | None = None


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


def _resolve_lib_path() -> Path:
    env = os.environ.get(_LIB_CACHE_ENV)
    if env:
        return Path(env)
    return _DEFAULT_LIB_CACHE


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
    global _loaded, _load_error, _txt_loaded, _lib_loaded
    with _lock:
        _loaded = None
        _load_error = None
    with _txt_lock:
        _txt_loaded = None
    with _lib_lock:
        _lib_loaded = None


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
# R39-B — cache `texteLegislatifRef` → `libelles_haystack` du dossier parent
# (symétrie avec `texteLegislatifRef` → `dossier_title` ci-dessus).
# ---------------------------------------------------------------------------


def _load_lib_cache(path: Path | None = None) -> dict:
    """Charge le cache `texteLegislatifRef → libelles_haystack` du dossier.

    Tolère l'absence (retour dict vide) — à ce stade tous les amendements
    reçoivent `""`, le matcher retombe sur title+summary comme avant R39-B.
    """
    global _lib_loaded
    with _lib_lock:
        if _lib_loaded is not None:
            return _lib_loaded
        target = path or _resolve_lib_path()
        if not target.exists():
            log.info(
                "Cache texte→libelles introuvable (%s) — amendements sans "
                "haystack dossier parent", target,
            )
            _lib_loaded = {"textes": {}, "generated_at": None}
            return _lib_loaded
        try:
            data = json.loads(target.read_text())
        except Exception as exc:
            log.warning("Cache texte→libelles corrompu (%s) : %s", target, exc)
            _lib_loaded = {"textes": {}, "generated_at": None}
            return _lib_loaded
        data.setdefault("textes", {})
        _lib_loaded = data
        log.info(
            "Cache texte→libelles chargé : %d entrées (gen %s)",
            len(data["textes"]), data.get("generated_at") or "?",
        )
        return _lib_loaded


def write_texte_libelles_cache(
    textes: dict[str, str], path: Path | None = None,
) -> Path:
    """Persiste le mapping `texteLegislatifRef → libelles_haystack`.

    Appelé par `_normalize_dosleg` via `assemblee.fetch_source` en fin de
    passe, comme `write_texte_dossier_cache`. Fichier versionné dans
    `data/an_texte_to_libelles.json`.
    """
    global _lib_loaded
    target = path or _resolve_lib_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "textes": dict(textes),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    with _lib_lock:
        _lib_loaded = payload
    log.info(
        "Cache texte→libelles écrit : %d entrées → %s", len(textes), target,
    )
    return target


def resolve_texte_libelles(texte_ref: str) -> str:
    """Renvoie le haystack d'actes cumulés du dossier parent pour un
    `texteLegislatifRef`. Vide si inconnu."""
    if not texte_ref or not isinstance(texte_ref, str):
        return ""
    data = _load_lib_cache()
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


def resolve_groupe_ref(pa_uid: str) -> str:
    """Renvoie l'identifiant POxxx du groupe politique d'un acteur, ou "".

    R23-B (2026-04-23) : le cache AMO stocke `groupe_ref` en plus de
    l'abrégé (`groupe`). Ce ref permet ensuite d'appeler
    `resolve_organe(po_uid, prefer_long=True)` pour récupérer le libellé
    long (tooltip au hover côté templates).
    """
    if not pa_uid or not isinstance(pa_uid, str):
        return ""
    data = load_cache()
    rec = data["acteurs"].get(pa_uid.strip())
    if not rec:
        return ""
    return rec.get("groupe_ref", "") or ""


def build_photo_url_an(pa_uid: str, *, legislature: int = 17) -> str:
    """Construit l'URL de la photo portrait d'un député AN à partir d'un PAxxx.

    R23-C (2026-04-23) + R23-C2 (2026-04-23, fix URL) — pattern
    déterministe observé sur assemblee-nationale.fr :

        https://www.assemblee-nationale.fr/dyn/static/tribun/{LEG}/photos/carre/{digits}.jpg

    où `{digits}` sont les chiffres du PAxxx (sans le préfixe "PA") et
    `{LEG}` est la législature en cours (17 depuis juillet 2024). Testé
    et validé par fetch direct sur des acteurs recents (HTTP 200).

    Historique : le pattern initial de R23-C
    `/tribun/{LEG}/photos/{digits}.jpg` produisait des 404 sur le site
    public — c'etait un ancien chemin, maintenant redirige vers /dyn/
    static/tribun/LEG/photos/carre/. Les <img> avaient donc
    `onerror='this.style.display=none'` qui masquait systematiquement
    la photo.

    Renvoie "" si `pa_uid` n'est pas un PAxxx valide — les templates
    décideront de ne pas émettre la balise `<img>` dans ce cas.

    Aucune requête HTTP effectuée : l'URL est juste construite. La
    robustesse (image absente → 404) est gérée par le template via
    `onerror` et par la politique du navigateur qui ne bloque pas la page.
    """
    if not pa_uid or not isinstance(pa_uid, str):
        return ""
    uid = pa_uid.strip()
    if not uid.startswith("PA"):
        return ""
    digits = uid[2:]
    if not digits.isdigit():
        return ""
    return (
        f"https://www.assemblee-nationale.fr/dyn/static/tribun/"
        f"{int(legislature)}/photos/carre/{digits}.jpg"
    )


# R23-C5 (2026-04-23) : une fiche Senat de la forme
# `//www.senat.fr/senfic/<slug>.html` donne le slug senateur, qu'on
# transforme en URL photo `https://www.senat.fr/senimg/<slug>_carre.jpg`.
# Le slug est du type `wattebled_dany19585h` (nom_prenomIDH). La regex
# accepte les URLs avec ou sans schema, avec www. ou sans, et tolere
# les majuscules (certaines fiches legacy sont capitalisees).
_SENAT_SENFIC_RE = re.compile(
    r"^(?:https?:)?//(?:www\.)?senat\.fr/senfic/([a-zA-Z0-9_-]+)\.html?$",
    re.IGNORECASE,
)


def build_photo_url_senat(fiche_senateur_url: str) -> str:
    """Construit l'URL photo d'un sénateur à partir de son URL `senfic`.

    R23-C5 (2026-04-23) — pattern déterministe observé sur senat.fr,
    extrait du HTML des fiches sénateur :

        https://www.senat.fr/senimg/{slug}_carre.jpg

    où `{slug}` est le slug de la fiche (ex. `wattebled_dany19585h`).
    L'entrée attendue est la valeur de la colonne "Fiche Sénateur" des
    CSV amendements Sénat, qui arrive typiquement sous la forme
    `//www.senat.fr/senfic/wattebled_dany19585h.html` (pas de schema).

    Tests réseau effectués (23 avril 2026) :
      - /senimg/wattebled_dany19585h_carre.jpg → HTTP 200
      - /senimg/richard_olivia21038e_carre.jpg → HTTP 200

    Renvoie "" si :
      - l'entrée est vide / None / pas une string ;
      - l'URL n'a pas le format attendu (`/senfic/<slug>.html`).

    Aucune requête HTTP effectuée : l'URL est juste construite. La
    robustesse (image absente → 404) est gérée par le template via
    `onerror`.
    """
    if not fiche_senateur_url or not isinstance(fiche_senateur_url, str):
        return ""
    url = fiche_senateur_url.strip()
    if not url:
        return ""
    m = _SENAT_SENFIC_RE.match(url)
    if not m:
        return ""
    slug = m.group(1)
    return f"https://www.senat.fr/senimg/{slug}_carre.jpg"


def resolve_groupe_long(pa_uid: str) -> str:
    """Renvoie le libellé LONG du groupe politique d'un acteur, ou "".

    R23-B (2026-04-23) : utilisé pour le tooltip `title=""` au hover.
    Combine `groupe_ref` (PO du groupe) + `resolve_organe(prefer_long=True)`.
    Renvoie "" si l'acteur est inconnu OU si le cache n'a pas le
    `groupe_ref` (ex: acteurs ingérés avant R23-B — le cache peut être
    régénéré via scripts/refresh_amo_cache.py).
    """
    ref = resolve_groupe_ref(pa_uid)
    if not ref:
        return ""
    return resolve_organe(ref, prefer_long=True)


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
