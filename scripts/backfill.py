#!/usr/bin/env python3
"""Premier run : backfill 7 jours et génération d'un digest initial.

Usage :
    python scripts/backfill.py            # fetch + match + export + email
    python scripts/backfill.py --no-email  # sans envoi SMTP
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.main import run  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    rc = run(since_days=7, send=not args.no_email, verbose=args.verbose)
    sys.exit(rc)


if __name__ == "__main__":
    main()
