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


if __name__ == "__main__":
    main()
