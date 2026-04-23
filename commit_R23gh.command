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
  git stash push -u -m "commit_R23gh auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add R23g (titre agenda precis) + R23h (filtre famille sources)"
git add src/sources/assemblee.py
git add src/site_export.py
git add site/layouts/agenda/list.html
git add site/layouts/_default/list.html
git add site/static/style.css
git add tests/test_agenda_title_r23g.py
git add tests/test_source_family_r23h.py
git add commit_R23gh.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R23g + R23h"
git commit -m "R23g + R23h — agenda titre precis (commission+objet) et filtre UI par famille de source

Deux patchs UX couples, meme commit :
- R23-G : nettoie les titres agenda AN qui transportaient le LIEU
  (salle, visioconference, Palais Bourbon) a la place de l'objet reel.
- R23-H : ajoute un filtre UI par famille de source (5 boutons) en
  tete des listings /items/agenda/ et /items/communiques/.

R23-G (2026-04-23) — Agenda AN : titre precis
---------------------------------------------
Symptome :
Sur /items/agenda/, les titres etaient souvent un LIEU au lieu de
l'objet de la reunion :
- 'salle 4075 (9 rue de Bourgogne)'
- 'Commission des affaires sociales — Salle 6351 – Palais Bourbon'
- 'Mission d'information sur le Cout… — Visioconference sans salle'
- 'Office parlementaire d'evaluation… — Assemblee nationale (a confirmer)'

Cause (src/sources/assemblee.py) :
_collect_agenda_titles descend dans l'arbre JSON AN et considere
libelleLong comme candidat de titre (9e position dans
_AGENDA_TITLE_KEYS). Or pour un item agenda, le lieu est expose sous
lieu.libelleLong = 'Salle 6242 – Palais Bourbon…' — et le parent_key
du str est libelleLong, donc il remonte. De meme les sous-noeuds
'Assemblee nationale (a confirmer)' des offices bicameraux.

Fix parser :
- _AGENDA_SKIP_SUBTREES = {'lieu'} : on ne descend plus dans lieu.*
  pendant la collecte de titres (les libelles y sont exclusivement
  des metadonnees d'adressage).
- _AGENDA_LIEU_RE : regex de detection de chaines-lieu (Salle,
  Visioconference, Hemicycle, Palais Bourbon, Petit Luxembourg, Palais
  du Luxembourg, 'N rue/avenue/boulevard …').
  _is_agenda_title_candidate rejette ces chaines en amont.
- Ajout a _AGENDA_NOISE : 'assemblee nationale (a confirmer)',
  'senat (a confirmer)' (bruit des offices bicameraux).

Fix renderer (src/site_export.py, _fix_agenda_row) :
Deux passes idempotentes sur les items legacy deja ingeres (leur
titre est fige en DB, hash_key dedouble, donc un simple reparse ne
suffirait pas) :
1. Suffixe ' — <lieu>' en queue de titre → on coupe avant le tiret.
   Ex : 'Commission des affaires sociales — Salle 6351 – Palais
   Bourbon, 1eme etage' → 'Commission des affaires sociales'.
2. Titre qui EST un lieu pur → fallback sur raw.organe_label si
   connu, sinon 'Reunion parlementaire'.
   Ex : 'salle 4075 (9 rue de Bourgogne)' +
   raw.organe_label='Condition et bien-etre des animaux' →
   'Condition et bien-etre des animaux'.

Tests (14 nouveaux dans tests/test_agenda_title_r23g.py) :
- _is_agenda_title_candidate : rejet Salle / Visioconference /
  Palais Bourbon / rue, acceptation Audition / Examen du texte.
- _collect_agenda_titles : ignore sous-arbre lieu, ignore 'chambre
  (a confirmer)'.
- _fix_agenda_row : suffixe lieu retire, titre pur-lieu remplace
  par organe_label, fallback 'Reunion parlementaire' si organe
  inconnu, idempotence, no-op sur titre propre.

R23-H (2026-04-23) — Filtre UI par famille de source
----------------------------------------------------
Objectif :
Sur /items/agenda/ et /items/communiques/, le lecteur voit un flux
tres heterogene (parlement, ministeres, autorites, operateurs,
JORF). Il n'a aucun moyen de cibler 'seulement les publications du
gouvernement' ou 'seulement les operateurs du sport'. R23-H expose
un filtre 5 familles (+ un bouton 'Tout').

5 buckets (cf. helper _source_family dans src/site_export.py) :
- parlement : AN + Senat (tous source_id an_* et senat_*).
- gouvernement : Matignon, Elysee, tous les ministeres (min_*,
  info_gouv_*).
- autorites : ANJ, AFLD, ARCOM, Autorite de la concurrence, Conseil
  constitutionnel, Conseil d'Etat, Defenseur des droits, Cour des
  comptes, IGESR rapports.
- operateurs : ANS, INSEP, INJEP, CNOSF, CPSF / France paralympique.
- jorf : journal officiel (dila_jorf).

Mapping robuste :
- _SOURCE_FAMILY_BY_ID : match exact prioritaire.
- _SOURCE_FAMILY_BY_PREFIX : prefixes ('an_', 'senat_', 'min_', …).
- Fallback par chamber (AN / Senat / JORF).
- Dernier recours : 'autres' (bucket generique, pas de bouton —
  le JS garde l'item visible sur 'Tout' uniquement).

Frontmatter (src/site_export.py) :
Nouvelle cle `family_source: \"<slug>\"` ajoutee apres `source:` dans
chaque .md ecrit par _export_one_item. Zero impact DB, recalculee
au build Hugo.

Template /items/agenda/ (site/layouts/agenda/list.html) :
- Nav <nav class=\"source-family-filter\"> avec 6 boutons (Tout +
  les 5 familles) juste apres le <h1>.
- <li data-family-source=\"{{ .Params.family_source }}\"> sur chaque
  item, upcoming ET past.
- <script> vanilla en bas : toggle display:none sur les <li> selon
  le bouton actif (aria-pressed=\"true\").

Template /items/communiques/ (site/layouts/_default/list.html) :
- Meme filtre, affiche conditionnellement via
  {{ if eq .Type \"communiques\" }} — les autres listings
  (amendements, questions, dossiers, CR) n'en beneficient pas (leurs
  sources sont deja homogenes : AN+Senat uniquement).
- <li data-family-source=\"…\"> partout.
- Meme <script> en bas (no-op si pas de .source-family-filter).

CSS (site/static/style.css) :
- .source-family-filter : flex-wrap, gap 6px, margin 10/16px.
- .sff-btn : pilule, border radius 999px, hover subtil, aria-pressed
  fond accent + couleur blanche.

Tests (27 nouveaux dans tests/test_source_family_r23h.py) :
Couverture exhaustive du mapping :
- parlement (4 tests : an_agenda, an_amendements, senat_rss,
  senat_questions_1an).
- gouvernement (5 tests : min_sports, matignon, elysee, info_gouv,
  min_education).
- autorites (8 tests : ANJ, AFLD, ARCOM, AdlC, CC, CE, Cour des
  comptes, IGESR).
- operateurs (4 tests : ANS, INJEP, CNOSF, France paralympique).
- jorf (1 test).
- Fallback : chamber=AN seul, chamber=None, source_id=None, case
  insensitive.

Label version bump : R23f -> R23g.

Pytest : 272 tests verts (vs 259 avant R23-F).

Effet au prochain run daily :
- R23-G : le parser AN n'injecte plus le lieu comme titre candidat
  (nouveaux items), le fixup reecrit les titres legacy a l'export
  Hugo (idempotent, recalcule a chaque build).
- R23-H : la page /items/agenda/ et /items/communiques/ exposent
  le filtre. Les items sans family_source (rare : source
  completement nouvelle non mappee) retombent sur 'autres' et ne
  sont visibles que sur le bouton 'Tout'.

Rien a reset en DB — tout le traitement est cote export/render.
reset-category questions (depuis R23-F commit) reste planifie pour
le backfill raw.texte_question."

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
echo "R23g + R23h pousses sur origin/main."
echo "Workflow daily.yml va auto-declencher :"
echo "  - label header passe R23f -> R23g"
echo "  - agenda AN : titres nettoyes (suffixe lieu retire, titre"
echo "    pur-lieu remplace par organe_label)"
echo "  - /items/agenda/ et /items/communiques/ : filtre 5 familles"
echo "    Parlement / Gouvernement / Autorites / Operateurs / JORF"
echo ""
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
