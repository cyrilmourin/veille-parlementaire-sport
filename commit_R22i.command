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
  git stash push -u -m "commit_R22i auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add fix R22i"
git add src/sources/senat.py
git add src/site_export.py
git add tests/test_site_export_fixups.py
git add commit_R22i.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R22i"
git commit -m "R22i — fix URLs questions Senat (fallback 404 -> colonne CSV URL Question)

Symptome :
Les liens des questions senatoriales sur le site renvoyaient 404
(ex. https://www.senat.fr/questions/base/1054S.html -> page introuvable).
Toutes les sources actives (senat_qg, senat_questions_1an) etaient
concernees : l URL stockee en DB etait systematiquement cassee.

Cause :
Dans src/sources/senat.py ligne 572, le parser faisait
    _pick(r, \"URL\", \"url\", \"lien\")
qui ne matche pas la colonne reelle du CSV Senat, nommee exactement
    URL Question
(colonne qui contient la vraie URL au format
http://www.senat.fr/questions/base/YYYY/qSEQ...<num>.html).
Resultat : on tombait toujours sur le fallback
    f\"https://www.senat.fr/questions/base/{uid}.html\"
qui n est pas un pattern valide cote senat.fr.

Fix (2 volets) :

1. Parser (src/sources/senat.py) : lecture de URL Question en priorite,
   force du scheme https:// (le CSV livre du http://). Fallback historique
   conserve mais ne devrait plus servir.

2. Fixup in-memory (src/site_export.py::_fix_question_row) : repare a
   l export les items deja ingeres avec l URL cassee, en relisant
   raw[\"URL Question\"] (colonne CSV persistee dans raw en DB). Evite
   un reset_db. Nouvelle regex _SENAT_QUESTION_LEGACY_URL_RE qui detecte
   le pattern legacy .../base/<uid>.html (sans segment annee). Idempotent :
   ne touche pas aux URLs deja correctes ni aux items non-Senat.

Label version bump : R22g -> R22i.

Tests (5 nouveaux, 194 total passent) :
- test_fix_question_row_rewrites_broken_senat_url_from_raw
- test_fix_question_row_rewrites_broken_senat_url_for_senat_qg
- test_fix_question_row_keeps_url_when_raw_url_missing
- test_fix_question_row_noop_when_url_already_correct
- test_fix_question_row_noop_for_an_questions

Effet au prochain run daily : toutes les questions Senat pointent vers
la vraie page senat.fr (plus de 404), sans reset_db."

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
echo "R22i pousse sur origin/main."
echo "Workflow daily.yml va auto-declenche :"
echo "  - label header passe R22g -> R22i"
echo "  - URLs des questions Senat passent de .../base/<uid>.html (404)"
echo "    a .../base/YYYY/qSEQ...<num>.html (OK)"
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
