"""Persistance du snapshot "vu au dernier run" pour le ping d'après-midi.

R24 (2026-04-23) — feature ping :
Un second job GitHub Actions (`ping-afternoon`, 17h30 Paris, lun-ven) relit la
DB et envoie un email court "Nouveautés de l'après-midi" si de nouveaux items
matchés sont apparus dans les 4 catégories prioritaires depuis le run du matin.

Ce module est un helper mince : il sérialise/désérialise un petit fichier JSON
(`data/ping_state.json`, ~quelques Ko) qui contient :
- `last_run_at` : ISO timestamp du dernier pipeline complet (run 4h)
- `last_ping_at` : ISO timestamp du dernier ping envoyé (utile pour debug)
- `pinged_uids` : mapping {category: [hash_key, …]} des items matchés connus.
  Le ping utilise ce set comme baseline : tout item matché en DB dans une
  catégorie surveillée, absent de ce set, est une "nouveauté" à notifier.

Le fichier est commité par le bot comme les autres caches `data/*.json`.

Design :
- Lecture tolérante : fichier absent ou corrompu → dict vide (empty baseline),
  aucune exception levée. Le pipeline tourne même si le state n'a jamais été
  initialisé (1er déploiement).
- Écriture atomique : write tmp + os.replace pour éviter qu'un fichier
  tronqué ne soit lu par un run concurrent (le workflow sérialise avec
  concurrency:, mais ceinture + bretelles).
- Sets stockés en listes triées dans le JSON (déterminisme, diffs git
  lisibles, pas de churn entre runs qui change juste l'ordre).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

log = logging.getLogger("veille.ping_state")


PING_CATEGORIES: tuple[str, ...] = (
    "dossiers_legislatifs",
    "amendements",
    "questions",
    "comptes_rendus",
)
"""Catégories surveillées par le ping. Les autres (agenda, jorf, nominations,
communiques) ne déclenchent pas de ping : leur rythme de publication n'est
pas assez "chaud" pour justifier une alerte intra-journée."""


def load(path: str | Path) -> dict:
    """Charge `ping_state.json`.

    Renvoie toujours un dict avec la forme canonique :
        {
            "last_run_at": str | None,
            "last_ping_at": str | None,
            "pinged_uids": {cat: [hash_key, …]},
        }

    Tolérant aux absences et aux corruptions : jamais lève, log + fallback vide.
    """
    p = Path(path)
    default = {"last_run_at": None, "last_ping_at": None, "pinged_uids": {}}
    if not p.exists():
        log.debug("ping_state absent à %s, baseline vide", p)
        return default
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("ping_state corrompu (%s) à %s, baseline vide", exc, p)
        return default
    if not isinstance(data, dict):
        log.warning("ping_state n'est pas un dict à %s, baseline vide", p)
        return default
    # Normalise les clés attendues (absences → None / {}).
    pinged = data.get("pinged_uids") or {}
    if not isinstance(pinged, dict):
        log.warning("ping_state.pinged_uids n'est pas un dict, reset")
        pinged = {}
    # Chaque valeur doit être une liste de strings. On tolère un set-like.
    clean: dict[str, list[str]] = {}
    for cat, uids in pinged.items():
        if not isinstance(cat, str):
            continue
        if not isinstance(uids, (list, tuple, set)):
            continue
        clean[cat] = sorted({str(u) for u in uids if u})
    return {
        "last_run_at": data.get("last_run_at"),
        "last_ping_at": data.get("last_ping_at"),
        "pinged_uids": clean,
    }


def save(
    path: str | Path,
    *,
    last_run_at: datetime | None = None,
    last_ping_at: datetime | None = None,
    pinged_uids: dict[str, Iterable[str]] | None = None,
) -> None:
    """Sérialise le state atomiquement (write tmp + rename).

    Les args `last_run_at` / `last_ping_at` / `pinged_uids` remplacent
    intégralement la valeur précédente : l'appelant est responsable du
    merge (c'est volontaire, ça évite des surprises du type "j'ai oublié
    de passer pinged_uids et il a été effacé par le rename").
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def _iso(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="seconds")

    payload = {
        "last_run_at": _iso(last_run_at),
        "last_ping_at": _iso(last_ping_at),
        "pinged_uids": {
            cat: sorted({str(u) for u in uids if u})
            for cat, uids in (pinged_uids or {}).items()
            if isinstance(cat, str)
        },
    }
    # Écriture atomique : write dans un tmp du même parent (même FS) + replace.
    fd, tmp_path = tempfile.mkstemp(
        prefix=".ping_state.", suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_path, p)
    except Exception:
        # Si l'écriture a foiré on nettoie le tmp pour ne pas polluer.
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def snapshot_from_rows(
    rows: Iterable[dict],
    categories: Iterable[str] = PING_CATEGORIES,
) -> dict[str, list[str]]:
    """Extrait `{cat: [hash_key, …]}` depuis une itérable de rows DB.

    Règles :
    - Ne retient que les rows dont `category ∈ categories`.
    - Ne retient que les rows matchés (matched_keywords JSON != "[]").
    - Le hash_key sert d'identifiant stable cross-runs
      (`f"{source_id}::{uid}"`, cf. `models.Item.hash_key`).
      La DB ne le stocke pas en colonne dédiée (c'est la PK) → on le
      recompose depuis `source_id` + `uid`, robuste au renommage des
      colonnes SQLite.

    La sortie est déterministe (listes triées) pour diff git stable.
    """
    cat_set = set(categories)
    buckets: dict[str, set[str]] = {c: set() for c in cat_set}
    for r in rows:
        cat = r.get("category")
        if cat not in cat_set:
            continue
        matched = r.get("matched_keywords") or "[]"
        # matched_keywords est stocké en JSON string par Store.upsert_many.
        # On tolère aussi les lists (tests en mémoire) et les bytes.
        if isinstance(matched, (bytes, bytearray)):
            matched = matched.decode("utf-8", errors="ignore")
        if isinstance(matched, str):
            s = matched.strip()
            if not s or s == "[]":
                continue
        elif isinstance(matched, (list, tuple)):
            if not matched:
                continue
        else:
            continue
        source_id = r.get("source_id") or ""
        uid = r.get("uid") or ""
        if not source_id or not uid:
            continue
        hk = r.get("hash_key") or f"{source_id}::{uid}"
        buckets[cat].add(hk)
    return {c: sorted(v) for c, v in buckets.items()}


def diff_new(
    current: dict[str, Iterable[str]],
    baseline: dict[str, Iterable[str]],
    categories: Iterable[str] = PING_CATEGORIES,
) -> dict[str, list[str]]:
    """Retourne les hash_keys présents dans `current` mais pas dans `baseline`.

    Filtré sur `categories`. Trié par catégorie puis par hash_key pour un
    rendu d'email déterministe.
    """
    out: dict[str, list[str]] = {}
    for cat in categories:
        cur = set(current.get(cat, []) or [])
        base = set(baseline.get(cat, []) or [])
        diff = sorted(cur - base)
        if diff:
            out[cat] = diff
    return out


def merge(
    baseline: dict[str, Iterable[str]],
    new_buckets: dict[str, Iterable[str]],
) -> dict[str, list[str]]:
    """Union des deux mappings. Utilisé pour mettre à jour `pinged_uids`
    après envoi d'un ping (on ajoute les nouveaux UIDs au set connu).

    Les catégories absentes de `new_buckets` sont conservées telles quelles.
    """
    out: dict[str, list[str]] = {
        c: sorted({str(u) for u in v if u})
        for c, v in (baseline or {}).items()
        if isinstance(c, str)
    }
    for cat, uids in (new_buckets or {}).items():
        if not isinstance(cat, str):
            continue
        s = set(out.get(cat, []))
        s.update(str(u) for u in uids if u)
        out[cat] = sorted(s)
    return out
