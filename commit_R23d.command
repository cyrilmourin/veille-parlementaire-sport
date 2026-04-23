#!/bin/bash
set -e
REPO="/Users/cyrilmourin/Documents/Claude/Projects/Veille Parlementaire/veille-parlementaire-sport"
cd "$REPO"

echo ">>> 0/6 nettoyage locks git orphelins"
for lockfile in .git/index.lock .git/HEAD.lock .git/refs/heads/main.lock; do
  if [[ -f "$lockfile" ]]; then
    echo "    lock trouve : $lockfile -> suppression"
    rm -f "$lockfile"
  fi
done

echo ">>> 1/6 stash runtime files non stages (si presents)"
STASHED=0
if ! git diff --quiet HEAD -- data/ site/data/ site/static/search_index.json 2>/dev/null; then
  git stash push -u -m "commit_R23d auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add R23d (titre questions Senat : retire prefixe +1 an)"
git add src/sources/senat.py
git add src/site_export.py
git add tests/test_site_export_fixups.py
git add commit_R23d.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R23d"
git commit -m "R23d — questions Senat : retire prefixe trompeur 'Question de +1 an sans reponse'

Symptome :
Sur le site, des questions Senat s affichaient avec un titre du type
'Question de +1 an sans reponse n°XXX S : <sujet>'. Or la date de depot
visible etait souvent recente (probable re-depot automatique cote
Senat). L etiquette '+1 an sans reponse' etait donc trompeuse : l item
n a pas 1 an de retard si on se fie a la date affichee.

Decision :
Retirer le prefixe du titre. On retombe sur le libelle neutre
'Question ecrite n°XXX S : <sujet>', identique aux autres questions
ecrites. Le `source_id` distinct `senat_questions_1an` reste intact :
il sert aux compteurs digest et au filtrage eventuel, mais n apparait
plus dans le titre visible.

Fix parser (src/sources/senat.py) :
Le mapping `qtype_label` pour sid='senat_questions_1an' passe de
'Question de +1 an sans reponse' a 'Question ecrite'. Les nouveaux
items ingeres auront donc directement le titre neutre.

Fix rendu (src/site_export.py _fix_question_row) :
Nouvelle etape 0ter : regex
    ^Question\\s+de\\s+\\+1\\s+an\\s+sans\\s+reponse\\b → 'Question ecrite'.
Idempotent. Corrige retroactivement les items deja en DB (upsert_many ne
renormalise pas les hash_key existants, donc le fix doit passer a
l export).

Label version bump : R23c -> R23d.

Tests (3 nouveaux + 1 ajuste, 221 total passent) :
- test_fix_question_row_rewrites_1an_prefix_to_question_ecrite
- test_fix_question_row_1an_prefix_idempotent
- test_fix_question_row_1an_prefix_preserves_source_id
- test_fix_question_row_strips_ministere_and_sort : assertion ajustee
  (le titre final commence maintenant par 'Question ecrite n°1054S').

Effet au prochain run daily :
- Nouveaux items senat_questions_1an : titre neutre des l ingestion.
- Items legacy deja en DB : fixup applique au build Hugo, pas de
  reset_category necessaire."

echo ""
echo ">>> 6/6 git push (avec rebase si besoin sur bot digest)"
git fetch origin main
BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
if [[ "$BEHIND" -gt 0 ]]; then
  echo "    $BEHIND commits en retard, rebase"
  git pull --rebase origin main
fi
git -c http.postBuffer=524288000 push origin main

if [[ "$STASHED" -eq 1 ]]; then
  echo ""
  echo ">>> post : git stash pop"
  git stash pop || echo "    stash pop echoue (a resoudre manuellement)"
fi

echo ""
echo "R23d pousse sur origin/main."
echo "Workflow daily.yml va auto-declencher :"
echo "  - label header passe R23c -> R23d"
echo "  - questions Senat (source senat_questions_1an) : titre neutre"
echo "    'Question ecrite n°... : ...'"
echo ""
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
