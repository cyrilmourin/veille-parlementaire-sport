"""Utilitaires partagés par tous les connecteurs."""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

# UA type navigateur Chrome sur macOS — les sites .gouv.fr refusent
# systématiquement les UA déclaratifs de type bot (403). On garde une
# identité honnête via l'entête "From" pour signaler l'origine aux admins.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
CONTACT_EMAIL = "veille@sideline-conseil.fr"
CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# Timeouts granulaires : on sépare connect (SYN/handshake) du read (corps).
# Une source injoignable doit échouer en 8s de connect × 2 retries = 16s max,
# pas en 60s × 3 = 3 min comme dans l'ancienne version. Le read reste
# confortable (60s) pour les gros zips Sénat (CRI 537 Mo) et AN (agrégats
# questions/amendements).
_TIMEOUT_LIGHT = httpx.Timeout(connect=8.0, read=30.0, write=10.0, pool=5.0)
_TIMEOUT_HEAVY = httpx.Timeout(connect=10.0, read=120.0, write=15.0, pool=5.0)


def _client(timeout: httpx.Timeout = _TIMEOUT_LIGHT) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "From": CONTACT_EMAIL,
            "DNT": "1",
        },
    )


# Retry "léger" pour le scraping HTML : 2 tentatives suffisent. Une source
# morte ne mérite pas 3×60s = 3 min de latence dans la pipeline.
@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
def fetch_bytes(url: str) -> bytes:
    log.info("GET %s", url)
    with _client() as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


# Retry "lourd" pour les dumps AN/Sénat : 3 tentatives, backoff large, timeout
# read généreux. Les dumps sont gros (jusqu'à 537 Mo) et méritent plus de
# patience qu'une page HTML.
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def fetch_bytes_heavy(url: str) -> bytes:
    log.info("GET (heavy) %s", url)
    with _client(timeout=_TIMEOUT_HEAVY) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


def fetch_text(url: str) -> str:
    return fetch_bytes(url).decode("utf-8", errors="replace")


def unzip_members(payload: bytes) -> Iterable[tuple[str, bytes]]:
    """Itère sur (nom, contenu) des membres d'un zip en mémoire."""
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            yield name, zf.read(name)


def unzip_members_since(
    payload: bytes, since: datetime | None = None
) -> Iterable[tuple[str, datetime, bytes]]:
    """Itère sur (nom, date, contenu) des membres d'un zip, en ne
    décompressant QUE les entrées plus récentes que `since`.

    Utile pour les zips massifs (Sénat CRI/débats, 500+ Mo, 2800+ fichiers)
    où l'on ne veut ingérer que la fenêtre récente. La date vient de
    `ZipInfo.date_time` (stockée dans l'entrée zip, pas besoin d'extraire
    le fichier pour la connaître).

    Si `since` est None → comportement = `unzip_members` mais expose
    aussi la date.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        kept = 0
        dropped = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            # date_time = (year, month, day, hour, minute, second)
            try:
                dt = datetime(*info.date_time)
            except (ValueError, TypeError):
                dt = datetime(1970, 1, 1)
            if since is not None and dt < since:
                dropped += 1
                continue
            kept += 1
            yield info.filename, dt, zf.read(info.filename)
        if since is not None:
            log.info(
                "unzip_members_since : %d entrées gardées (>= %s), %d ignorées",
                kept, since.date().isoformat(), dropped,
            )


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # tolère "2026-04-18T12:34:56+00:00" et "2026-04-18"
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s)
    except Exception:
        return None
