#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

echo "== Run workflow_dispatch avec reset_category=comptes_rendus =="
echo "   Purge les rows an_syceron / an_cr_commissions / senat_debats /"
echo "   senat_cri / senat_cr_culture et re-ingere avec le strip R38-A"
echo "   (main block + html.unescape + breadcrumb retire)."
echo ""
gh workflow run daily.yml -f reset_category=comptes_rendus -f since_days=1

echo ""
echo "== Workflow declenche. Suivi :"
sleep 3
gh run list --workflow=daily.yml --limit 3

echo ""
echo "== Auto-cleanup =="
rm -- "$0"
echo "Fait. ~5-8 min pour le refetch CR + deploy Pages."
