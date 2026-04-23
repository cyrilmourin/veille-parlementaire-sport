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
  git stash push -u -m "commit_R23c2 auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add R23c2 (fix URL photo AN 404)"
git add src/amo_loader.py
git add src/sources/assemblee.py
git add src/site_export.py
git add tests/test_amo_loader.py
git add tests/test_assemblee_amendement_parser.py
git add commit_R23c2.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R23c2"
git commit -m "R23-C2 — fix URL photo portrait AN : /dyn/static/tribun/LEG/photos/carre/N.jpg

Symptome (remonte par Cyril) :
Sur /items/amendements/ et /items/questions/, la vignette
portrait du depute etait toujours masquee (display:none).
Le HTML contenait bien la balise <img>, mais le onerror la
masquait systematiquement apres un 404.

Diagnostic :
Le pattern construit par amo_loader.build_photo_url_an
etait :
    https://www.assemblee-nationale.fr/tribun/{LEG}/photos/{N}.jpg

Test reseau direct :
    curl -I https://www.assemblee-nationale.fr/tribun/17/photos/795908.jpg
    HTTP/2 404

C'etait un ancien chemin du site legacy. L'AN a migre ses
portraits sous /dyn/static/tribun/{LEG}/photos/carre/{N}.jpg.
Verification par fetch d'une fiche depute :
    <meta property='og:image' content='https://www.assemblee-nationale.fr/dyn/static/tribun/17/photos/carre/795908.jpg'>
    curl -I ce nouveau URL → HTTP 200.

Fix (src/amo_loader.py, build_photo_url_an) :
Remplace le pattern retourne par la nouvelle URL. Aucun
appel reseau n'est fait dans le pipeline — c'est juste une
URL construite a partir du PAxxx. Le template s'appuie sur
l'onerror comme garde-fou, donc cote site, des que le bon
pattern est en place, toutes les images apparaissent.

Impact :
- Les frontmatter regeneres au prochain build Hugo porteront
  la bonne URL (auteur_photo_url recalcule a chaque export
  site, car persiste dans raw mais backfille via
  amo_loader.build_photo_url_an si vide OU via appel direct
  dans le parser pour les nouveaux items).
- Pour les items deja ingeres avec l'ancienne URL dans raw,
  le fixup site_export (_fix_question_row / rendu des amdts)
  se base sur raw['auteur_photo_url']. Il faudra au prochain
  refresh_amo_cache + reingestion pour que les anciens items
  beneficient de la nouvelle URL — OU reparser rapidement
  via reset-category amendements/questions (geres par
  workflow reset-category.yml).

Tests (tests/test_amo_loader.py, tests/test_assemblee_amendement_parser.py) :
- test_build_photo_url_an_pattern : URL nouveau format (2 cas).
- test_build_photo_url_an_custom_legislature : leg=16 →
  /dyn/static/tribun/16/photos/carre/N.jpg.
- test_build_photo_url_an_accepts_whitespace : meme pattern
  pour entree avec espaces.
- test_parser_persists_auteur_photo_url_from_pa_ref : le
  parser amendement AN persiste la nouvelle URL.

Autres fichiers modifies (commentaires uniquement) :
- src/sources/assemblee.py : commentaire pattern
- src/site_export.py : commentaire backfill R23-C

Pytest : 272 tests verts (inchange)."

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
echo "R23-C2 pousse sur origin/main."
echo "Workflow daily.yml va regenerer les frontmatter avec les"
echo "bonnes URLs. Pour forcer le rattrapage des items legacy"
echo "deja ingeres avec la vieille URL :"
echo "  gh workflow run reset-category.yml -f category=amendements"
echo "  gh workflow run reset-category.yml -f category=questions"
echo ""
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
