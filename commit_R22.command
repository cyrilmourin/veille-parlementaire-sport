#!/bin/bash
set -e
REPO="/Users/cyrilmourin/Documents/Claude/Projects/Veille Parlementaire/veille-parlementaire-sport"
cd "$REPO"

echo ">>> 0/5 nettoyage locks git orphelins"
for lockfile in .git/index.lock .git/HEAD.lock .git/refs/heads/main.lock; do
  if [[ -f "$lockfile" ]]; then
    echo "    lock trouve : $lockfile -> suppression"
    rm -f "$lockfile"
  fi
done

echo ">>> 1/5 git status avant"
git status --short

echo ">>> 2/5 git add tout"
git add config/sources.yml
git add src/sources/html_generic.py
git add docs/AUDIT_R19.md
git add scripts/run_clean.sh
git add finish_R19.command
git add commit_R22.command
git add run_clean_R22.command

echo ""
echo ">>> 3/5 git status apres add"
git status --short

echo ""
echo ">>> 4/5 commit R22"
git commit -m "R22 — arbitrages AAI/juridictions

Sources (config/sources.yml) :
- Cour des comptes : HTML desactive -> RSS officiel ccomptes.fr/rss/publications
- Autorite de la concurrence : ajout scraping HTML /fr/communiques-de-presse
- Cour de cassation : supprimee du scope (site JS-only insurmontable)

html_generic._chamber() :
- + autoritedelaconcurrence.fr -> AdlC
- - courdecassation.fr -> Cassation (retire)

Docs :
- AUDIT_R19.md section 7 actualisee (arbitrages Cyril : CC reactivee,
  AdC ajoutee, Cassation/AMF/CNIL/HATVP/CADA/HCERES sortis, pas de
  2eme passe ML, amelioration du lexique mots-cles a la place, Alpes
  2030 retire des chantiers en attendant le site officiel COJOP).

Autres fichiers historiques traques :
- scripts/run_clean.sh (reset DB + run propre)
- finish_R19.command"

echo ""
echo ">>> 5/5 git push"
git -c http.postBuffer=524288000 push origin main

echo ""
echo "R22 pousse sur origin/main."
echo "Workflow GitHub Actions va se declencher pour rebuild le site."
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
