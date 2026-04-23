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
  git stash push -u -m "commit_R24 auto-stash $(date +%s)" -- data/ site/data/ site/static/search_index.json 2>/dev/null || true
  STASHED=1
fi

echo ">>> 2/6 git status avant add"
git status --short

echo ""
echo ">>> 3/6 git add R24 (ping 17h30 feature)"
git add src/main.py
git add src/ping.py
git add src/ping_state.py
git add tests/test_ping.py
git add tests/test_ping_state.py
git add .github/workflows/daily.yml
git add commit_R24.command

echo ""
echo ">>> 4/6 git status apres add"
git status --short

echo ""
echo ">>> 5/6 commit R24 (ping 17h30)"
git commit -m "R24 — ping 17h30 : alerte email si nouveautes apres-midi

Nouveau job GitHub Actions ping-afternoon (cron 30 15 * * 1-5,
17h30 Paris lun-ven) qui relit la DB et envoie un email court si
de nouveaux items matches sont apparus depuis le run du matin dans
les 4 categories prioritaires : dossiers legislatifs, amendements,
questions, comptes rendus. Silence total si rien de neuf, pas
d email  tout va bien  a 17h30.

Architecture
------------
Le pipeline principal (run 4h) ecrit a la fin data/ping_state.json
contenant les hash_keys matches des 4 categories chaudes. Ce fichier
est commite par veille-bot comme les autres caches data/.

Le ping compare ce snapshot au set actuel en DB par hash_key (pas
par date), ce qui est robuste aux re-upserts qui ne changent pas le
hash_key (ex. R23-A sort-prime-l-etat sur amendements). Aucun fetch
reseau : lecture DB uniquement, pour rester rapide (< 5s typique).

Fichiers nouveaux
-----------------
- src/ping_state.py : helper load/save/snapshot_from_rows/diff_new/
  merge. Ecriture atomique (tmp + os.replace), tolerant absence /
  corruption. Constante PING_CATEGORIES = (dossiers_legislatifs,
  amendements, questions, comptes_rendus).
- src/ping.py : orchestrateur. run_ping(db_path, state_path, ...)
  renvoie 0 silence/OK, 2 SMTP non configure, 10 DB absente. Template
  Jinja2 PING_EMAIL_TEMPLATE plus compact que le digest matin
  (titre, chambre, date, lien — pas de snippet ni status_label).
  send_email_fn injectable pour les tests.
- tests/test_ping_state.py : 27 tests (round-trip, corruption,
  filtrage categorie, diff sense/reverse, merge, hash_key fallback).
- tests/test_ping.py : 21 tests end-to-end avec vraie DB SQLite
  temporaire + mailer mocke (pas de SMTP reel) : DB vide, DB absente,
  silence sans nouveaute, envoi multi-cat, filtrage non-prioritaires,
  MAJ state conditionnelle a l envoi reussi, baseline corrompue.

Fichiers modifies
-----------------
- src/main.py : nouvelle sous-commande ping. A la fin de run(),
  appel ping_state.snapshot_from_rows + ping_state.save pour capturer
  l etat matinal. Nouvelle fonction ping() delegue a ping.run_ping.
- .github/workflows/daily.yml :
  - Nouveau cron 30 15 * * 1-5 (17h30 Paris lun-ven).
  - collect-and-publish filtre sur schedule == 0 2 * * * pour ne pas
    tourner a 17h30.
  - Nouveau job ping-afternoon (timeout 5min, pip install, restore
    cache SQLite read-only via restore-keys, python -m src.main ping,
    commit ping_state.json si change).
  - Ajout data/ping_state.json au commit veille-bot du job matin.

Tests
-----
Pytest : 326 verts (vs 278 avant R24) — les 48 nouveaux tests R24
couvrent ping_state (27) + ping end-to-end (21).

Limites connues
---------------
- Le cache SQLite du job ping-afternoon est restore via restore-keys
  seulement (pas de save), donc on lit la version du dernier run
  matin. Si la DB evolue en fil-de-l eau entre 4h et 17h30 (ex. via
  un workflow_dispatch intermediaire), le ping peut manquer ces
  items jusqu au prochain run matin. Acceptable vu que les sources
   temps-reel  (an_amendements, senat_amendements) ne sont
  re-ingerees que par les runs planifies.
- SITE_URL pas configure dans le job ping : utilise le defaut
  https://veille.sideline-conseil.fr. Suffisant tant que le domaine
  ne change pas.

Effet au prochain daily
-----------------------
- Run matin 4h (inchange) ecrit maintenant data/ping_state.json.
- Run ping 17h30 lun-ven tourne — email uniquement si nouveautes
  dans les 4 categories chaudes.
- Job ping-afternoon silencieux (exit 0) tant que la DB n a pas
  evolue depuis le run matin."

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
echo "R24 pousse sur origin/main."
echo ""
echo "Effet au prochain daily :"
echo "  - run matin 4h ecrit data/ping_state.json en fin de pipeline"
echo "  - ping-afternoon tourne a 17h30 Paris lun-ven (silencieux"
echo "    par defaut, email court si nouveautes dans les 4 categories"
echo "    prioritaires : dossiers, amendements, questions, CR)"
echo ""
echo "Premier ping 17h30 effectif : lundi prochain apres le run matin"
echo "qui aura genere le ping_state.json baseline."
echo ""
open -a "Google Chrome" "https://github.com/cyrilmourin/veille-parlementaire-sport/actions"

read -n 1 -s -r -p "Appuie sur une touche pour fermer..."
