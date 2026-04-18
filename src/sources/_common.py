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

USER_AGENT = "SidelineVeilleBot/0.1 (+https://veille.sideline-conseil.fr)"
CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=20))
def fetch_bytes(url: str) -> bytes:
    log.info("GET %s", url)
    with _client() as c:
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
