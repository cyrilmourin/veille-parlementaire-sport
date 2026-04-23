#!/bin/bash
# Déploiement R19 — Veille Parlementaire Sport
# Double-clic : commit + push des modifs locales, puis ouvre Chrome sur Actions.
set -e
REPO="/Users/cyrilmourin/Documents/Claude/Projects/Veille Parlementaire/veille-parlementaire-sport"
cd "$REPO"

echo ">>> 1/4 Nettoyage des locks git éventuels"
rm -f .git/index.lock .git/HEAD.lock .git/refs/heads/main.lock 2>/dev/null || true

echo ">>> 2/4 git add + status"
git add -A
git status --short

echo ">>> 3/4 Commit R19"
git commit -m "R19 — 8 fixes UX : logos dosleg, encoding Sénat, dédup JOP, Google News off, snippet CR recentré, agenda layout, PA codes, layout

Cf. tickets R19-A à R19-H (visuels Cyril 2026-04-23) :
- R19-A : encoding ï¿œ titres Sénat (fetch_bytes → feedparser respecte la PI XML ISO-8859-15)
- R19-B : dédup dossiers législatifs (désactive senat_theme_sport_rss qui remontait 8 docs internes par dossier)
- R19-C : questions — retire préfixe auteur redondant du snippet
- R19-D : agenda sidebar — badge orga + titre sur même ligne (line-clamp au lieu de flex-wrap)
- R19-E : agenda page — titre sur même ligne que badge
- R19-F : retrait source Google News Alpes 2030 (confirmation enabled: false)
- R19-G : snippet comptes rendus recentré sur le keyword (strip préambule Syceron)
- R19-H : CC → JORF clarifié (CC en communiques ; les décisions DC/QPC sont bien publiées au JO)

Fichiers :
- site/static/logos/senat.png (nouveau logo officiel 112x112)
- site/static/style.css (dosleg-card, side-body, agenda-row)
- site/layouts/dossiers_legislatifs/list.html (logos PNG)
- src/sources/senat.py (fetch_bytes + filtre /leg/pjl|ppl)
- src/sources/html_generic.py (fetch_bytes pour encoding RSS)
- config/sources.yml (senat_theme_sport_rss enabled: false)
- src/site_export.py (strip préambule Syceron CR + SYSTEM_VERSION_LABEL = R19)"

echo ">>> 4/4 git push"
git -c http.postBuffer=524288000 push origin main

echo ""
echo "✅ R19 pushé sur origin/main. GitHub Actions va déclencher le daily workflow."
echo ""
echo "→ Ouverture Chrome sur la page Actions pour suivre le run…"
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

echo ""
echo "(Cette fenêtre peut être fermée.)"
read -n 1 -s -r -p "Appuie sur une touche pour fermer…"
