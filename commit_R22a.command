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

echo ">>> 1/6 purge site/public/ local (fantomes hugo builds anterieurs)"
rm -rf site/public || true

echo ">>> 2/6 git status avant"
git status --short

echo ">>> 3/6 git add fix R22a"
git add src/site_export.py
git add tests/test_site_export_dedup.py
git add tests/test_sources_config.py
git add .github/workflows/daily.yml
git add scripts/run_clean.sh
git add site/static/style.css
git add commit_R22a.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R22a"
git commit -m "R22a — fix dedup dosleg 4-occurrences + purge site/public CI

Probleme racine dedup (src/site_export.py) :
Scenario JOP Alpes 2030 : 4 items (1 AN DLR5L17N52100 + 2 senat_akn
avec url_an + 1 senat_promulguees sans url_an) ne fusionnaient qu a 2.

Cause : passe 2a (URL canon) fusionnait les 3 Senat sur le winner
senat_promulguees (date desc). Mais senat_promulguees n a pas raw.url_an
qui porte le bridge vers DLR5L17N52100. Passe 2c (dossier_id) ne pouvait
donc plus relier l AN restant au Senat winner.

Fix : nouveau helper _merge_ids_into_winner qui injecte raw._merged_dossier_ids
cumule dans le winner a chaque fusion (passes 2a et 2b). _item_dossier_ids
lit ce champ en plus de raw.dossier_id / raw.signet / URLs. La passe 2c
voit alors {DLR5L17N52100, pjl24-630, 2026-201} cote Senat -> intersection
non vide avec {DLR5L17N52100} cote AN -> fusion OK.

Hugo workflow (.github/workflows/daily.yml) :
rm -rf site/public/ avant hugo --minify pour eviter que les pages
generees par des sources desactivees (alpes_2030_news, senat_theme_sport_rss)
restent en ligne. Hugo ne purge pas les pages orphelines par defaut.
Constate en prod : 85 pages alpes_2030_news encore accessibles HTTP 200
sur veille.sideline-conseil.fr alors que la source est disabled depuis R17.

Tests :
- Nouveau test_dedup_r22a_preserves_url_an_bridge_across_passes
  (regression : 4 items JOP Alpes 2030 -> 1 item attendu)
- test_high_jurisdictions_configured : retrait cour_cassation
  (coherent avec R22 qui sort Cassation du scope)
- 185 tests passent.

Aussi committes (traines de R22 non encore pousses) :
- scripts/run_clean.sh : resolution Python (.venv/bin/python -> python3
  systeme -> erreur) pour macOS qui n aliase pas python -> python3
- site/static/style.css : module recherche en #11264b (demande Cyril)"

echo ""
echo ">>> 6/6 git push"
git -c http.postBuffer=524288000 push origin main

echo ""
echo "R22a pousse sur origin/main."
echo "Workflow GitHub Actions va rebuild site/public depuis zero,"
echo "les pages alpes_2030_news fantomes vont disparaitre du prod."
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
