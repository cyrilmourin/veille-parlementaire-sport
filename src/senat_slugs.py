"""R25b-A (2026-04-23) — Index nom→slug des 348 sénateurs en activité.

Alimenté par `scripts/build_senat_slugs.py` (scraping de la liste officielle
https://www.senat.fr/senateurs/senatl.html) → `data/senat_slugs.json`. Le
JSON est versionné dans le repo et rechargeable sans réseau.

Utilisé par `site_export._enrich_senat_question_photo` pour résoudre
l'URL photo d'une question Sénat quand l'auteur n'apparaît pas dans le
cache R23-N (construit depuis les amendements). Couverture : les 348
sénateurs en activité, y compris ceux qui ne déposent que des QAG et pas
d'amendements dans la fenêtre de publication.

Ce module est importé une seule fois par process ; le JSON est chargé
en mémoire à l'import (singleton _CACHE). Refresh manuel en relançant
`scripts/build_senat_slugs.py` après chaque renouvellement sénatorial.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "senat_slugs.json"
_CIV_TOKENS = {"m.", "mme", "mlle", "dr", "pr", "m", "mme.", "mlle."}

# Cache singleton chargé à la demande (pas à l'import, pour ne pas payer
# le coût dans les tests qui n'exercent pas le module).
_CACHE: Optional[dict[str, tuple[str, str]]] = None


def _load_cache() -> dict[str, tuple[str, str]]:
    """Charge `data/senat_slugs.json` et construit le dict key→(photo, fiche).

    Idempotent : le singleton est mémorisé dans `_CACHE` pour éviter les
    ré-ouvertures fichier successives au cours d'un export. Si le fichier
    n'existe pas (ex. fresh clone sans build), on log un warning et on
    renvoie un dict vide — le fallback R23-N continue de fonctionner.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not _JSON_PATH.exists():
        log.warning(
            "senat_slugs: %s absent — lancer `python scripts/build_senat_slugs.py`",
            _JSON_PATH,
        )
        _CACHE = {}
        return _CACHE
    try:
        payload = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.error("senat_slugs: lecture %s KO (%s)", _JSON_PATH, e)
        _CACHE = {}
        return _CACHE
    entries = payload.get("entries") or []
    cache: dict[str, tuple[str, str]] = {}
    for e in entries:
        key = (e.get("key") or "").strip()
        photo = (e.get("photo_url") or "").strip()
        fiche = (e.get("fiche_url") or "").strip()
        if not key:
            continue
        cache.setdefault(key, (photo, fiche))
    log.info("senat_slugs: %d entrées chargées", len(cache))
    _CACHE = cache
    return _CACHE


def _normalize(name: str) -> str:
    """Normalise un nom pour lookup (même algo que `_normalize_auteur_name_senat`
    dans site_export) : unidecode + lowercase + retrait civilité + tri tokens.

    On duplique la logique ici plutôt que d'importer depuis site_export pour
    éviter un cycle de dépendances (senat_slugs doit être importable par
    site_export et par les parsers). Le test `test_senat_slugs.py` vérifie
    la cohérence des deux normalisations.
    """
    if not name:
        return ""
    try:
        from unidecode import unidecode
        s = unidecode(name).lower().strip()
    except ImportError:  # pragma: no cover
        s = name.lower().strip()
    s = re.sub(r"[.,;]", " ", s)
    tokens = [t for t in s.split() if t and t not in _CIV_TOKENS]
    if not tokens:
        return ""
    return " ".join(sorted(tokens))


def resolve_photo(civilite: str, prenom: str, nom: str) -> Optional[tuple[str, str]]:
    """Renvoie (photo_url, fiche_url) pour un sénateur identifié par
    civilité + prénom + nom, ou None si pas trouvé dans l'index.

    Exemples :
      resolve_photo("Mme", "Cécile", "Cukierman")
        → ("https://www.senat.fr/senimg/cukierman_cecile11056n_carre.jpg",
           "https://www.senat.fr/senateur/cukierman_cecile11056n.html")
      resolve_photo("M.", "Jean-Baptiste", "BLANC")
        → (url, url)  # MAJUSCULES tolérées (unidecode + lowercase)
      resolve_photo("", "", "Inconnu")
        → None
    """
    cache = _load_cache()
    if not cache:
        return None
    # On construit la clé candidate depuis les 3 morceaux ; la normalisation
    # absorbe la civilité, les accents, la casse et l'ordre nom/prénom.
    candidate = " ".join(p for p in [civilite, prenom, nom] if p).strip()
    key = _normalize(candidate)
    if not key:
        return None
    return cache.get(key)


def resolve_by_auteur(auteur: str) -> Optional[tuple[str, str]]:
    """Variante utile quand on n'a que le champ `Auteur` brut (ex.
    `M. Jean-Baptiste BLANC` côté amendements Sénat ou `Mme Cécile
    CUKIERMAN` côté débats). Utilisé aussi pour enrichir le cache R23-N
    d'amendements pré-R23-C5 dont `auteur_photo_url` n'a jamais été peuplé.
    """
    cache = _load_cache()
    if not cache or not auteur:
        return None
    key = _normalize(auteur)
    if not key:
        return None
    return cache.get(key)


def reset_cache_for_tests() -> None:
    """Réinitialise le singleton — réservé aux tests unitaires qui
    overrident `_JSON_PATH` via monkeypatch.
    """
    global _CACHE
    _CACHE = None
