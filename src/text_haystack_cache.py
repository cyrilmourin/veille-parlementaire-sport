"""Cache SQLite des textes intégraux de dossiers législatifs (R42-AI).

Évite de re-fetcher à chaque run :
  - AN  : `https://www.assemblee-nationale.fr/dyn/opendata/<TEXTE_REF>.html`
  - Sénat : `https://www.senat.fr/leg/<slug>.html`

Ces pages sont quasi-immutables (publiées à T puis figées). En mode
`nominal`, 588 fetches AN + 154 Sénat = 742 fetches lourds par run alors
que la majorité du contenu est inchangé depuis la veille — voir analyse
du run 25659789715 (cf. HANDOFF R42-AI).

Stratégie :
- 1 table `dosleg_text_cache (source, ref) -> haystack` dans la SQLite
  principale (`data/veille.sqlite3`), partagée avec le cache GHA.
- TTL court (14 j) pour les dossiers actifs (un nouveau rapport peut
  ré-publier un texte modifié sous la même ref dans cette fenêtre).
- TTL ∞ pour les promulgués (texte gelé légalement, JO publié).
- Validation min 500 chars avant cache pour éviter d'empoisonner sur
  une page d'erreur HTML 200 OK (maintenance, WAF) — le caller re-fetch
  au run suivant.

Le module est volontairement minimal et soft-fail : toute erreur SQLite
log un debug et retourne `None` / no-op, sans casser le pipeline.

Purge : `scripts/reset_category.py dossiers_legislatifs` vide la table.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("text_haystack_cache")


def _is_disabled() -> bool:
    """Permet de couper le cache via env var (tests, debug).
    `VEILLE_DOSLEG_TEXT_CACHE_DISABLE=1` → toutes les lectures retournent
    None (cache miss) et toutes les écritures sont no-op. Utilisé par le
    conftest pytest pour étanchéifier les tests existants R42-L/R42-X
    qui fetchent en mock et ne doivent pas polluer `data/veille.sqlite3`.
    """
    return os.environ.get("VEILLE_DOSLEG_TEXT_CACHE_DISABLE") == "1"


SCHEMA = """
CREATE TABLE IF NOT EXISTS dosleg_text_cache (
    source         TEXT NOT NULL,
    ref            TEXT NOT NULL,
    haystack       TEXT NOT NULL,
    is_promulgated INTEGER NOT NULL DEFAULT 0,
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (source, ref)
);
CREATE INDEX IF NOT EXISTS idx_dosleg_text_cache_fetched
  ON dosleg_text_cache(fetched_at);
"""

SOURCE_AN = "an_dosleg"
SOURCE_SENAT = "senat_dosleg"
VALID_SOURCES = (SOURCE_AN, SOURCE_SENAT)

# TTL en jours pour les dossiers actifs (non promulgués). Au-delà, on
# re-fetch pour capter d'éventuels corrigendum/réécritures.
TTL_ACTIVE_DAYS = 14

# Seuil min de chars pour qu'un haystack soit considéré comme valide.
# En-dessous on suspecte une page d'erreur (WAF, maintenance, 200 OK
# avec body vide) et on ne cache PAS — un re-fetch sera tenté au run
# suivant.
MIN_VALID_LEN = 500


_STAT_KEYS = (
    "hits",
    "miss_absent",
    "miss_expired",
    "put",
    "put_rejected_too_short",
    "put_errors",
)
_STATS: dict[str, dict[str, int]] = {
    SOURCE_AN: {k: 0 for k in _STAT_KEYS},
    SOURCE_SENAT: {k: 0 for k in _STAT_KEYS},
}


def reset_stats() -> None:
    """Remet tous les compteurs à zéro. Idempotent."""
    for src in _STATS:
        for k in _STATS[src]:
            _STATS[src][k] = 0


def get_stats(source: Optional[str] = None) -> dict[str, int] | dict[str, dict[str, int]]:
    """Snapshot des compteurs. Si `source` est précisé, retourne le dict
    plat de cette source. Sinon retourne le dict imbriqué {source: stats}."""
    if source is not None:
        return dict(_STATS.get(source, {k: 0 for k in _STAT_KEYS}))
    return {src: dict(_STATS[src]) for src in _STATS}


def _bump(source: str, key: str) -> None:
    if source in _STATS and key in _STATS[source]:
        _STATS[source][key] += 1


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Crée la table si absente (idempotent)."""
    conn.executescript(SCHEMA)


def _open(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    _ensure_schema(conn)
    return conn


def get_cached_haystack(
    db_path: Path | str,
    source: str,
    ref: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Retourne le haystack cached pour `(source, ref)` si présent ET valide
    (TTL non expiré pour les actifs, infini pour les promulgués).

    Retourne `None` en cas de cache miss, TTL expiré, ou erreur SQLite
    (soft-fail). Le caller doit fetch live et appeler `put_cached_haystack`
    pour rafraîchir.
    """
    if source not in VALID_SOURCES or not ref:
        return None
    if _is_disabled():
        return None
    now = now or datetime.utcnow()
    try:
        with _open(db_path) as conn:
            row = conn.execute(
                "SELECT haystack, is_promulgated, fetched_at "
                "FROM dosleg_text_cache WHERE source = ? AND ref = ?",
                (source, ref),
            ).fetchone()
    except sqlite3.Error as exc:
        log.debug("get_cached_haystack(%s, %s) KO : %s", source, ref, exc)
        return None
    if row is None:
        _bump(source, "miss_absent")
        return None
    haystack, is_promulgated, fetched_at = row
    if is_promulgated:
        _bump(source, "hits")
        return haystack
    try:
        fetched_dt = datetime.fromisoformat(fetched_at)
    except ValueError:
        _bump(source, "miss_expired")
        return None
    if now - fetched_dt > timedelta(days=TTL_ACTIVE_DAYS):
        _bump(source, "miss_expired")
        return None
    _bump(source, "hits")
    return haystack


def put_cached_haystack(
    db_path: Path | str,
    source: str,
    ref: str,
    haystack: str,
    *,
    is_promulgated: bool = False,
    now: Optional[datetime] = None,
) -> bool:
    """Persiste `haystack` pour `(source, ref)`. Refuse si len < MIN_VALID_LEN
    (cache empoisonné suspecté). Retourne True si écrit, False sinon.
    Soft-fail sur erreur SQLite (log debug + False)."""
    if source not in VALID_SOURCES or not ref:
        return False
    if _is_disabled():
        return False
    if len(haystack) < MIN_VALID_LEN:
        _bump(source, "put_rejected_too_short")
        return False
    now = now or datetime.utcnow()
    try:
        with _open(db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dosleg_text_cache "
                "(source, ref, haystack, is_promulgated, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source, ref, haystack, 1 if is_promulgated else 0,
                 now.isoformat(timespec="seconds")),
            )
            conn.commit()
    except sqlite3.Error as exc:
        _bump(source, "put_errors")
        log.debug("put_cached_haystack(%s, %s) KO : %s", source, ref, exc)
        return False
    _bump(source, "put")
    return True


def purge_haystack_cache(
    db_path: Path | str,
    source: Optional[str] = None,
) -> int:
    """Vide la table (ou la restreint à une source). Retourne le nombre de
    rows supprimées. Appelé par `scripts/reset_category.py` quand on reset
    `dossiers_legislatifs`."""
    try:
        with _open(db_path) as conn:
            if source:
                cur = conn.execute(
                    "DELETE FROM dosleg_text_cache WHERE source = ?",
                    (source,),
                )
            else:
                cur = conn.execute("DELETE FROM dosleg_text_cache")
            conn.commit()
            return cur.rowcount
    except sqlite3.Error as exc:
        log.debug("purge_haystack_cache(%s) KO : %s", source, exc)
        return 0
