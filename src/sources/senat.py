"""Connecteur Sénat — open data (CSV/ZIP) + RSS actualités."""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Iterable

import feedparser

from ..models import Item
from ._common import fetch_bytes, fetch_text, parse_iso, unzip_members

log = logging.getLogger(__name__)

# --- mapping CSV ------------------------------------------------------------

# Les CSV Sénat sont en UTF-8 avec délimiteur ';' et guillemets double.
# Colonnes courantes (varient selon le fichier — on lit par nom).
SEP = ";"


def _read_csv(payload: bytes) -> Iterable[dict]:
    text = payload.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=SEP)
    for row in reader:
        yield {(k or "").strip(): (v or "").strip() for k, v in row.items()}


def fetch_source(src: dict) -> list[Item]:
    fmt = src.get("format")
    sid = src["id"]
    log.info("Fetch Sénat %s (%s)", sid, fmt)
    try:
        if fmt == "csv":
            payload = fetch_bytes(src["url"])
            rows = list(_read_csv(payload))
            return list(_normalize_rows(src, rows))
        if fmt == "csv_zip":
            data = fetch_bytes(src["url"])
            items: list[Item] = []
            for name, payload in unzip_members(data):
                if not name.lower().endswith(".csv"):
                    continue
                rows = list(_read_csv(payload))
                items.extend(_normalize_rows(src, rows, csv_name=name))
            return items
        if fmt == "rss":
            return _normalize_rss(src, fetch_text(src["url"]))
    except Exception as e:
        log.error("Sénat %s KO: %s", sid, e)
    return []


def _normalize_rows(src: dict, rows: list[dict], csv_name: str = "") -> Iterable[Item]:
    sid = src["id"]
    cat = src["category"]

    # Dossiers législatifs — colonnes « numero_initiative », « titre », « date_depot »…
    if sid in ("senat_dosleg", "senat_ppl", "senat_promulguees"):
        for r in rows:
            uid = r.get("numero_initiative") or r.get("numero") or r.get("id") or ""
            titre = r.get("titre") or r.get("intitule") or ""
            date = parse_iso(r.get("date_depot") or r.get("datePromulgation") or r.get("date"))
            if not uid or not titre:
                continue
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=titre[:220],
                url=r.get("url") or f"https://www.senat.fr/dossier-legislatif/{uid}.html",
                published_at=date, summary=titre[:500], raw=r,
            )

    elif sid == "senat_rapports":
        for r in rows:
            uid = r.get("numero") or r.get("id") or ""
            titre = r.get("titre") or ""
            if not uid or not titre:
                continue
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=f"Rapport n°{uid} — {titre}"[:220],
                url=r.get("url") or f"https://www.senat.fr/rap/{uid}.html",
                published_at=parse_iso(r.get("date")),
                summary=titre[:500], raw=r,
            )

    elif sid in ("senat_ameli",):
        for r in rows:
            uid = r.get("num_amdt") or r.get("numero") or r.get("id") or ""
            obj = r.get("objet") or r.get("titre") or ""
            disp = r.get("dispositif") or ""
            if not uid:
                continue
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=f"Amendement {uid}"[:220],
                url=r.get("url") or f"https://www.senat.fr/enseance/{uid}.html",
                published_at=parse_iso(r.get("date")),
                summary=(obj or disp)[:500], raw=r,
            )

    elif sid in ("senat_questions", "senat_qg", "senat_questions_1an"):
        for r in rows:
            uid = r.get("numQuestion") or r.get("numero") or r.get("id") or ""
            titre = r.get("titre") or r.get("objet") or r.get("texte") or ""
            if not uid or not titre:
                continue
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=titre[:220],
                url=r.get("url") or f"https://www.senat.fr/questions/base/{uid}.html",
                published_at=parse_iso(r.get("date") or r.get("datePublication")),
                summary=titre[:500], raw=r,
            )

    elif sid in ("senat_debats", "senat_cri"):
        for r in rows:
            uid = r.get("id") or r.get("numero") or r.get("date") or ""
            titre = r.get("titre") or r.get("sujet") or "Séance publique"
            if not uid:
                continue
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=titre[:220],
                url=r.get("url") or "https://www.senat.fr/seances/",
                published_at=parse_iso(r.get("date")),
                summary=titre[:500], raw=r,
            )


def _normalize_rss(src, text: str) -> list[Item]:
    d = feedparser.parse(text)
    out = []
    for e in d.entries:
        uid = getattr(e, "id", None) or getattr(e, "link", "")
        if not uid:
            continue
        dt = None
        if getattr(e, "published_parsed", None):
            dt = datetime(*e.published_parsed[:6])
        out.append(Item(
            source_id=src["id"], uid=uid, category=src["category"], chamber="Senat",
            title=(getattr(e, "title", "") or "")[:220],
            url=getattr(e, "link", ""),
            published_at=dt,
            summary=(getattr(e, "summary", "") or "")[:500],
            raw={},
        ))
    return out
