#!/bin/bash
set -e
REPO="/Users/cyrilmourin/Documents/Claude/Projects/Veille Parlementaire/veille-parlementaire-sport"
cd "$REPO"

echo ">>> Reset DB + run propre (via scripts/run_clean.sh)"
echo "    - reset DB integrale (backup auto)"
echo "    - pipeline --since 30 --no-email"
echo "    - verification dedup dosleg"
echo ""

bash scripts/run_clean.sh

echo ""
echo "Run termine. Log complet : /tmp/veille_run.log"
echo ""

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
