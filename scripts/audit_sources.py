#!/usr/bin/env python3
"""Audit rapide des sources : ping HEAD + taille de réponse.

Utile pour détecter les URLs cassées (404, redirects) après un changement de
site officiel.

Usage :
    python scripts/audit_sources.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "sources.yml"


def audit():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    rows = []
    for group_name, group in cfg.items():
        if not isinstance(group, dict):
            continue
        for src in group.get("sources", []) or []:
            url = src.get("url")
            if not url:
                continue
            sid = src["id"]
            try:
                r = httpx.head(
                    url,
                    timeout=15,
                    follow_redirects=True,
                    headers={"User-Agent": "SidelineVeilleBot/0.1"},
                )
                rows.append((sid, r.status_code, r.headers.get("content-length", "?"), url))
            except Exception as e:
                rows.append((sid, "ERR", str(e)[:40], url))

    width = max(len(r[0]) for r in rows)
    print(f"{'source':<{width}}  status  size     url")
    print("-" * (width + 60))
    fails = 0
    for sid, st, size, url in rows:
        flag = " " if isinstance(st, int) and 200 <= st < 400 else "!"
        if flag == "!":
            fails += 1
        print(f"{flag} {sid:<{width}}  {st}    {size:<8}  {url}")

    print(f"\n{len(rows)} sources — {fails} en erreur")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(audit())
