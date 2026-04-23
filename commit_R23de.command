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
  git stash push -u -m "commit_R23de auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add R23d2 (extrait corps question) + R23e (logo chambre CR)"
git add src/sources/assemblee.py
git add src/sources/senat.py
git add src/site_export.py
git add site/layouts/comptes_rendus/list.html
git add site/static/style.css
git add tests/test_assemblee_question_parser.py
git add tests/test_senat_questions_parser.py
git add commit_R23de.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R23d2 + R23e"
git commit -m "R23d2 + R23e — questions : extrait depuis le corps + logo chambre sur la liste des CR

Objectif :
Deux patchs UX couples sur le rendu des items :
- R23-D2 : l'extrait (snippet) affiche enfin le CORPS de la question et
  non plus les metadonnees (Destinataire / Rubrique / Analyse) qui
  squattaient le debut du summary.
- R23-E : sur la page /items/comptes_rendus/, le badge texte .chamber
  (AN / Senat) est remplace par le logo SVG de la chambre (assets deja
  presents dans site/static/logos/).

R23-D2 (2026-04-23) — extrait depuis le corps de la question
-------------------------------------------------------------
Symptome :
Sur le site, le snippet des questions parlementaires affichait souvent
'Destinataire : Ministre X — Rubrique : sports — Analyse : Y' au lieu
du texte reel de la question. Le matcher de snippet (build_snippet)
tombait sur la premiere occurrence du mot-cle dans le prefixe metadonnees
du summary, avant d'atteindre le corps.

Cause :
Dans site_export._parse_rows, le haystack etait systematiquement
(summary or title), qui pour les questions est le resultat de
    auteur (groupe) — Destinataire : X — Rubrique : Y — Analyse : Z — <texte> — <reponse>
Le match positionnel etait donc concentre sur le prefixe metadonnees.

Fix parser AN (src/sources/assemblee.py) :
Le parser `_normalize_question` persiste maintenant `raw.texte_question`
= le corps nettoye (`_text_of(texte_node)`) separement du summary. Cle
stable pour le consommateur aval.

Fix parser Senat (src/sources/senat.py) :
Le parser `_normalize_rows` pour `senat_questions*` injecte
`r['texte_question'] = texte` (depuis la colonne CSV 'Texte') quand il
est non vide. Cle identique a celle du parser AN pour unifier le data path.

Fix rendu (src/site_export.py) :
Dans le bloc qui reconstruit le snippet, pour category == 'questions',
on parse `r['raw']` et on prefere `raw.texte_question` comme haystack.
Fallback sur `summary or title` si le corps n'est pas disponible (items
legacy ou mal formes).

R23-E (2026-04-23) — logo chambre sur la liste des CR
-----------------------------------------------------
Sur la page /items/comptes_rendus/, le badge texte .chamber (AN / Senat)
est remplace par le logo SVG embarque (assets deja presents :
site/static/logos/an.svg et senat.svg, commit R13-H).

Template (site/layouts/comptes_rendus/list.html) :
Remplacement conditionnel du span textuel :
  - chamber = AN  -> <img src='/logos/an.svg' class='chamber-logo' alt='AN'>
  - chamber = Senat -> <img src='/logos/senat.svg' class='chamber-logo' alt='Senat'>
  - autre -> fallback sur l'ancien <span class='chamber' data-chamber='...'>
Fallback 404 gere par onerror='this.style.display=\"none\"' (ne devrait
pas arriver pour les assets Hugo locaux mais ceinture-bretelles).

CSS (site/static/style.css) :
Nouvelle classe .chamber-logo : carre 22x22, border-radius 3px, fond
blanc + filet discret (#rgba 0.06) pour se detacher sur fond creme.

Scope limite a comptes_rendus/list.html pour cette release. La meme
logique pourra etre portee plus tard sur :
- _default/list.html (listes generiques de toutes categories)
- recherche/list.html (recherche globale)
- sidebar et home (si UX le demande)

Label version bump : R23d -> R23e.

Tests (4 nouveaux, 225 total passent) :
- tests/test_assemblee_question_parser.py (nouveau, 2 tests) :
  * parser AN persiste raw.texte_question depuis le corps
  * raw.texte_question vide quand pas de corps
- tests/test_senat_questions_parser.py (nouveau, 2 tests) :
  * parser Senat persiste raw.texte_question depuis colonne CSV 'Texte'
  * raw.texte_question absent quand colonne vide (fallback implicite)

Effet au prochain run daily :
- Nouveaux items ingeres : raw.texte_question present des l'ingestion,
  snippet reconstruit sur le corps au build Hugo.
- Items legacy en DB : raw.texte_question absent → fallback summary
  (comportement actuel). Pour forcer la recalculation, reset_category
  questions declenche un reparse complet depuis le cache.
- CR list page : logos au build Hugo suivant (aucun impact DB)."

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
echo "R23d2 + R23e pousses sur origin/main."
echo "Workflow daily.yml va auto-declencher :"
echo "  - label header passe R23d -> R23e"
echo "  - questions : snippet construit sur raw.texte_question (nouveaux"
echo "    items), summary en fallback pour le legacy"
echo "  - /items/comptes_rendus/ : logo AN / Senat a la place du badge"
echo "    texte, fallback gracieux pour chambres inconnues"
echo ""
echo "Pour re-injecter le corps sur TOUS les items questions en DB :"
echo "  gh workflow run reset-category.yml -f category=questions"
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
