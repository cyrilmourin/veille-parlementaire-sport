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
  git stash push -u -m "commit_R23c4_c5_m auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add R23-C4 + R23-C5 + R23-M"
# R23-C4 : templates + CSS (portraits 56x56 en colonne gauche)
git add site/layouts/_default/list.html
git add site/static/style.css
# R23-C5 : helper build_photo_url_senat + cablage parser amendements Senat
git add src/amo_loader.py
git add src/sources/senat_amendements.py
git add tests/test_amo_loader.py
# R23-M : retrait prefixe "En cours :"
git add site/layouts/dossiers_legislatifs/list.html
# Label version R23g -> R23h
git add src/site_export.py
git add commit_R23c4_c5_m.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R23-C4 + R23-C5 + R23-M"
git commit -m "R23-C4 + R23-C5 + R23-M — portraits 56x56, photos Senat, retrait 'En cours :'

Trois patchs UX couples :
- R23-C4 : photos portrait parlementaires agrandies a 56x56 avec
  colonne gauche dediee, pour aligner le rendu sur les logos chambre
  des dossiers legislatifs.
- R23-C5 : photos portrait senateurs (Senat) enfin construites depuis
  le slug de la fiche senateur (colonne CSV 'Fiche Senateur').
- R23-M : retrait du prefixe 'En cours :' devant les status_label
  des dossiers legislatifs (le statut suffit).

R23-C4 (2026-04-23) — portraits 56x56
-------------------------------------
Symptome : les miniatures 28x28 etaient perdues dans la colonne
auteur, pas assez visuelles, et disaliennees avec les logos chambre
des dossiers legislatifs (56x56 en colonne gauche dediee).

Fix template (site/layouts/_default/list.html) :
Le <li> devient flex horizontal via la classe .listing-item. Si
.Params.auteur_photo_url est defini, une colonne <div class='listing-item__photo'>
(flex: 0 0 56px) encapsule l'<img class='auteur-photo'> 56x56. Le body
(<div class='listing-item__body'>) prend le reste. Si pas de photo, le
body occupe toute la largeur naturellement (pas de colonne fantome).
onerror masque .listing-item__photo (pas juste l'img) pour que le body
reprenne la place si la photo 404.

Fix CSS (site/static/style.css) :
- .auteur-photo : 28x28 → 56x56, border-radius 6 → 8, display: block,
  retrait de vertical-align / margin-right (plus d'inline).
- .auteur-photo-lg : 44 → 72 (detail, proportion preserve).
- Nouvelles regles .listing-item, .listing-item__photo,
  .listing-item__body — meme flex que .dosleg-card.

R23-C5 (2026-04-23) — build_photo_url_senat
-------------------------------------------
Diagnostic reseau :
  curl -I https://www.senat.fr/senimg/wattebled_dany19585h_carre.jpg
  HTTP 200

Pattern observe sur la HTML des fiches senfic (meta og:image) :
  https://www.senat.fr/senimg/<slug>_carre.jpg
ou <slug> est le slug senfic (ex. wattebled_dany19585h), extrait de
la colonne CSV 'Fiche Senateur' livre comme
  //www.senat.fr/senfic/wattebled_dany19585h.html

Ajouts :
- src/amo_loader.py :
  - Import `re`.
  - Regex _SENAT_SENFIC_RE (schema optionnel, www optionnel, .html/.htm).
  - Nouvelle fonction build_photo_url_senat(fiche_url) → URL /senimg/.
- src/sources/senat_amendements.py :
  - Lecture colonne 'Fiche Senateur' dans _build_item.
  - Normalisation schema (//… → https://…).
  - Appel amo_loader.build_photo_url_senat pour auteur_photo_url.
  - Persistance de auteur_url et auteur_photo_url dans raw.
- site_export.py deja cable pour lire raw['auteur_photo_url'] et
  l'exposer en frontmatter — aucun changement.

Limite connue (a traiter plus tard) :
Les questions Senat (senat_questions_1an) n'ont pas de colonne 'Fiche
Senateur' dans le CSV data.senat.fr. Elles ne beneficient pas encore
de la photo. Un mapping 'nom_prenom → slug senfic' construit une fois
depuis /senateurs/senatl.html serait la piste la plus simple pour une
mini-release ulterieure.

Tests :
- tests/test_amo_loader.py : 6 nouveaux tests build_photo_url_senat
  (pattern sans schema, avec https, avec http, sans www, invalides,
  whitespace). Toutes variantes du format CSV couvertes.

Pytest : 278 tests verts (vs 272 avant R23-C5).

R23-M (2026-04-23) — retrait 'En cours :'
-----------------------------------------
Objectif :
Sur la page /items/dossiers_legislatifs/, les badges affichaient
'En cours : Examen en commission' ou 'En cours : Adopte en 1ere
lecture'. Cyril : le status_label seul porte deja l'info (le fond
bleu distingue 'en cours' du fond vert 'Promulgue'). Le prefixe
'En cours :' est redondant et alourdit la lecture.

Decision :
- 'En cours :' supprime (juste status_label).
- 'Promulgue :' conserve (le status_label porte alors le numero/date
  de la loi — 'Loi n°2025-XXX du JJ mois' — le prefixe reste utile).

Fix (site/layouts/dossiers_legislatifs/list.html, ligne 92) :
Le ternaire {{ if \$.Params.is_promulgated }}Promulgue : {{ . }}{{ else }}En cours : {{ . }}{{ end }}
devient : {{ if \$.Params.is_promulgated }}Promulgue : {{ . }}{{ else }}{{ . }}{{ end }}

Label version bump : R23g -> R23h.

Effet au prochain run daily :
- R23-C4 : tous les items amendements/questions/communiques/agenda
  qui ont une auteur_photo_url auront leur photo 56x56 en colonne
  gauche. Les items Senat amendements ingeres a partir du prochain
  daily auront cette photo (backfill-via-reparse pour le legacy).
- R23-C5 : les amendements Senat ingeres depuis ce commit auront leur
  auteur_photo_url persiste dans raw + expose en frontmatter.
  Reset-category possible pour backfill :
    gh workflow run reset-category.yml -f category=amendements
- R23-M : effet immediat au build (pas de re-ingestion DB)."

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
echo "R23-C4 + R23-C5 + R23-M pousses sur origin/main."
echo ""
echo "Effets attendus au prochain daily :"
echo "  - label header R23g -> R23h"
echo "  - portraits AN et Senat agrandis a 56x56 en colonne gauche"
echo "  - amendements Senat : photo portrait enfin rendue (via slug senfic)"
echo "  - dossiers legislatifs : plus de 'En cours :' (status_label seul)"
echo ""
echo "Pour forcer le rattrapage des amendements Senat legacy :"
echo "  gh workflow run reset-category.yml -f category=amendements"
echo ""
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
