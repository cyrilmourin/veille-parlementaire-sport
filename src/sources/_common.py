"""Utilitaires partagés par tous les connecteurs."""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Politique de retry : on retry les erreurs réseau / 5xx, jamais les 4xx.

    Une URL qui renvoie 404 (ex. dump renommé côté producteur, cf. R11d)
    ne se mettra pas à répondre 200 au 2e essai — les 3 retries de tenacity
    consommaient 16+ secondes pour rien et masquaient le diagnostic. Les
    timeouts, RemoteProtocolError, ConnectError, etc. restent retryables
    (transitoires côté réseau).
    """
    if isinstance(exc, httpx.HTTPStatusError):
        # 5xx = côté serveur, mérite un retry. 4xx = côté URL, abandon.
        return exc.response.status_code >= 500
    return True

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


def _raise_for_status_loud(r: httpx.Response) -> None:
    """Wrapper sur `raise_for_status` qui loggue explicitement le 4xx/5xx.

    Sans ça, un 404 silencieux remontait juste en traceback générique côté
    `normalize._fetch_one`, noyé dans les DEBUG httpcore — c'est ce qui a
    laissé `an_amendements = 0 items` inaperçu entre R11 et R11d. On log
    au moment où l'erreur survient, avec l'URL complète et le code.
    """
    if r.is_success:
        return
    log.error(
        "HTTP %d sur GET %s — %s",
        r.status_code, r.url, r.reason_phrase or "(no reason)"
    )
    r.raise_for_status()


# Retry "léger" pour le scraping HTML : 2 tentatives suffisent. Une source
# morte ne mérite pas 3×60s = 3 min de latence dans la pipeline. On ne retry
# PAS sur 4xx (cf. `_is_retryable`).
@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(min=1, max=5),
    retry=retry_if_exception(_is_retryable),
)
def fetch_bytes(url: str) -> bytes:
    log.info("GET %s", url)
    with _client() as c:
        r = c.get(url)
        _raise_for_status_loud(r)
        return r.content


# Retry "lourd" pour les dumps AN/Sénat : 3 tentatives, backoff large, timeout
# read généreux. Les dumps sont gros (jusqu'à 537 Mo) et méritent plus de
# patience qu'une page HTML. Pareil : pas de retry sur 4xx.
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception(_is_retryable),
)
def fetch_bytes_heavy(url: str) -> bytes:
    log.info("GET (heavy) %s", url)
    with _client(timeout=_TIMEOUT_HEAVY) as c:
        r = c.get(url)
        _raise_for_status_loud(r)
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


# --- Extraction de thème depuis un compte rendu --------------------------
# Heuristiques pour deviner l'objet d'une séance (AN ou Sénat) à partir du
# texte brut de son CR, sans parser le XML propriétaire. Cyril veut que le
# titre évoque le thème du débat, pas juste "Séance du JJ/MM/AAAA".
import re as _re_theme

_THEMES_RE = _re_theme.compile(
    r"("
    r"discussion (?:générale )?(?:du|de la|des) (?:projet|proposition|texte)[^\.\n;]{5,160}|"
    r"examen (?:du|de la|des) [^\.\n;]{5,160}|"
    r"projet de loi (?:relatif|de finances|de programmation|portant|autorisant|ratifiant)[^\.\n;]{5,160}|"
    r"proposition de loi (?:relative|portant|tendant|visant|ratifiant)[^\.\n;]{5,160}|"
    r"proposition de résolution[^\.\n;]{5,160}|"
    r"ordre du jour\s*:\s*[^\.\n;]{5,180}|"
    r"questions? (?:au gouvernement|d'actualité)[^\.\n;]{0,100}|"
    r"déclaration du gouvernement (?:relative|sur)[^\.\n;]{5,160}"
    r")",
    _re_theme.IGNORECASE,
)


def extract_cr_theme(text: str | None, max_len: int = 110) -> str:
    """Extrait un libellé de thème pertinent depuis le texte d'un compte rendu.

    Cherche dans les 8000 premiers caractères les patterns typiques d'ordre
    du jour ou d'objet de séance (« Discussion du projet de loi relatif à… »,
    « Questions au gouvernement », « Examen du rapport sur… »). Renvoie ""
    si aucun pattern reconnu.
    """
    if not text:
        return ""
    sample = text[:8000]
    m = _THEMES_RE.search(sample)
    if not m:
        return ""
    theme = _re_theme.sub(r"\s+", " ", m.group(1)).strip(" .,;:—-")
    if not theme:
        return ""
    # Capitalise la 1re lettre, préserve les sigles / accents.
    theme = theme[0].upper() + theme[1:]
    if len(theme) > max_len:
        theme = theme[:max_len].rsplit(" ", 1)[0] + "…"
    return theme


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
