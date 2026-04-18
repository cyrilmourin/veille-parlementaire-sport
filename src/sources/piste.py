"""Connecteur Légifrance / JORF — API PISTE (OAuth2 client_credentials).

Credentials attendus dans l'environnement :
    PISTE_CLIENT_ID
    PISTE_CLIENT_SECRET

Si l'un d'eux manque, le connecteur renvoie [] silencieusement pour ne pas
bloquer le pipeline quotidien. Le scaffolding est prêt pour l'intégration.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

import httpx

from ..models import Item
from ._common import USER_AGENT, parse_iso

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth.piste.gouv.fr/api/oauth/token"
API_BASE = "https://api.piste.gouv.fr/dila/legifrance/lf-engine-app"


class PisteClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._token_exp: float = 0

    def _token_get(self) -> str:
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        r = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "openid",
            },
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        j = r.json()
        self._token = j["access_token"]
        self._token_exp = time.time() + int(j.get("expires_in", 3600))
        return self._token

    def post(self, path: str, payload: dict) -> dict:
        tok = self._token_get()
        r = httpx.post(
            API_BASE + path,
            json=payload,
            headers={"Authorization": f"Bearer {tok}", "User-Agent": USER_AGENT,
                     "Content-Type": "application/json", "Accept": "application/json"},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()


def fetch_source(src: dict) -> list[Item]:
    cid = os.environ.get("PISTE_CLIENT_ID")
    csec = os.environ.get("PISTE_CLIENT_SECRET")
    if not cid or not csec:
        log.info("PISTE credentials absents — %s sauté", src["id"])
        return []

    client = PisteClient(cid, csec)
    since = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
    until = datetime.now().strftime("%Y-%m-%d")

    # On privilégie jorfSearch qui renvoie une liste paginée
    payload = {
        "recherche": {
            "fromAdvancedRecherche": False,
            "pageSize": 50, "pageNumber": 1,
            "sort": "PUBLICATION_DATE_DESC",
            "typePagination": "DEFAUT",
            "champs": [{"typeChamp": "ALL", "criteres": [{"valeur": "", "operateur": "ET"}]}],
            "filtres": [{"facette": "DATE_PUBLICATION",
                          "dates": {"start": since, "end": until}}],
        },
        "fond": "JORF",
    }
    try:
        resp = client.post("/search", payload)
    except Exception as e:
        log.error("PISTE %s KO : %s", src["id"], e)
        return []

    out: list[Item] = []
    results = (resp or {}).get("results", [])
    for r in results:
        titles = r.get("titles") or []
        title = (titles[0].get("title") if titles else "") or r.get("title") or ""
        url = ""
        if titles and titles[0].get("id"):
            url = f"https://www.legifrance.gouv.fr/jorf/id/{titles[0]['id']}"
        nature = (r.get("nature") or "").lower()
        cat = "nominations" if "nomination" in nature else "jorf"
        out.append(Item(
            source_id=src["id"], uid=r.get("id") or url or title[:80],
            category=cat, chamber="JORF",
            title=title[:220], url=url or "https://www.legifrance.gouv.fr/",
            published_at=parse_iso(r.get("dateDebut") or r.get("datePublication")),
            summary=(r.get("text") or "")[:500], raw=r,
        ))
    log.info("PISTE %s : %d items", src["id"], len(out))
    return out
