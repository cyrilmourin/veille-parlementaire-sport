#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

echo "== Derniers runs R36 (10 derniers) =="
gh run list --workflow=daily.yml --limit 10

echo ""
echo "== Trouver le run du commit f40a5bc =="
RUN_ID=$(gh run list --workflow=daily.yml --limit 20 --json databaseId,headSha,conclusion \
  --jq '.[] | select(.headSha | startswith("f40a5bc")) | .databaseId' | head -1)

if [[ -z "$RUN_ID" ]]; then
  echo "Aucun run avec SHA f40a5bc trouve. Derniers runs avec leurs SHA :"
  gh run list --workflow=daily.yml --limit 10 --json databaseId,headSha,conclusion,displayTitle \
    --jq '.[] | "\(.databaseId)  \(.headSha[0:8])  \(.conclusion // "?")  \(.displayTitle)"'
  exit 1
fi

echo "Run trouve : $RUN_ID"
echo ""
echo "== Summary =="
gh run view "$RUN_ID" --exit-status=false

echo ""
echo "== Logs des jobs echoues (tail) =="
gh run view "$RUN_ID" --log-failed 2>&1 | tail -60

echo ""
echo "== Auto-cleanup =="
rm -- "$0"
