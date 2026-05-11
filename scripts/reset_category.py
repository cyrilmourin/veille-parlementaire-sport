#!/usr/bin/env python3
"""Purge ciblée d'une catégorie dans la DB SQLite.

Utilitaire à lancer après un patch de parser (ex. amendements) pour
forcer la ré-ingestion des items existants avec la nouvelle logique.

`upsert_many` dans src/store.py ignore les collisions de hash_key
(INSERT simple puis `except sqlite3.IntegrityError: pass`) — donc les
items déjà en base gardent leur ancien summary/matching même après
un patch côté connecteur. Ce script supprime ces items pour qu'ils
soient ré-insérés au prochain `python -m src.main run`.

Usage :
    python scripts/reset_category.py amendements
    python scripts/reset_category.py comptes_rendus --dry-run
    python scripts/reset_category.py amendements --yes    # non-interactif (CI)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "veille.sqlite3"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("category",
                    help="Catégorie à purger (ex. amendements, questions, "
                         "comptes_rendus, dossiers_legislatifs, agenda)")
    ap.add_argument("--source-id", default=None,
                    help="Restreindre à une source précise (ex. an_amendements)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Affiche le count sans supprimer")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="Ne demande pas confirmation (mode CI)")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB introuvable : {DB}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    where = "WHERE category = ?"
    params = [args.category]
    if args.source_id:
        where += " AND source_id = ?"
        params.append(args.source_id)

    (n,) = cur.execute(f"SELECT COUNT(*) FROM items {where}", params).fetchone()
    print(f"Items à purger : {n} (category={args.category}"
          + (f", source_id={args.source_id}" if args.source_id else "")
          + ")")

    if args.dry_run or n == 0:
        return

    if not args.yes:
        reply = input("Confirmer la suppression ? [y/N] ").strip().lower()
        if reply not in ("y", "yes", "o", "oui"):
            print("Abandonné.")
            return

    cur.execute(f"DELETE FROM items {where}", params)
    conn.commit()
    print(f"Supprimé : {cur.rowcount} items.")

    # R39-M (2026-04-25) — purger aussi les state files des scrapers
    # incrémentaux dont la catégorie correspond. Sans ça, le scraper
    # voit ses numéros déjà dans `scanned` et SKIP au prochain run :
    # les items purgés de la DB ne sont jamais re-créés.
    _purge_incremental_state(args.category, args.source_id)


def _purge_incremental_state(category: str, source_id: str | None) -> None:
    """Purge les state files des scrapers incrémentaux concernés par la
    catégorie. Aujourd'hui :
      - `data/an_cr_state.json` pour les CR AN
      - table SQLite `dosleg_text_cache` pour les dossiers législatifs
        (R42-AI : cache HTML `/dyn/opendata/` + `/leg/`).
    Idempotent : si le fichier/table n'existe pas, no-op."""
    if category == "comptes_rendus" and (
        not source_id or source_id == "an_cr_commissions"
    ):
        state_path = ROOT / "data" / "an_cr_state.json"
        if state_path.exists():
            try:
                state_path.unlink()
                print(f"State file purgé : {state_path}")
            except OSError as exc:
                print(f"Échec purge state {state_path} : {exc}",
                      file=sys.stderr)
    if category == "dossiers_legislatifs":
        # R42-AI : si on reset les dosleg, on vide aussi le cache HTML
        # texte intégral pour forcer un re-fetch propre. Sinon les items
        # ré-insérés piochent dans l'ancien cache et ne reflètent pas un
        # éventuel changement de parser/normalisation.
        try:
            sys.path.insert(0, str(ROOT))
            from src import text_haystack_cache as _hc
            restrict_source = None
            if source_id and source_id.startswith("senat"):
                restrict_source = _hc.SOURCE_SENAT
            elif source_id and source_id.startswith("an_"):
                restrict_source = _hc.SOURCE_AN
            n = _hc.purge_haystack_cache(DB, source=restrict_source)
            scope = restrict_source or "AN+Sénat"
            print(f"Cache haystack purgé : {n} entrées ({scope})")
        except Exception as exc:
            print(f"Échec purge dosleg_text_cache : {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
