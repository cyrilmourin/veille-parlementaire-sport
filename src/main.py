"""Point d'entrée du pipeline quotidien.

Usage :

    python -m src.main run             # pipeline complet
    python -m src.main run --no-email  # sans envoi SMTP
    python -m src.main run --since 7   # backfill 7 jours (pour le premier run)
    python -m src.main dry              # fetch + match uniquement, pas d'écriture
    python -m src.main ping            # ping 17h30 : email si nouveautés depuis matin

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

from . import digest, normalize, ping_state, site_export
from . import ping as ping_mod
from .keywords import KeywordMatcher
from .store import Store

log = logging.getLogger("veille")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_SOURCES = ROOT / "config" / "sources.yml"
CONFIG_KEYWORDS = ROOT / "config" / "keywords.yml"
SQLITE_PATH = ROOT / "data" / "veille.sqlite3"
SITE_ROOT = ROOT / "site"
DIGEST_OUT = ROOT / "data" / "last_digest.html"
PING_STATE_PATH = ROOT / "data" / "ping_state.json"

DEFAULT_TO = os.environ.get("DIGEST_TO", "cyrilmourin@sideline-conseil.fr")
DEFAULT_SITE_URL = os.environ.get("SITE_URL", "https://veille.sideline-conseil.fr")

# R25-H (2026-04-23) — bypass du filtre mots-clés pour les sources
# 100% sport : tout le flux institutionnel doit remonter au site et au
# digest même si un titre/chapô n'accroche aucun keyword (on ne veut pas
# rater une publi ANS sur un appel à projet juste parce que le terme
# "sport" n'apparaît pas dans le titre).
#
# Scope (demande Cyril) : publications uniquement (category == "communiques"),
# sources dont le cœur de métier EST déjà le sport :
#   - Opérateurs publics sport : ANS, INSEP, INJEP, AFLD
#   - Mouvement sportif RUP    : CNOSF, CPSF/France paralympique, FDSF
#   - MinSports (presse + actu) : tout le ministère est dans le scope
#
# Les autres sources (autorités généralistes type ARCOM/ANJ, autres
# ministères) gardent le filtre keywords — sinon on inonde le digest
# avec des communiqués hors sujet.
#
# Implémentation : on injecte un pseudo-keyword "(flux complet)" après
# matcher.apply pour les items concernés qui ont matched_keywords vide.
# Cela suffit à passer le filtre `matched_keywords != '[]'` dans
# store.fetch_matched_since(). Le libellé est visible côté site comme
# un kw-tag — voulu : Cyril saura que l'item est remonté "au titre de
# la source" et pas d'un mot-clé métier.
BYPASS_KEYWORDS_SOURCES: set[str] = {
    "ans",
    "insep",
    "injep",
    "afld",
    "cnosf",
    "france_paralympique",
    "fdsf",
    "min_sports_actualites",
    "min_sports_presse",
}
BYPASS_KEYWORD_LABEL = "(flux complet)"


def _apply_source_bypass(items) -> int:
    """R25-H : injecte le pseudo-keyword sur items de sources bypass sans match.

    Opère in-place. Retourne le nombre d'items enrichis (pour le log)."""
    enriched = 0
    for it in items:
        if getattr(it, "matched_keywords", None):
            continue
        source_id = (getattr(it, "source_id", "") or "").strip().lower()
        category = (getattr(it, "category", "") or "").strip().lower()
        if source_id in BYPASS_KEYWORDS_SOURCES and category == "communiques":
            it.matched_keywords = [BYPASS_KEYWORD_LABEL]
            # keyword_families reste vide : ce n'est pas un match thématique.
            enriched += 1
    return enriched


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
    # R25-H (2026-04-23) — bypass keywords pour sources 100% sport
    # (ANS, INSEP, INJEP, AFLD, CNOSF, CPSF, FDSF, MinSports). Voir
    # BYPASS_KEYWORDS_SOURCES en tête de module.
    bypassed = _apply_source_bypass(items)
    matched = [it for it in items if it.matched_keywords]
    log.info(
        "Matching : %d items matchés sur %d (dont %d via bypass source)",
        len(matched), len(items), bypassed,
    )

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

    # 7. R24 (2026-04-23) — snapshot ping : sauvegarde des hash_keys matchés des
    # 4 catégories chaudes (dossiers, amendements, questions, CR) pour que le
    # job ping-afternoon puisse détecter les nouveautés apparues après 4h.
    # Pas de filtre de date : on capture l'état *complet* du set matché pour
    # que le diff à 17h30 soit rigoureux (un item ré-upserté sans hash_key
    # neuf ne déclenchera pas de faux-positif).
    snapshot = ping_state.snapshot_from_rows(all_matched, ping_state.PING_CATEGORIES)
    ping_state.save(
        PING_STATE_PATH,
        last_run_at=datetime.now(timezone.utc),
        last_ping_at=None,
        pinged_uids=snapshot,
    )
    log.info(
        "Ping state écrit : %s (%s)",
        PING_STATE_PATH,
        ", ".join(f"{c}={len(v)}" for c, v in sorted(snapshot.items())),
    )

    store.close()
    return 0


def dry(verbose: bool = False) -> int:
    """Fetch + match uniquement, pas de persistance ni d'envoi."""
    _setup_logging(verbose)
    items, stats = normalize.run_all(CONFIG_SOURCES)
    matcher = KeywordMatcher(CONFIG_KEYWORDS)
    matcher.apply(items)
    # R25-H : même règle de bypass qu'en mode run, pour que `dry` reflète
    # fidèlement ce qui serait persisté et affiché sur le site.
    _apply_source_bypass(items)
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


def ping(send: bool = True, verbose: bool = False) -> int:
    """Ping 17h30 : lit la DB, compare avec l'état du matin (`ping_state.json`),
    envoie un email court si de nouveaux items matchés sont apparus dans les
    catégories prioritaires. Pas de fetch réseau — lecture DB uniquement.

    R24 (2026-04-23) — cron lundi-vendredi 17h30 Paris (15h30 UTC en été).
    """
    _setup_logging(verbose)
    log.info("=== Ping 17h30 — %s ===", datetime.now().isoformat(timespec="seconds"))
    return ping_mod.run_ping(
        db_path=SQLITE_PATH,
        state_path=PING_STATE_PATH,
        site_url=DEFAULT_SITE_URL,
        to=DEFAULT_TO,
        send=send,
    )


def main():
    ap = argparse.ArgumentParser(prog="veille")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Pipeline complet")
    p_run.add_argument("--since", type=int, default=1, help="Fenêtre du digest en jours (défaut 1)")
    p_run.add_argument("--no-email", action="store_true", help="Ne pas envoyer le mail")
    p_run.add_argument("-v", "--verbose", action="store_true")

    p_dry = sub.add_parser("dry", help="Fetch + match uniquement")
    p_dry.add_argument("-v", "--verbose", action="store_true")

    p_ping = sub.add_parser("ping", help="Ping 17h30 : email si nouveautés depuis matin")
    p_ping.add_argument("--no-email", action="store_true")
    p_ping.add_argument("-v", "--verbose", action="store_true")

    args = ap.parse_args()

    if args.cmd == "run":
        sys.exit(run(since_days=args.since, send=not args.no_email, verbose=args.verbose))
    elif args.cmd == "dry":
        sys.exit(dry(verbose=args.verbose))
    elif args.cmd == "ping":
        sys.exit(ping(send=not args.no_email, verbose=args.verbose))


if __name__ == "__main__":
    main()
