#!/bin/bash
# Finition R19 — pull rebase + push
set -e
REPO="/Users/cyrilmourin/Documents/Claude/Projects/Veille Parlementaire/veille-parlementaire-sport"
cd "$REPO"

echo ">>> 1/3 git fetch + rebase sur origin/main"
git fetch origin main
git rebase origin/main

echo ">>> 2/3 git push"
git -c http.postBuffer=524288000 push origin main

echo ""
echo "✅ R19 pushé sur origin/main."
echo "→ Ouverture Chrome sur Actions…"
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer…"
