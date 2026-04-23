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
  git stash push -u -m "commit_R23a auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add fix R23a"
git add src/sources/assemblee.py
git add src/site_export.py
git add tests/test_site_export_fixups.py
git add tests/test_assemblee_amendement_parser.py
git add commit_R23a.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R23a"
git commit -m "R23a — fix amendements AN : sort prime etat (API AN renvoie string, pas dict)

Symptome :
Sur le site, plusieurs amendements AN affichaient le chip Discute (jaune,
transitoire) alors que la seance avait deja statue (Tombe, Adopte...).
Ex. AS28, AS57, AS24 dans les commissions : ils etaient Tombe en seance
mais le site affichait Discute.

Cause racine (parser src/sources/assemblee.py) :
L API AN renvoie cycleDeVie.sort comme STRING directe
(ex. cycleDeVie.sort = 'Tombe') et pas comme dict {libelle: 'Tombe'}.
Le parser faisait
    _first(root, 'cycleDeVie.sort.libelle', 'cycleDeVie.sort.sortEnSeance')
qui ne matche jamais une string (on cherche le segment .libelle dans un
str). Resultat : raw.sort restait '' et le chip tombait sur raw.etat
(Discute, transitoire) au lieu du vrai sort.

Fix parser (src/sources/assemblee.py) :

1. cycleDeVie.sort lu en premier (forme string moderne). Le fallback
   dict legacy .libelle est conserve mais passe en 2eme position.
   _strip_html_text gere les deux cas (string ET dict {libelle: ...}).

2. Nouveau raw.sous_etat lu depuis
   cycleDeVie.etatDesTraitements.sousEtat.libelle. C est un proxy fiable
   quand sort est encore vide mais la decision est prise en commission
   (ex. sort='' mais sousEtat='Adopte sans modif').

Fix rendu (src/site_export.py) :

3. Chip logique extraite dans helper _amendement_chip(raw) testable en
   isolation. Nouvelle chaine de priorite :
       sort > sous_etat > etat > statut (legacy)
   sous_etat insere entre sort et etat : evite le fallback sur etat
   (Discute) quand le sousEtat dit deja Tombe/Adopte.

Label version bump : R22i -> R23a (1ere mini-release de la serie R23).

Tests (13 nouveaux, 207 total passent) :
- 6 tests parser (test_assemblee_amendement_parser.py) :
  * sort en forme string moderne
  * sort Adopte string
  * fallback forme dict legacy
  * sort vide -> raw.sort vide
  * sous_etat persiste depuis etatDesTraitements
  * sous_etat vide quand absent
- 7 tests _amendement_chip (test_site_export_fixups.py) :
  * sort prime sous_etat + etat + statut
  * fallback sous_etat quand sort vide (REGRESSION R23-A)
  * fallback etat quand sort + sous_etat vides
  * fallback statut (legacy pre-R13-J)
  * vide quand aucun champ renseigne
  * slug accents + espaces (Adopte sans modif. -> adopte-sans-modif)
  * noop sur raw non-dict

Effet au prochain run daily :
- Nouveaux amendements AN : chip correct des l ingestion.
- AMENDEMENTS DEJA EN DB : hash_key fige donc upsert_many ne remettra pas
  a jour raw.sort. Un reset_category amendements peut etre declenche pour
  les 3 amendements concernes (AS28, AS57, AS24) si souhaite.
  Alternative : laisser faire le temps, les nouveaux votes reinjecteront."

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
echo "R23a pousse sur origin/main."
echo "Workflow daily.yml va auto-declenche :"
echo "  - label header passe R22i -> R23a"
echo "  - nouveaux amendements : chip base sur sort > sous_etat > etat"
echo ""
echo "Pour corriger les 3 amendements existants (sort='' en DB) :"
echo "  gh workflow run reset-category.yml -f category=amendements"
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
