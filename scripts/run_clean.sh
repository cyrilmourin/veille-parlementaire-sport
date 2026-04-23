#!/usr/bin/env bash
# run_clean.sh — Run propre avec reset DB + vérification dédup dossiers législatifs
#
# Usage :
#   bash scripts/run_clean.sh              # reset DB + run 30 jours + vérif
#   bash scripts/run_clean.sh --no-reset   # run sans reset (juste vérif)
#   bash scripts/run_clean.sh --since 7   # fenêtre 7 jours
#
# Ce script :
#   1. Commit et push les changements locaux en attente
#   2. Purge la DB SQLite (sauf --no-reset)
#   3. Lance python -m src.main run --no-email --since <N>
#   4. Vérifie que le dédup dossiers législatifs (AN↔Sénat par ID) fonctionne
#   5. Affiche un rapport de dédup concis

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# ── Python : priorité au venv du repo, sinon python3 système ───────────────
# R22+ (2026-04-23) : macOS moderne n'aliase pas `python` → `python3`.
# Avant ce fix, la ligne `python -m src.main run` plantait
# `command not found` et le reset DB restait sans DB régénérée.
if [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
  PYTHON="$REPO_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "ERREUR : aucun Python disponible (ni .venv/bin/python ni python3 système)" >&2
  exit 1
fi

# ── Couleurs ───────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "${RED}✗${NC}  $*" >&2; }
step() { echo -e "\n${BLUE}▶${NC}  $*"; }

# ── Arguments ──────────────────────────────────────────────────────────────
RESET_DB=1
SINCE=30
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-reset)  RESET_DB=0; shift ;;
    --since)     SINCE="$2"; shift 2 ;;
    *) err "Argument inconnu : $1"; exit 1 ;;
  esac
done

# ── 1. Commit + push des changements locaux ────────────────────────────────
step "Vérification de l'état git"
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
  warn "Des fichiers non commités sont présents — ils seront ignorés par le run."
  git status --short
fi

# Supprimer les lock files orphelins si présents
for lockfile in .git/index.lock .git/HEAD.lock .git/refs/heads/main.lock; do
  if [[ -f "$lockfile" ]]; then
    warn "Lock git orphelin trouvé : $lockfile — suppression"
    rm -f "$lockfile" && ok "Lock supprimé" || warn "Impossible de supprimer (FUSE ?)"
  fi
done

AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
if [[ "$AHEAD" -gt 0 ]]; then
  step "Push des $AHEAD commit(s) locaux en attente"
  git push origin main && ok "Push OK" || { err "Push échoué — continue sans push"; }
else
  ok "Repo à jour avec origin/main"
fi

# ── 2. Reset DB ────────────────────────────────────────────────────────────
if [[ "$RESET_DB" -eq 1 ]]; then
  step "Reset DB SQLite (purge intégrale)"
  DB="data/veille.sqlite3"
  if [[ -f "$DB" ]]; then
    cp "$DB" "${DB}.bak.$(date +%Y%m%d_%H%M%S)" && ok "Backup créé : ${DB}.bak.*"
    rm -f "$DB" && ok "DB purgée"
  else
    warn "Aucune DB existante — premier run"
  fi
  # Nettoyer aussi le cache de recherche
  rm -f site/static/search_index.json && true
else
  warn "Mode --no-reset : DB conservée"
fi

# ── 3. Run pipeline ────────────────────────────────────────────────────────
step "Lancement pipeline (--since $SINCE --no-email)"
step "Python utilisé : $PYTHON"
"$PYTHON" -m src.main run --since "$SINCE" --no-email -v 2>&1 | tee /tmp/veille_run.log

EXIT_CODE=${PIPESTATUS[0]}
if [[ "$EXIT_CODE" -ne 0 ]]; then
  err "Pipeline terminé avec code $EXIT_CODE — vérifier /tmp/veille_run.log"
  exit "$EXIT_CODE"
fi
ok "Pipeline terminé sans erreur"

# ── 4. Vérification dédup dossiers législatifs ─────────────────────────────
step "Vérification dédup dossiers législatifs"

"$PYTHON" - <<'PYEOF'
import sqlite3, json, re, sys
from pathlib import Path

DB = Path("data/veille.sqlite3")
if not DB.exists():
    print("  DB introuvable — skip vérification")
    sys.exit(0)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT source_id, uid, title, url, raw FROM items "
    "WHERE category='dossiers_legislatifs' AND matched_keywords!='[]' "
    "ORDER BY published_at DESC"
).fetchall()

if not rows:
    print("  Aucun dossier législatif matché — check keywords")
    sys.exit(0)

print(f"  {len(rows)} dossiers matchés au total")

# Chercher des potentiels doublons par titre
by_title: dict[str, list] = {}
for r in rows:
    key = re.sub(r'\s+', ' ', (r['title'] or '').lower().strip())[:80]
    by_title.setdefault(key, []).append(r)

dupes = {k: v for k, v in by_title.items() if len(v) > 1}
if dupes:
    print(f"\n  ⚠  {len(dupes)} titre(s) avec doublons potentiels :")
    for title, items in list(dupes.items())[:5]:
        print(f"    « {title[:60]}... »")
        for it in items:
            raw = json.loads(it['raw'] or '{}')
            did = raw.get('dossier_id') or raw.get('signet') or '—'
            print(f"      [{it['source_id']}] id={did} url={it['url'][:60]}")
else:
    print("  ✓ Aucun doublon de titre détecté")

# Vérifier spécifiquement la fusion AN↔Sénat via dossier_id
print("\n  Vérification fusion AN↔Sénat par ID :")
an_ids: dict[str, str] = {}
senat_ids: dict[str, str] = {}
for r in rows:
    raw = json.loads(r['raw'] or '{}')
    src = r['source_id']
    did = raw.get('dossier_id') or raw.get('signet') or ''
    url_an = raw.get('url_an') or ''
    if src.startswith('an_') and did:
        an_ids[did.upper()] = r['title']
    elif 'senat' in src and did:
        senat_ids[did.lower()] = r['title']
        # Extraire l'ID AN depuis url_an si dispo
        m = re.search(r'/(DLR5L\w+)', url_an, re.I)
        if m:
            an_ids_from_senat = m.group(1).upper()
            if an_ids_from_senat in an_ids:
                print(f"    ✓ Fusion OK : {did} ↔ {an_ids_from_senat} (url_an)")
            else:
                print(f"    ~ Sénat {did} → AN {an_ids_from_senat} (pas de doublons, fusion non nécessaire)")

shared = set(k.upper() for k in senat_ids) & set(an_ids)
if not shared:
    print("    ✓ Aucun doublon AN↔Sénat détecté — dédup propre")

# Aperçu des 5 premiers dossiers
print(f"\n  Premiers dossiers exportés :")
for r in rows[:5]:
    raw = json.loads(r['raw'] or '{}')
    did = raw.get('dossier_id') or raw.get('signet') or '—'
    print(f"    [{r['source_id'][:20]}] {r['title'][:55]} (id={did})")
PYEOF

# ── 5. Résumé ──────────────────────────────────────────────────────────────
step "Résumé"
ERRORS=$(grep -c "ERREUR\|ERROR\|Exception\|Traceback" /tmp/veille_run.log 2>/dev/null || true)
ITEMS=$(grep -oE "[0-9]+ items matchés" /tmp/veille_run.log 2>/dev/null | tail -1 || echo "?")
echo "  Items matchés  : $ITEMS"
echo "  Erreurs log    : $ERRORS"
echo "  Log complet    : /tmp/veille_run.log"
[[ "$ERRORS" -gt 0 ]] && warn "Des erreurs sont présentes dans le log — vérifier" || ok "Run propre"
