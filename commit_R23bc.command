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
  git stash push -u -m "commit_R23bc auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add R23b + R23c (header UX pass)"
git add src/amo_loader.py
git add src/sources/assemblee.py
git add src/site_export.py
git add site/layouts/_default/list.html
git add site/layouts/_default/single.html
git add site/static/style.css
git add tests/test_amo_loader.py
git add tests/test_assemblee_amendement_parser.py
git add commit_R23bc.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R23b + R23c (header UX)"
git commit -m "R23b + R23c — header item : sigle groupe (tooltip libelle long) + photo portrait

Objectif :
Rendre la ligne d auteur plus informative sans deborder. Deux ajouts
visuels couples sur les memes templates Hugo (list.html + single.html)
et donc regroupes en un commit 'header UX pass'.

R23-B (2026-04-23) — sigle groupe parlementaire avec tooltip libelle long
---------------------------------------------------------------------------
Sur l AN, on affichait deja le sigle abrege (ex. 'LFI-NFP', 'RN', 'RE')
recupere du cache AMO. On ajoute un tooltip natif title=... porteur du
libelle long ('La France insoumise - Nouveau Front Populaire') pour ceux
qui survolent a la souris.

Pipeline :
- amo_loader.resolve_groupe_ref(PAxxx) : nouveau, renvoie POxxx du groupe.
- amo_loader.resolve_groupe_long(PAxxx) : compose resolve_groupe_ref +
  resolve_organe(prefer_long=True).
- sources/assemblee : amendement + question persistent raw.groupe_long
  (resolu via le PO fourni directement ou via le cache AMO).
- site_export expose auteur_groupe_long en frontmatter (avec backfill
  pour les items legacy via amo_loader.resolve_groupe_long).
- list.html + single.html : <span class='auteur-groupe' title='...'> si
  le libelle long est present. CSS .auteur-groupe[title] ajoute un
  cursor:help et une bordure pointillee tres discrete.

Sénat : deferrable (le CSV Senat ne publie pas le libelle long). Le
tooltip se masque gracieusement quand auteur_groupe_long est vide.

R23-C (2026-04-23) — photo portrait AN deterministe
---------------------------------------------------
L Assemblee publie les portraits des deputes sous pattern stable :
    https://www.assemblee-nationale.fr/<legislature>/photos/<digits>.jpg
ou <digits> est le PAxxx sans le prefixe 'PA'.

Pipeline :
- amo_loader.build_photo_url_an(PAxxx, legislature=17) : nouveau helper
  deterministe, tolere les PA vides/bruites (retourne '' si pas de
  digits apres strip).
- sources/assemblee : amendement + question persistent raw.auteur_photo_url.
- site_export expose auteur_photo_url en frontmatter (avec backfill
  pour les items legacy).
- list.html : <img class='auteur-photo'> 28x28 avant l auteur-inline.
- single.html : <img class='auteur-photo auteur-photo-lg'> 44x44.
- onerror='this.style.display=\"none\"' : masque l image si 404 (acteur
  inconnu ou photo absente cote AN). alt='' pour ne pas doubler
  l info avec .auteur-inline (accessibilite lecteurs d ecran).
- CSS .auteur-photo (border-radius 6px) + .auteur-photo-lg (8px).

Sénat : differe (necessite scraping /senateurs/senatl.html pour slug
matricule senateur_prenom_nom<matricule><lettre>). Seules les photos AN
sont implementees dans cette release.

Label version bump : R23a -> R23c (pas de R23b separe, les changes
B et C sont regroupes car ils touchent les memes templates).

Tests (11 nouveaux, 218 total passent) :
- 6 tests amo_loader.py :
  * resolve_groupe_ref PAxxx -> POxxx
  * resolve_groupe_long PAxxx -> libelle long via groupe_ref
  * build_photo_url_an pattern /tribun/17/photos/<digits>.jpg
  * build_photo_url_an legislature parametrable (16 pour archives)
  * build_photo_url_an entrees invalides (vide, None, PO_, lettres)
  * build_photo_url_an tolere whitespace parasite
- 5 tests parser (test_assemblee_amendement_parser.py) :
  * groupe_long persiste quand PO fourni directement
  * groupe_long persiste via cache AMO (resolve_groupe_ref)
  * groupe_long vide quand cache pas encore rafraichi
  * auteur_photo_url persiste depuis acteurRef PAxxx
  * auteur_photo_url vide quand acteurRef absent

Effet au prochain run daily :
- Tous les items AN ingerer recemment ont deja auteur_ref persiste,
  donc le backfill dans site_export les decorera de photo + tooltip au
  prochain build Hugo. Pas besoin de reset_category."

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
echo "R23b + R23c pousses sur origin/main."
echo "Workflow daily.yml va auto-declencher :"
echo "  - label header passe R23a -> R23c"
echo "  - items AN (amendements + questions) : sigle groupe avec tooltip"
echo "    libelle long + photo portrait 28x28 (list) / 44x44 (detail)"
echo "  - Senat : tooltip et photo masques gracieusement (differes)"
echo ""
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
