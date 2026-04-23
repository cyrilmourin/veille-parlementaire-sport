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
  git stash push -u -m "commit_R23ij auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add R23-I + R23-J (INSEP + FDSF)"
git add src/sources/html_generic.py
git add config/sources.yml
git add tests/test_sources_config.py
git add commit_R23ij.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R23-I + R23-J (INSEP + FDSF)"
git commit -m "R23-I + R23-J : ajout sources INSEP et Fondation du Sport Francais

Deux nouvelles sources 'communiques' coeur de cible veille sport,
toutes deux via leur flux RSS officiel (pas de scrape HTML) :

INSEP (R23-I)
-------------
Institut national du sport, de l expertise et de la performance,
etablissement public national rattache au MinSports. Site Drupal 11,
aucun flux RSS sur les paths standard (/feed, /fr/feed, /rss.xml
renvoient tous 404), mais Drupal Views expose automatiquement le
listing actualites en RSS 2.0 via le suffixe .xml :
  https://www.insep.fr/fr/actualites.xml
Verifie live 2026-04-23 : HTTP 200, 75 Ko, 10 items recents avec
pubDate RFC 822 et descriptions HTML echappees propres (feSPORT
europeen du sport, medaille Milan-Cortina, scolarite athletes...).

Configuration : poids 3 (meme niveau que ans, min_sports_presse,
min_sports_actualites — operateur coeur de cible). Domaine mappe
insep.fr -> badge INSEP dans html_generic._chamber().

FDSF (R23-J)
------------
Fondation du Sport Francais, reconnue d utilite publique, adossee
au CNOSF. Promeut le sport comme vecteur de lien social via les
dispositifs Soutiens Ton Club, Pacte de Performance et Soutiens
Ton Sportif. Site Squarespace : la home est rendue en JS (rien a
scraper en HTML statique) mais le blog /web/fsf/actualites expose
le feed RSS natif Squarespace via le suffixe magique ?format=rss :
  https://www.fondation-du-sport-francais.fr/web/fsf/actualites?format=rss
Verifie live 2026-04-23 : HTTP 200, 299 Ko, 20+ items avec
<description> CDATA + <content:encoded> corps HTML complet (utile
au keyword matcher).

Items recents pertinents pour la veille institutionnelle :
  - FDSF au Senat : la diplomatie sportive (2026-04-09)
  - Soutiens Ton Sportif franchit le cap des 3 M EUR (2026-03-31)
  - Milan-Cortina 2026, edition historique pour la France
Domaine mappe fondation-du-sport-francais.fr -> badge FDSF.

Fichiers modifies
-----------------
- src/sources/html_generic.py : 2 nouveaux blocs dans _chamber(),
  place juste apres INJEP (domaines non-gouv.fr, avant le fallback
  generique .gouv.fr). Zero impact sur les domaines existants.
- config/sources.yml : 2 nouvelles entrees dans le groupe autorites
  juste apres injep, toutes deux en format: rss (le _from_rss_generic
  existant gere deja les 2 flux sans code dedie). INSEP a poids: 3.
- tests/test_sources_config.py : 3 nouveaux tests garde-fou
  (presence des 2 sources, format rss verrouille, URLs stables,
  mapping _chamber correct pour les 2 domaines).

Tests
-----
Pytest : 329 verts (vs 326 avant R23-I+J). Les 3 nouveaux tests
R23-I+J couvrent test_r23ij_insep_fdsf_present + test_r23i_insep_
chamber_mapping + test_r23j_fdsf_chamber_mapping.

Effet au prochain daily
-----------------------
- Au run matin 4h, ces 2 flux seront fetches (html_generic dispatch
  format rss vers _from_rss_generic).
- INSEP devrait remonter ~10 items sur fenetre 90j, dont plusieurs
  matches keywords (sport-etudes, haut niveau, JO, performance).
- FDSF devrait remonter ~20 items dont plusieurs matches directs
  veille parlementaire (tables rondes Senat, propositions de loi,
  diplomatie sportive)."

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
echo "R23-I + R23-J pousse sur origin/main."
echo ""
echo "Effet au prochain daily :"
echo "  - INSEP : 10 items RSS /fr/actualites.xml, poids 3"
echo "  - FDSF  : 20 items RSS ?format=rss (Squarespace natif)"
echo "  - Les 2 sources en categorie communiques, fenetre 90j"
echo "  - Badges INSEP et FDSF dans le digest et sur le site"
echo ""
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
