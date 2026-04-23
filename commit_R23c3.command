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
  git stash push -u -m "commit_R23c3 auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add R23c3 (fix deploy-pages multiple artifacts)"
git add .github/workflows/daily.yml
git add commit_R23c3.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R23c3"
git commit -m "R23-C3 — fix deploy-pages : purge artefacts github-pages stales avant upload

Symptome observe sur collect-and-publish (runs R23-D2 et R23-E) :
    Error: Multiple artifacts named 'github-pages' were unexpectedly
    found for this workflow run. Artifact count is 3.
       at getArtifactMetadata (actions/deploy-pages/v4)
       at Deployment.create

Cause :
Le job unique 'collect-and-publish' n'appelle upload-pages-artifact
qu'une seule fois. MAIS le bug connu actions/deploy-pages#290 veut
que si l'utilisateur clique 'Re-run failed jobs' sur un run existant,
GitHub Actions relance les steps en echec SANS purger les artefacts
deja publies par le run initial. A la 2e tentative, il y a 2
artefacts 'github-pages' pour le meme run_id ; a la 3e, 3. Le step
deploy-pages@v4 lit la liste des artefacts du run courant, en trouve
plusieurs avec le meme nom, et abandonne avec l'erreur ci-dessus.

Fix (.github/workflows/daily.yml) :
Nouveau step 'Purge stale github-pages artifacts from this run' INSERE
juste avant 'Upload Pages artifact'. Il utilise actions/github-script@v7
pour :
  1. lister les artefacts du run courant (context.runId) via l'API
     REST GitHub ;
  2. filtrer sur name == 'github-pages' ;
  3. les supprimer un par un via deleteArtifact.

Le step ne touche a rien au premier passage (pas d'artefact encore) et
purge les stales sur tout re-run. upload-pages-artifact re-ecrit
ensuite l'artefact unique que deploy-pages consomme.

Effet :
- Premier run : aucun artefact à purger, upload normal, deploy OK.
- Re-run failed jobs : purge des 1-N artefacts stales avant upload,
  deploy-pages@v4 trouve un unique 'github-pages', deploy OK.
- Aucun impact sur les autres artefacts (digest-\${run_id}, caches…).

Permissions :
Ajout d'actions: write dans le bloc 'permissions:' du workflow.
deleteArtifact() exige cette permission ; sans elle, le step
renvoie 403 et bloque le deploy. Le bloc passe donc de
  contents: write / pages: write / id-token: write
a
  contents: write / pages: write / id-token: write / actions: write

Pas de test automatise : on ne teste pas les workflows GHA. Validation
en conditions reelles sur le prochain re-run.

Ref : https://github.com/actions/deploy-pages/issues/290"

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
echo "R23-C3 pousse sur origin/main."
echo "Prochain run daily.yml : collect-and-publish purgera les artefacts"
echo "stales 'github-pages' avant upload. Les re-runs 'Re-run failed jobs'"
echo "seront de nouveau idempotents."
echo ""
echo "Si le premier run echoue sur le step 'Purge stale github-pages…'"
echo "avec une erreur 403, ajouter dans permissions: du workflow :"
echo "    actions: write"
echo ""
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
