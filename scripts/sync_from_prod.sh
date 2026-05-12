#!/usr/bin/env bash
# R42-AU (2026-05-12) — Synchronise la DB SQLite locale depuis la prod.
#
# Récupère l'artifact `veille-db-latest` du dernier run réussi de
# `daily.yml` et l'installe dans `data/veille.sqlite3` (écrase le local
# sans confirmation). Ne déclenche AUCUN nouveau run GHA — utilise les
# artifacts déjà produits par les daily naturels.
#
# Pré-requis :
#   - `gh` CLI authentifié (`gh auth status`)
#   - artifact `veille-db-latest` produit par au moins un daily depuis
#     R42-AU (sinon le script tombe en erreur explicite)
#
# Usage :
#   bash scripts/sync_from_prod.sh
#
# Après sync :
#   - `python -m src.main export -v` régénère les .md Hugo + meta.json
#   - `cd site && hugo server` pour preview locale à jour
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="$REPO_ROOT/data/veille.sqlite3"
ARTIFACT_NAME="veille-db-latest"
WORKFLOW="daily.yml"

cd "$REPO_ROOT"

if ! command -v gh >/dev/null 2>&1; then
    echo "❌ gh CLI introuvable. Installer : https://cli.github.com/" >&2
    exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "❌ gh non authentifié. Lancer : gh auth login" >&2
    exit 1
fi

echo "🔍 Recherche du dernier daily.yml réussi avec artifact '$ARTIFACT_NAME'…"

# On parcourt les 20 derniers runs daily.yml success et on prend le
# 1er qui contient l'artifact (pour gérer le cas où un run pré-R42-AU
# n'a pas l'artifact).
RUN_ID=""
for candidate in $(gh run list \
        --workflow "$WORKFLOW" \
        --status success \
        --limit 20 \
        --json databaseId \
        --jq '.[].databaseId'); do
    if gh api "repos/{owner}/{repo}/actions/runs/$candidate/artifacts" \
            --jq ".artifacts[] | select(.name == \"$ARTIFACT_NAME\") | .id" \
            2>/dev/null | grep -q .; then
        RUN_ID="$candidate"
        break
    fi
done

if [ -z "$RUN_ID" ]; then
    echo "❌ Aucun artifact '$ARTIFACT_NAME' trouvé sur les 20 derniers runs success de $WORKFLOW." >&2
    echo "   (R42-AU vient d'être ajouté ? Attendre le prochain daily naturel.)" >&2
    exit 2
fi

CREATED=$(gh run view "$RUN_ID" --json createdAt -q '.createdAt')
echo "✓ Run trouvé : id=$RUN_ID créé le $CREATED"

# Backup local au cas où
if [ -f "$DB_PATH" ]; then
    BACKUP="${DB_PATH}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    echo "💾 Backup DB locale : $BACKUP"
    cp "$DB_PATH" "$BACKUP"
fi

# Téléchargement (gh download écrase si destination existe)
TMP_DIR="$(mktemp -d)"
trap "rm -rf '$TMP_DIR'" EXIT
echo "⬇️  Download artifact dans $TMP_DIR …"
gh run download "$RUN_ID" -n "$ARTIFACT_NAME" -D "$TMP_DIR" >/dev/null

if [ ! -f "$TMP_DIR/veille.sqlite3" ]; then
    echo "❌ Artifact téléchargé mais veille.sqlite3 introuvable dans $TMP_DIR" >&2
    ls -la "$TMP_DIR" >&2
    exit 3
fi

# Écrase la DB locale
mv "$TMP_DIR/veille.sqlite3" "$DB_PATH"
SIZE=$(stat -f%z "$DB_PATH" 2>/dev/null || stat -c%s "$DB_PATH")
SIZE_MB=$(( SIZE / 1024 / 1024 ))
echo "✅ DB locale écrasée : $DB_PATH (${SIZE_MB} Mo)"

# Stats minimales pour confirmer la fraîcheur
if command -v sqlite3 >/dev/null 2>&1; then
    LAST=$(sqlite3 "$DB_PATH" "SELECT max(inserted_at) FROM items;" 2>/dev/null || echo "?")
    COUNT=$(sqlite3 "$DB_PATH" "SELECT count(*) FROM items;" 2>/dev/null || echo "?")
    echo "📊 Items en DB : $COUNT | dernier inserted_at : $LAST"
fi

echo ""
echo "Prochaine étape pour preview locale :"
echo "  source .venv/bin/activate"
echo "  python -m src.main export -v"
echo "  cd site && hugo server"
