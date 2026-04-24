#!/bin/bash
set -e
REPO="cyrilmourin/veille-parlementaire-sport"
KEEP_LAST=20

echo ">>> 1/5 verif gh CLI"
if ! command -v gh >/dev/null 2>&1; then
  echo "    ERREUR : gh CLI non installe."
  echo "    Installer via : brew install gh"
  read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "    ERREUR : gh non authentifie."
  echo "    Authentifier via : gh auth login"
  read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
  exit 1
fi
echo "    OK, $(gh --version | head -1)"

echo ""
echo ">>> 2/5 declenchement run daily.yml avec reset_db=1"
gh workflow run daily.yml -R "$REPO" --ref main \
  -f reset_db=1 \
  -f since_days=1 \
  -f no_email=0

echo "    dispatch envoye, attente 8s pour enregistrement GitHub..."
sleep 8

LATEST_RUN_ID=$(gh run list -R "$REPO" --workflow=daily.yml --limit 1 --json databaseId -q '.[0].databaseId')
echo "    run ID : $LATEST_RUN_ID"
echo "    https://github.com/$REPO/actions/runs/$LATEST_RUN_ID"

echo ""
echo ">>> 3/5 liste des runs completes (garder les $KEEP_LAST derniers)"
RUN_IDS_TO_DELETE=$(gh run list -R "$REPO" --limit 500 --status completed --json databaseId \
  -q ".[$KEEP_LAST:] | .[] | .databaseId")

if [[ -z "$RUN_IDS_TO_DELETE" ]]; then
  COUNT=0
else
  COUNT=$(echo "$RUN_IDS_TO_DELETE" | wc -l | tr -d ' ')
fi
echo "    $COUNT anciens runs a supprimer"

echo ""
echo ">>> 4/5 suppression"
if [[ "$COUNT" -gt 0 ]]; then
  DELETED=0
  FAILED=0
  while IFS= read -r run_id; do
    [[ -z "$run_id" ]] && continue
    if gh run delete "$run_id" -R "$REPO" >/dev/null 2>&1; then
      DELETED=$((DELETED + 1))
    else
      FAILED=$((FAILED + 1))
    fi
  done <<< "$RUN_IDS_TO_DELETE"
  echo "    supprimes : $DELETED"
  if [[ "$FAILED" -gt 0 ]]; then
    echo "    echecs : $FAILED (runs proteges ou deja supprimes)"
  fi
else
  echo "    rien a supprimer"
fi

echo ""
echo ">>> 5/5 ouverture du run en cours dans Chrome"
open -a "Google Chrome" "https://github.com/$REPO/actions/runs/$LATEST_RUN_ID"

echo ""
echo "Reset DB lance. Le run va :"
echo "  1. Purger data/veille.sqlite3"
echo "  2. Re-fetch toutes les sources enabled (fil-de-l eau, pas historique complet)"
echo "  3. Re-normaliser, re-exporter vers site/content/items/"
echo "  4. rm -rf public + hugo --minify (pages alpes_2030_news fantomes purgees)"
echo "  5. Deploy veille.sideline-conseil.fr"
echo ""
echo "Duree attendue : 10-20 min selon l a-coup des sources."
echo ""
read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
