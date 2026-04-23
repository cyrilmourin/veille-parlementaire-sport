#!/bin/bash
set -e
REPO="/Users/cyrilmourin/Documents/Claude/Projects/Veille Parlementaire/veille-parlementaire-sport"
cd "$REPO"

echo ">>> 0/7 nettoyage locks git orphelins"
for lockfile in .git/index.lock .git/HEAD.lock .git/refs/heads/main.lock; do
  if [[ -f "$lockfile" ]]; then
    echo "    lock trouve : $lockfile -> suppression"
    rm -f "$lockfile"
  fi
done

echo ">>> 1/7 stash runtime files non stages (si presents)"
STASHED=0
if ! git diff --quiet HEAD -- data/ site/data/ site/static/search_index.json 2>/dev/null; then
  git stash push -u -m "commit_R23f auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/7 git status avant add"
git status --short

echo ""
echo ">>> 3/7 git add R23f (helper _strip_cr_an_preamble + tests)"
git add src/site_export.py
git add tests/test_site_export_fixups.py
git add commit_R23f.command

echo ""
echo ">>> 4/7 git status apres add"
git status --short

echo ""
echo ">>> 5/7 commit R23f"
git commit -m "R23f — CR AN : extrait sans preambule Syceron (helper + 6 tests)

Objectif :
Sur /items/comptes_rendus/, les CR de l'Assemblee Nationale affichaient
un snippet dont le debut etait pollue par le preambule technique
Syceron (CRSANR5L17S2026..., RUANR..., numeros de seance isoles,
'Session ordinaire 2025 -2026', 'valide complet public avant_JO PROD',
timestamps ISO). Le lecteur devait scroller visuellement a travers ce
bruit avant de tomber sur le contenu reel (Presidence, Questions au
gouvernement, La seance est ouverte, La commission).

R19-G avait tente une approche regex + check de taille residuelle, mais
les numeros de seance isoles (ex. '1 130 AN 17') echappaient au pattern
et empechaient la coupe. Le probleme est reste visible sur le site.

R23-F (2026-04-23) — nouvelle strategie : marker-based cut
----------------------------------------------------------
Plutot que de regex-clean le preambule (fragile, regex instables face
aux variantes), on cherche le premier marqueur connu de DEBUT DE CORPS
dans les 600 premiers caracteres et on coupe dessus.

Marqueurs (_CR_AN_BODY_MARKERS) :
  - 'Presidence' (ex. 'Presidence de Mme Yael Braun-Pivet')
  - 'Questions au gouvernement'
  - 'La seance est ouverte'
  - 'La commission' (pour les CR de commissions)

Strategie (src/site_export.py, nouveau helper _strip_cr_an_preamble) :
- Pour chaque marqueur, find() son index dans le haystack.
- Si 0 <= idx <= 600, candidat retenu. On garde le PLUS PETIT index.
- Si best_idx > 0, on coupe : haystack[best_idx:]
- Si best_idx == 0 (marqueur deja au debut), on retourne tel quel →
  bloque la re-coupe sur un marqueur ulterieur et garantit l'idempotence.
- Si aucun marqueur trouve ou > 600 chars, haystack inchange (le summary
  est deja propre OU le format est inattendu).

Integration (_parse_rows, bloc existant R23-D2 haystack priority) :
Pour category == 'comptes_rendus' AND chamber == 'AN', le helper est
applique au haystack AVANT le passage au matcher de snippet. Scope
limite a l'AN : les CR Senat ont un format de preambule different et
pour l'instant pas de probleme signale.

Label version bump : R23e -> R23f.

Tests (6 nouveaux, 231 total passent) :
tests/test_site_export_fixups.py, nouvelle section R23-F :
- test_strip_cr_an_preamble_cuts_at_presidence_marker : preambule
  Syceron realiste + coupe verifiee a 'Presidence', verification que
  'CRSANR5L17' et 'avant_JO PROD' ne sont plus presents.
- test_strip_cr_an_preamble_no_marker_returns_unchanged : haystack
  propre (sans marqueur) → renvoye tel quel.
- test_strip_cr_an_preamble_picks_earliest_marker : plusieurs
  marqueurs presents → on coupe au plus tot, pas a l'ordre de la
  tuple _CR_AN_BODY_MARKERS.
- test_strip_cr_an_preamble_is_idempotent : deux passes consecutives
  donnent le meme resultat (bloquage re-coupe quand marqueur deja en
  position 0).
- test_strip_cr_an_preamble_empty_input : chaine vide tolere.
- test_strip_cr_an_preamble_marker_beyond_max_prefix_ignored :
  marqueur a position > 600 chars ignore (protege les summaries ou
  'Presidence' apparait en plein corps).

Effet au prochain run daily :
- Nouveaux CR AN : snippet construit sur le corps utile, sans
  preambule. Aucun reset DB necessaire (le snippet est recompute au
  build Hugo depuis summary).
- CR AN legacy en DB : idem, puisque le snippet est TOUJOURS recompute
  au moment de _parse_rows (et non persiste en DB). Le helper corrige
  donc retroactivement TOUS les items comptes_rendus AN sans requerir
  de reset_category.

Integration reset-category questions (R23-D2 backfill) :
Ce commit declenche AUSSI 'gh workflow run reset-category.yml -f
category=questions' post-push pour forcer la re-ingestion des
questions parlementaires legacy. Raison : R23-D2 a ajoute raw.
texte_question cote parsers (AN + Senat), mais les items deja en DB
ont un hash_key deduplique → ils ne sont pas re-normalises au daily
suivant. reset_category questions purge la table et reimporte, ce qui
ajoute raw.texte_question aux items historiques et ameliore leur
snippet au prochain build Hugo."

echo ""
echo ">>> 6/7 git push (avec rebase si besoin sur bot digest)"
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
echo ">>> 7/7 declenchement reset-category questions (backfill R23-D2)"
echo "    objectif : re-ingerer les questions legacy pour ajouter"
echo "    raw.texte_question aux items dedupliques en DB"
if command -v gh >/dev/null 2>&1; then
  gh workflow run reset-category.yml -f category=questions && \
    echo "    workflow reset-category.yml declenche avec category=questions" || \
    echo "    echec du gh workflow run (a relancer manuellement)"
else
  echo "    gh CLI absent : declencher manuellement depuis l'onglet Actions"
fi

echo ""
echo "R23f pousse sur origin/main."
echo "Workflow daily.yml va auto-declencher :"
echo "  - label header passe R23e -> R23f"
echo "  - CR AN : snippet coupe au premier marqueur de corps (Presidence,"
echo "    Questions au gouvernement, La seance est ouverte, La commission)"
echo "  - CR Senat et autres categories : inchange"
echo ""
echo "Workflow reset-category.yml declenche (category=questions) :"
echo "  - backfill R23-D2 sur tous les items questions legacy en DB"
echo "  - raw.texte_question ajoute → snippet construit sur le corps"
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
