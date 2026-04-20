"""Point d'entrée du pipeline quotidien.

Usage :

    python -m src.main run             # pipeline complet
    python -m src.main run --no-email  # sans envoi SMTP
    python -m src.main run --since 7   # backfill 7 jours (pour le premier run)
    python -m src.main dry              # fetch + match uniquement, pas d'écriture

Variables d'environnement :
    PISTE_CLIENT_ID / PISTE_CLIENT_SECRET — Légifrance
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM — envoi email
    DIGEST_TO           — destinataire (défaut : cyrilmourin@sideline-conseil.fr)
    SITE_URL            — URL publique (défaut : https://veille.sideline-conseil.fr)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import digest, normalize, site_export
from .keywords import KeywordMatcher
from .store import Store

log = logging.getLogger("veille")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_SOURCES = ROOT / "config" / "sources.yml"
CONFIG_KEYWORDS = ROOT / "config" / "keywords.yml"
SQLITE_PATH = ROOT / "data" / "veille.sqlite3"
SITE_ROOT = ROOT / "site"
DIGEST_OUT = ROOT / "data" / "last_digest.html"

DEFAULT_TO = os.environ.get("DIGEST_TO", "cyrilmourin@sideline-conseil.fr")
DEFAULT_SITE_URL = os.environ.get("SITE_URL", "https://veille.sideline-conseil.fr")


def _setup_logging(verbose: bool = False):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def run(since_days: int = 1, send: bool = True, verbose: bool = False) -> int:
    """Pipeline complet. Renvoie le code de sortie (0 OK)."""
    _setup_logging(verbose)
    log.info("=== Veille parlementaire sport — %s ===", datetime.now().isoformat(timespec="seconds"))

    # 1. Fetch toutes les sources
    items, fetch_stats = normalize.run_all(CONFIG_SOURCES)

    # 2. Matching mots-clés
    matcher = KeywordMatcher(CONFIG_KEYWORDS)
    matcher.apply(items)
    matched = [it for it in items if it.matched_keywords]
    log.info("Matching : %d items matchés sur %d", len(matched), len(items))

    # 3. Persist
    store = Store(SQLITE_PATH)
    inserted = store.upsert_many(items)
    log.info("Store : %d nouveaux items insérés", inserted)

    # 4. Récupère les items à inclure dans le digest et l'export (matched uniquement)
    # `replace(tzinfo=None)` car fetch_matched_since compare à des `published_at` naïfs stockés en DB.
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).replace(tzinfo=None)
    digest_rows = store.fetch_matched_since(since, only_matched=True)
    log.info("Digest : %d items matchés sur les %d derniers jours", len(digest_rows), since_days)

    # 5. Email HTML
    html, total = digest.build_html(digest_rows, DEFAULT_SITE_URL)
    digest.save_html(html, DIGEST_OUT)
    log.info("Digest HTML écrit : %s (%d items)", DIGEST_OUT, total)

    if send:
        subject = f"Veille parlementaire sport — {datetime.now():%Y-%m-%d} ({total} nouveautés)"
        ok = digest.send_email(html, subject, DEFAULT_TO)
        if ok:
            log.info("Email envoyé à %s", DEFAULT_TO)
        else:
            log.warning("SMTP non configuré, email non envoyé (html dispo : %s)", DIGEST_OUT)
    else:
        log.info("Envoi désactivé (--no-email)")

    # 6. Export site statique — tous les items matchés (pas juste depuis `since`)
    all_matched = store.fetch_matched_since(datetime(1970, 1, 1), only_matched=True)
    summary = site_export.export(all_matched, SITE_ROOT)
    log.info("Site statique exporté : %s", summary)

    store.close()
    return 0


def dry(verbose: bool = False) -> int:
    """Fetch + match uniquement, pas de persistance ni d'envoi."""
    _setup_logging(verbose)
    items, stats = normalize.run_all(CONFIG_SOURCES)
    matcher = KeywordMatcher(CONFIG_KEYWORDS)
    matcher.apply(items)
    matched = [it for it in items if it.matched_keywords]
    log.info("=== Dry-run ===")
    log.info("Total items : %d", len(items))
    log.info("Matchés : %d", len(matched))
    by_cat: dict[str, int] = {}
    for it in matched:
        by_cat[it.category] = by_cat.get(it.category, 0) + 1
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        log.info("  %-25s %d", cat, n)
    for sid, s in sorted(stats.items()):
        err = f" ERR={s['error']}" if s.get("error") else ""
        log.info("  %-25s fetched=%-4d%s", sid, s["fetched"], err)
    return 0


def main():
    ap = argparse.ArgumentParser(prog="veille")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Pipeline complet")
    p_run.add_argument("--since", type=int, default=1, help="Fenêtre du digest en jours (défaut 1)")
    p_run.add_argument("--no-email", action="store_true", help="Ne pas envoyer le mail")
    p_run.add_argument("-v", "--verbose", action="store_true")

    p_dry = sub.add_parser("dry", help="Fetch + match uniquement")
    p_dry.add_argument("-v", "--verbose", action="store_true")

    args = ap.parse_args()

    if args.cmd == "run":
        sys.exit(run(since_days=args.since, send=not args.no_email, verbose=args.verbose))
    elif args.cmd == "dry":
        sys.exit(dry(verbose=args.verbose))


if __name__ == "__main__":
    main()
