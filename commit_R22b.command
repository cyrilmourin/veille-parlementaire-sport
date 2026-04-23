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
if ! git diff --quiet -- data/ site/data/ site/static/search_index.json 2>/dev/null; then
  git stash push -u -m "commit_R22b auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add fix R22b"
git add src/site_export.py
git add tests/test_site_export_disabled_sources.py
git add commit_R22b.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R22b"
git commit -m "R22b — label version R19 -> R22a + filtre sources disabled a l export

Label version (SYSTEM_VERSION_LABEL) :
Bump R19 -> R22a. Le label etait hardcode depuis R13-G et jamais
incremente au fil des releases R20/R21/R22/R22a. Visible en haut a
droite du header (Version systeme : R22a . <short_sha>).

Filtre sources disabled (_filter_disabled_sources) :
Quand une source est marquee enabled: false dans config/sources.yml
(ex. alpes_2030_news en R17, senat_theme_sport_rss en R19-B), le
fetcher s arrete mais les items deja en DB continuent d etre
re-exportes vers le site jusqu a expiration de la fenetre de
publication (30 a 180 jours selon categorie). Resultat en prod :
- Google News items (chamber=news.google.com) dans Publications
- Sept items tas25-051/pjl25-307/l25-306/pjl25-278/pjl24-734...
  en dossiers_legislatifs (JOP Alpes 2030 doublons)
Le filtre lit config/sources.yml au demarrage de l export, construit
le set des source_id disabled, et exclut tous les rows dont source_id
est dans ce set. Applique AVANT _fix_* / _filter_window pour eviter
du travail inutile. Idempotent et safe : retourne rows tels quels
si le yaml est illisible.

Tests (4 nouveaux, 189 total passent) :
- test_load_disabled_source_ids_reads_real_yaml : sentinelle sur
  alpes_2030_news et senat_theme_sport_rss dans le yaml courant
- test_filter_removes_rows_from_disabled_source : filtre OK
- test_filter_is_noop_when_no_disabled_sources : safe si yaml KO
- test_filter_handles_missing_source_id_gracefully : rows sans
  source_id conserves

Effet sur le site au prochain run daily :
- Les items Google News disparaissent des Publications
- Les 4 textes Senat JOP 2030 disparaissent des dosleg (restera
  uniquement pjl24-630, le dossier principal)
- Plus besoin de reset DB manuel : la desactivation d une source
  prend effet immediatement"

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
echo "R22b pousse sur origin/main."
echo "Workflow daily.yml va auto-declenche :"
echo "  - label header passe de R19 a R22a"
echo "  - Google News + 4 textes Senat JOP disparaissent du site"
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
