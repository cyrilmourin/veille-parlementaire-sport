---
title: Veille Parlementaire Sport — Handoff
maintainer: Cyril Mourin
last_updated: 2026-04-23 (après R22 → R22a → R22b)
---

# Handoff — Veille Parlementaire Sport

Ce document est le point d'entrée pour reprendre le projet sans contexte préalable. Il suit une structure fixe : État actuel · Décisions clés · TODO · Pièges connus · Historique. Les quatre premières sections sont réécrites à chaque session ; seule la section Historique cumule.

---

## État actuel

### Ce que fait l'outil

Agrégation automatisée de la production institutionnelle française (Parlement, Élysée, Matignon, ministères, JORF, AAI, juridictions, instances sportives) filtrée sur un dictionnaire de mots-clés sport. Deux livrables :

1. **Email quotidien** à 06:30 Europe/Paris (`digest.py`, template Jinja2).
2. **Site statique Hugo** publié sur `https://veille.sideline-conseil.fr` (GitHub Pages).

Catégories Follaw.sv couvertes : dossiers législatifs, JORF, amendements, questions, comptes-rendus, publications, nominations, agenda, communiqués.

### Pipeline

`src/main.py run --since N` orchestre :

1. `normalize.run_all` itère `config/sources.yml` (≈ 61 sources déclarées, ≈ 51 actives) et appelle pour chaque source le connecteur approprié (`src/sources/*.py`). Retourne une liste d'`Item` pivot (`src/models.py`, pydantic v2).
2. `keywords.KeywordMatcher.match(item)` calcule `(matched_keywords, families)` à partir du haystack `title + summary + raw`. Seuls les items avec `matched_keywords` non vides sont conservés.
3. `store.upsert_many` inscrit en SQLite (`data/veille.sqlite3`, dédup par `hash_key`, `ON CONFLICT DO UPDATE` depuis R15).
4. `digest.build_digest(since_days=N)` construit le HTML et l'envoie (sauf `--no-email`).
5. `site_export._build` génère les JSON + pages Hugo. Depuis R22b, la première étape est `_filter_disabled_sources(rows)` — les rows dont le `source_id` est marqué `enabled: false` dans `config/sources.yml` sont écartés AVANT les `_fix_*_row` / dédup / filtre fenêtre.

Sous-commandes utiles : `python -m src.main run --since 7 --no-email -v` (pipeline complet sans mail), `python -m src.main dry -v` (fetch + match, pas d'écriture DB ni d'email), `scripts/reset_category.py <cat>` (purge ciblée avant re-ingest post-patch parser).

### Orchestration (GitHub Actions)

`.github/workflows/daily.yml` tourne tous les jours à 06:00 UTC, plus un trigger `push` sur `main` (auto-deploy ajouté en R18). Inputs `workflow_dispatch` : `since_days` (défaut `1`), `no_email` (`1` pour dry-run), `reset_db` (`1` purge complète avant run), `reset_category` (ex. `amendements`, purge ciblée).

Concurrence : `concurrency: group: veille-daily, cancel-in-progress: false` — les runs se queuent, ne s'interrompent pas. Persistance SQLite via `actions/cache` (clé `veille-sqlite-v3-${run_id}`, restore-key `veille-sqlite-v3-`). DB non trackée en git (> 100 Mo).

Auto-deploy Pages via `actions/deploy-pages@v4`. Le workflow commit en fin de job les caches `data/amo_resolved.json`, `data/an_texte_to_dossier.json` et `data/last_digest.html`.

### Ce qui tourne en prod (2026-04-23, après R22a poussé)

- **Label header** : `R22a · <short_sha>` (bump R19 → R22a en R22b, `SYSTEM_VERSION_LABEL` ligne 28 de `site_export.py`). Le label R19 hardcodé depuis R13-G sera remplacé au prochain déploiement de R22b.
- **37 sources actives** (sur 61 déclarées). Les désactivées en `enabled: false` : `senat_dosleg` et `senat_questions` (doublons R11g), `senat_ameli` (dump PostgreSQL, R11g), `senat_agenda` (403 WAF + 404 sub-paths, R16), `senat_agenda_print` (printable), `senat_theme_sport_rss` (doublons dosleg internes, R19-B), `min_sante` et `min_travail` (WAF, R11g), `cojop_alpes2030_news` (Google News parasite, R17) + entrée site COJOP (pas encore en ligne, R22).
- **Scope AAI/juridictions arbitré R22** : actifs — ARCOM, ANJ, AFLD, Défenseur des droits, Conseil d'État (RSS), Conseil constitutionnel (RSS QPC360 actualités + décisions), **Cour des comptes** (RSS `ccomptes.fr/rss/publications`, R22), **Autorité de la concurrence** (scraping HTML `/fr/communiques-de-presse`, R22). Hors scope définitif — Cour de cassation (JS-only), AMF, CNIL, HATVP, CADA, HCERES.
- **Nouvelles sources R20** : IGESR (rapports), INJEP (RSS). CC reclassé en `communiques` au lieu de `jorf` pour les actualités (les décisions publiées au JO restent en `jorf`).
- **Dédup dosleg R22a** : passes 2a → 2b → 2c avec cumul `raw._merged_dossier_ids` entre passes. Le winner de chaque fusion hérite des IDs de ses losers (extraits depuis `raw.dossier_id`, `raw.signet`, URLs, `raw.url_an`). La passe 2c peut ainsi toujours faire le bridge AN↔Sénat même si la passe 2a a écarté le seul item porteur de `url_an`. Cas de référence corrigé : JOP Alpes 2030 pjl24-630 × 4 occurrences → 1 seule fiche.
- **Filtre sources disabled R22b** : `_filter_disabled_sources(rows)` au début de `_build`, lecture yaml idempotente (retourne rows tels quels si yaml KO). Résout les items orphelins qui survivaient en DB après désactivation d'une source (Google News `alpes_2030_news`, 4 textes Sénat JOP 2030 via `senat_theme_sport_rss`).

### Volumes au dernier run propre (R22a)

Environ **45 000 items ingérés** / run reset_db. Matchés sport : questions ~15, communiques ~12 (MinSports, MinARMEES, CPSF, ANS, CE, CC, Cour des comptes), comptes_rendus ~10, dossiers_legislatifs ~10 (après dédup), agenda ~100 / 30j (majoritairement AN), amendements ~4 (AN 3 LFI PJL sécurité 52/56/57 + Sénat 1), jorf ~5.

### Tests

189 tests pytest verts (R22b — +4 nouveaux tests sur `_filter_disabled_sources`). Fixtures principales : `tests/test_keywords.py`, `tests/test_amo_loader.py`, `tests/test_agenda_normalize.py`, `tests/test_site_export_fixups.py`, `tests/test_sources_config.py`, `tests/test_site_export_disabled_sources.py` (R22b).

### Scripts macOS `.command`

- `push_R22.command` — stash runtime files → rebase → push → stash pop. Mode standard pour pousser une feature.
- `commit_R22b.command` — variante dédiée R22b : stash ciblé sur `data/ site/data/ site/static/search_index.json` via `git diff --quiet HEAD --` (détecte diff vs HEAD peu importe l'état d'indexation, fix du faux-négatif observé en session : le précédent `git diff --quiet --` ratait les runtime files déjà stagés, d'où le merge conflict au rebase).
- `reset_db_full.command` — dispatch `gh workflow run daily.yml -f reset_db=1 -f since_days=1` + nettoyage des runs GitHub Actions complétés (garde les 20 derniers via `gh run list | .[KEEP_LAST:] | gh run delete`).
- `run_clean.sh` (R21b) — lanceur local du pipeline sans email, pour itérer sur un patch parser sans attendre le cron CI.

---

## Décisions clés

**Site Hugo, pas de JS serveur.** Le pipeline Python génère des `.md` Markdown + frontmatter + JSON sous `site/content/items/<cat>/` et `site/data/*.json`, et Hugo 0.128 compile à plat. Le JavaScript côté client est limité à la recherche (`search_index.json`) et au text-fragment natif pour les CR.

**Fixups à l'export plutôt que migration DB** (pattern depuis R12). `site_export._fix_<cat>_row(r)` réécrit title/url/status en mémoire à l'export, pas dans la DB. Idempotent, couvre les items pré-patch sans reset_db. Justification : `upsert_many` ne touchait pas aux `hash_key` existants avant R15 et continue à ne pas re-normaliser les `matched_keywords` ou `raw.*` persistés. *Contrepartie :* `_fix_*_row` grossit à chaque rustine (10+ fonctions aujourd'hui), source principale de dette — cf. AUDIT_R19 §2.2.

**Filtre sources disabled au chargement de l'export** (R22b). Lire `config/sources.yml` en début de `_build` et écarter les rows dont le `source_id` est marqué `enabled: false`. *Pourquoi :* désactiver une source arrête le fetcher mais n'invalide pas les rows déjà en DB, qui continuent de s'afficher jusqu'à expiration de la fenêtre (30 à 180 jours). Applique AVANT `_fix_*` / `_filter_window` — économie CPU et clarté. Safe si yaml illisible (retourne le set vide). Alternative rejetée : purger la DB à chaque désactivation (casse le suivi historique + impose un `reset_db=1` par changement yaml).

**Dédup dosleg : trois passes avec cumul des IDs.** 2a par URL canonicalisée → 2b par intersection de word-set (INTERSECTION_MIN=5, WORDS_MIN=4, KEY_LEN_MIN=25) → 2c par `dossier_id` canonique. À chaque fusion, `_merge_ids_into_winner(w, loser)` stocke les IDs du loser dans `raw._merged_dossier_ids` du winner (R22a). Sans ce cumul, le bridge AN↔Sénat porté par `raw.url_an` d'un senat_akn_* disparaît si la passe 2a lui préfère un senat_promulguees plus récent. *Pourquoi pas une refonte `dossier_id` comme clé primaire :* c'est l'ambition de la Vague 3 AUDIT_R19 §4.4, mais elle implique de refondre le schéma DB + ajouter une fiche dossier + migrer les templates. R22a est la version minimale qui corrige le cas JOP Alpes 2030 sans ce chantier.

**Tiebreak `_prefer()` uniformisé** (R18). Ordre : date de publication desc → chambre Sénat (navette plus visible) → URL dossier-législatif officielle (`/dossier-legislatif/` ou `/dossiers/`) → a (premier rencontré). Appliqué à chaque fusion des 3 passes dosleg.

**Naïf UTC partout** (R11f). `parse_iso`, `_parse_date_any`, `_parse_dt` strippent la tz à l'entrée. Convention cohérente avec `main.py::_utcnow_naive`. Évite les `offset-naive vs offset-aware` sur les flux AN tz-aware.

**Pas de retry tenacity sur 4xx** (R11e). `_common._is_retryable` whitelist les 5xx + erreurs réseau. Économie ~16 s par source morte et log.error explicite. Le récap WARNING de `run_all` liste sources en erreur ET à 0 item — canari à surveiller dans les logs CI.

**`type: <cat>` sélectif dans les `_index.md`** (R13-K, R13-M). On pose `type: "<cat>"` uniquement pour les catégories qui ont un layout dédié (`agenda`, `dossiers_legislatifs`). Si on le met partout, Hugo filtre silencieusement les pages sans `date:` valide (cas `senat_promulguees` avec `published_at = None`) et n'affiche que 1-3 items.

**CNOSF via sitemap Drupal** (R13-M). Site Nuxt SPA, scraping HTML classique retourne 0 item. Dispatcher `html_generic._from_sitemap_generic` lit `/sitemap.xml` Drupal (statique), filtre les URLs contenant "actualite"/"news"/"communique" + cutoff 120j + titre reconstruit depuis slug.

**Agenda AN : liens vers page globale** (R13-J). Les permaliens unitaires `/dyn/17/reunions/RUANR...` sont cassés. Le template agenda force tous les items chamber="AN" vers `https://www2.assemblee-nationale.fr/agendas/les-agendas`. Autres chambres : source_url individuel conservé.

**`an_texte_to_dossier.json` persisté** (R13-F). Cache AN `texteLegislatifRef → titre dossier` (≈ 4438 entrées, ~500 Ko), écrit par `_normalize_dosleg` et lu par `_normalize_amendement`. `normalize.run_all` parallélise via ThreadPoolExecutor, donc sans pré-chargement il y aurait une race au 1er run après reset_db. Committé en fin de workflow comme `data/amo_resolved.json` et `data/last_digest.html`.

**Matcher lexical, pas de deuxième passe ML** (décision R22). Plutôt qu'ajouter une passe zero-shot pour les items borderline (risques : faux positifs, coût CI, dépendance lourde), l'axe retenu est d'enrichir `config/keywords.yml` quand un angle mort apparaît. Plus léger, plus déterministe, plus lisible dans les diffs.

---

## TODO

### Priorité haute

1. **Finaliser R22b** (en cours au moment de ce handoff) — commit + push du patch `_filter_disabled_sources` + bump label R19 → R22a, via `commit_R22b.command`. Le commit local 300eb75 est en état de rebase avorté suite à un conflit sur `data/last_digest.html` (runtime file pris dans le commit à cause d'un `git diff` qui ratait les fichiers stagés). Script patché : stash check via `git diff --quiet HEAD --` au lieu de `--`. Séquence de récupération documentée : `git rebase --abort && git reset HEAD~1 && git status --short`, puis re-run du script.
2. **Monter la vague 1 de l'audit — filet de sécurité.** Les trois briques : (a) monitoring pipeline (sources actives / en erreur / à 0 items + écart vs J-7) en fin de digest ; (b) suite de tests « incidents » rétroactive sur les rustines R13 → R22b (un test par régression pour fermer la porte) ; (c) contrat de source auto-généré (`tests/contracts/test_source_<id>.py` : ≥ 1 item / 30 j). Détail §5 vague 1 de `docs/AUDIT_R19.md`.
3. **Actes_timeline vide pour certains dosleg AN pré-2026** — le dossier laïcité 2025-07-09 signalé retiré le 9 juillet 2025 a `raw.actes_timeline = []` en DB, `_fix_dossier_row` ne peut pas détecter le retrait. Cause probable : structure JSON différente pour dossiers pré-XVIIe ou retirés tôt. À investiguer sur le JSON source.
4. **Alerte "source tombée à 0"** — intégrer dans le digest. Une source à 0 items alors qu'elle était ≥ 1 la veille = warning visible. 30 lignes de Python pour couper court à la moitié des incidents silencieux.

### Priorité moyenne

- **Vague 2 audit — `textclean.py` + persistance colonnes**. Extraire décodage bytes + strip HTML + strip bruit technique dans un module unique. Persister `snippet`, `dossier_id`, `canonical_url`, `status_label`, `content_hash` en DB pour ne plus recalculer à chaque export (moins de `_fix_*_row`, moins de points de régression).
- **CC → JORF** — vérifier que chaque décision CC classée en `jorf` a bien fait l'objet d'une publication au JO. Aujourd'hui le flux QPC360 "décisions" est reclassé par défaut. Ne pas mélanger "rendue publique sur CC" et "publiée au JO".
- **Procédure législative** — maîtriser dépôt → commission → séance → adoption → CC → promulgation avant de patcher `_map_code_acte` et `status_label`. Débloquerait le ticket "JO 2030 affiché Conseil Constit côté AN alors que promulgué".
- **Cache AMO évolutif** — fusionner le dump historique `/17/` avec `AMO10_deputes_actifs_mandats_actifs_organes_divises_XVII.json.zip` (organes actifs, mis à jour en quasi temps-réel) pour résoudre les POxxx / PAxxx ultra-récents absents du dump historique.
- **Agenda Sénat** — `senat_agenda` toujours désactivé (R16 : 403 WAF + 404 sub-paths). Réactivation nécessiterait Playwright stealth ou accord DSI Sénat. Surveillance manuelle tous les 1-2 mois pour détecter un retour du endpoint.
- **`SYSTEM_VERSION_LABEL` → fichier dédié.** Déplacer la constante de `site_export.py` ligne 28 vers un `version.py` lu en CI. Empêcher qu'un label reste figé pendant 10 tags (cas R13-G → R22b).

### Priorité basse

- **Vague 3 audit — modèle documentaire.** Fiche de dossier = document + événements rattachés. Refondre la dédup autour de `dossier_id` comme clé primaire, nouveau template Hugo, JSON schema versionné + CSS découpé par composant.
- **PISTE Légifrance OAuth2** — connecteur prêt (`src/sources/piste.py`) désactivé. Secrets à créer si JORF DILA se dégrade.
- **Text-fragment CR pour Firefox** — aujourd'hui l'URL `#:~:text=<kw>` fonctionne Chrome/Edge/Safari 16.4+ et dégrade silencieusement sur Firefox. Alternative : scraper les `<paragraphe id="X">` du XML Syceron à l'ingestion pour stocker une ancre absolue.
- **Logos AN/Sénat officiels** — SVG actuels sont stylisés maison (façade tricolore AN, arcs dôme Sénat). Cyril a fourni les PNG officiels mais le sandbox ne peut pas lire les fichiers attachés. Si fidélité exigée, déposer directement dans `site/static/logos/an.svg` et `/senat.svg`.
- **Coverage tests** `store.py`, `digest.py` partiels.

---

## Pièges connus

### Environnement local et shell

- **`python` vs `python3`** : macOS n'a que `python3`. Toujours activer le venv (`source .venv/bin/activate`) pour que `python` fonctionne.
- **Pas de commentaires `#` dans les blocs shell partagés** — zsh sans `INTERACTIVE_COMMENTS` casse le copier-coller. Règle persistante en mémoire.
- **FUSE mount sandbox** : le sandbox ne peut pas supprimer `.git/index.lock` / `.git/HEAD.lock` même en root. Utiliser `mcp__cowork__allow_cowork_file_delete` puis `rm -f` côté bash, ou passer par macOS.
- **Sandbox egress limité** : certains domaines (Wikimedia Commons, AN CDN Cloudflare) peuvent renvoyer 404/429. Pour tester une URL, privilégier `curl -sI` depuis le Mac.
- **`git diff --quiet HEAD --` vs `--`** : la variante sans `HEAD` ne voit que le diff working-vs-index. Pour détecter des runtime files stagés (cas du commit_R22b.command de cette session), comparer à HEAD. Écueil déjà rencontré en pleine session, documenté dans `commit_R22b.command`.
- **Stash ciblé** : `git stash push -u -m <msg> -- <pathspec>` ne stash que les pathspecs donnés. Précède par `git reset HEAD -- <pathspec>` si les fichiers sont déjà stagés (sinon le stash ne désindexe pas proprement).

### Données AN (Assemblée nationale opendata)

- **XSD AN 0.9.8 casse sensible** : `timeStampDebut` avec S majuscule. Certains vieux dumps utilisent `timestampDebut` (lowercase). Le parseur gère les deux + fallback `cycleDeVie.chrono.creation` (R13-L).
- **Codes AN vs libellés** — items référencent `PAxxx` (acteurs) / `POxxx` (organes). Sans cache AMO chargé, titres affichent les codes bruts. Dump `/17/amo/tous_acteurs_...` régénéré quotidiennement mais 24-48 h de retard sur les nouveaux POxxx.
- **Paths JSON amendements** (R11a) : `identification.numeroLong`, `corps.contenuAuteur.dispositif` + `exposeSommaire`, `signataires.auteur.groupePolitiqueRef`, `cycleDeVie.etatDesTraitements.etat.libelle`, `texteLegislatifRef`. R13-J a séparé `cycleDeVie.sort.libelle` (final) de `cycleDeVie.etatDesTraitements.etat.libelle` (transitoire) dans `raw.sort` / `raw.etat`.
- **Questions** : `indexationAN` (pas `indexationAnalytique`), `acteurRef` sans nom/prénom direct, `textesQuestion` liste. **R13-G patch 8** : priorité sujet `analyse > tete_analyse > rubrique`.
- **URL agenda AN unitaires cassées** — `/dyn/17/reunions/RUANR...` 404. Template agenda force AN vers `https://www2.assemblee-nationale.fr/agendas/les-agendas` (R13-J).
- **actes_timeline vide** — certains dossiers AN pré-2026 ingèrent `actes_timeline = []`. Empêche la détection automatique du retrait. Cause à investiguer (cf. TODO §1.3).

### Données Sénat

- **`senat_ameli.zip` = dump PostgreSQL** (pas CSV zip). Désactivé définitivement, remplacé par `senat_amendements` per-texte.
- **Format session** : Sénat Akoma Ntoso utilise `"25"` (2 chiffres), les CSV per-texte `"2025-2026"`. Conversion dans `senat_amendements._session_to_csv`.
- **CSV per-texte** : TAB-delimited, ligne 1 hint `sep=\t`, encoding cp1252 (fallback utf-8-sig + utf-8+replace). Séance : `https://www.senat.fr/amendements/<session>/<num>/jeu_complet_<session>_<num>.csv`. Commission : `/commissions/<session>/<num>/jeu_complet_commission_<session>_<num>.csv`.
- **404 = normal** : beaucoup de textes sans amendements encore. `_try_fetch` silencieux.
- **Budget fetch** : `_MAX_TEXTS_PER_RUN = 300`, `_MAX_AMDT_PER_TEXTE = 2000`.
- **Agenda Sénat 403 WAF** — `/agenda/` bloqué UA Chrome, sub-paths `/Global/agl{DDMMYYYY}Print.html` en 404. Source disabled R16. Ne pas rouvrir avant passage Playwright stealth.
- **Encoding ISO-8859-15** (R19-A) — certains flux Sénat sortent en ISO-8859-15. Passer les `bytes` à feedparser (pas le string déjà décodé) : feedparser détecte l'encoding via la PI XML. Le `.replace("ï¿œ", "œ")` masquait le symptôme, pas la cause.
- **`senat_theme_sport_rss` désactivé** (R19-B) — remontait les documents INTERNES d'un dossier (pjl + rapports + avis pour un seul dossier → 8 occurrences). Pas de handler pour discerner le dossier parent des pièces. Source conservée `enabled: false` en yaml pour mémoire, items orphelins écartés par `_filter_disabled_sources` (R22b).

### Matching et catégories

- **Amendement ≠ texte du dossier** — l'amendement ne cite rarement les mots-clés sport. Enrichir le haystack avec le titre du dossier parent via `an_texte_to_dossier.json` (R11b). Match tombe sur le thème porté par le dossier.
- **Priorité summary** : titre du dossier EN PREMIER, puis objet, puis dispositif. Sinon le dispositif (long, générique) domine l'extrait.
- **Kws ANS et ARCOM nus retirés** — `ANS` → unidecode "ans" → faux positifs massifs. `ARCOM` seul → faux positifs audiovisuel. Seulement formes étendues.
- **`STRICT_DATED_CATEGORIES = {"communiques", "dossiers_legislatifs"}`** — pas de fallback `inserted_at`. Élimine rapports Sénat CSV sans date, pages pivot html_generic, agendas hebdo datés en fin de semaine à venir. Pour dosleg : `senat_promulguees` (≈ 11 matchés sport) filtrés car `published_at = None`.

### Sources gouv.fr et réseau

- **Redirects 301 qui changent de scheme** — certains sites `.gouv.fr` renvoient un 301 vers un path sans trailing slash qui transite par `http://` avant de remonter en `https://`. `httpx follow_redirects=True` gère mais pas toujours proprement (observé R15 sur MEAE + Justice). Règle : dès qu'un `curl -I -L` montre un redirect 301 gouv.fr, remplacer l'URL dans `sources.yml` par la destination canonique directe.
- **Sitemap Élysée `<lastmod>` factice** — les 6 sitemaps `elysee.fr` ont tous leurs `<lastmod>` identiques (`2026-03-18`, jour de la refonte). Impossible de filtrer par récence. Utiliser `/feed` (RSS 2.0) à la place.
- **TYPO3/Drupal refontes silencieuses** — ministères refactorent leur CMS sans annonce ni redirect garanti. Routine : `scripts/audit_sources.py` en hebdo pour détecter les passages à 0 items / 404.
- **curl_cffi `impersonate: true`** — ajouté R18 pour les sites Cloudflare qui bloquent httpx. 6 ministères réactivés grâce à ça. Config par source via le flag yaml `impersonate: true`.

### Hugo / Site export

- **Store ne persiste pas `snippet`** — rebuild à la volée via `KeywordMatcher.build_snippet` dans `_load`. À corriger vague 2 audit.
- **`upsert_many` depuis R15 fait `ON CONFLICT DO UPDATE`** — les items re-ingérés après patch parser mettent à jour leur row. **Mais** les `matched_keywords` / `raw.*` recalculés ne sont update QUE si le `hash_key` change. Pour une refonte parser lourde, le `reset_db=1` reste nécessaire.
- **Fixups in-memory = insurance policy** — chaque `_fix_<cat>_row` dans `site_export.py` gère le delta DB legacy / parser courant à l'export. Après reset_db, les fixups ne touchent plus rien.
- **Slugs et doublons .md** (R12a) — slugs dépendent du titre. Quand un fixup réécrit le title, le slug change. `shutil.rmtree(items_dir)` au début de chaque export.
- **`type: <cat>` dans frontmatter** — à n'utiliser QUE pour les catégories ayant un layout Hugo dédié (`agenda`, `dossiers_legislatifs`). Sinon Hugo filtre silencieusement les pages sans `date:` valide.
- **Hugo `{{ with }}` change le contexte `.`** — pour accéder à `$.Params.<autre>` dans un bloc `with`, utiliser `$.` explicite. Alternative plus robuste : `{{ if }}` qui n'altère pas `.`. Cause du bug sort_slug vide pré-R13-O.
- **Text-fragment** `#:~:text=<kw>` fonctionne Chrome/Edge/Safari 16.4+, dégrade silencieusement sur Firefox.
- **`SYSTEM_VERSION_LABEL` hardcodé** — ligne 28 de `site_export.py`. Écueil historique : resté figé à R13-G pendant 10 tags (R14 → R22). À déplacer dans un `version.py` lu en CI (cf. TODO priorité basse).

### Dédup dosleg

- **Trois passes empilées, facile à casser quand une source nouvelle arrive.** Ordre sensible : 2a URL canonicalisée → 2b intersection de word-set → 2c dossier_id. Modifier l'une sans vérifier les autres = régression JOP 2030 typique.
- **Seuils sémantiques en dur** (R18) : `INTERSECTION_MIN = 5`, `WORDS_MIN = 4`, `KEY_LEN_MIN = 25`. Protège contre les clés trop courtes (« esport responsable » → bag-of-words vide). Ne pas baisser sans tests.
- **`_merge_ids_into_winner` indispensable** (R22a) — sans ça, le bridge AN↔Sénat via `url_an` d'un senat_akn_* disparaît quand un senat_promulguees gagne le tiebreak en passe 2a. Symptôme : 2 items (AN orphelin + Sénat gagnant) au lieu d'1 fiche.
- **Tiebreak `_prefer()` non commutatif** — l'ordre d'évaluation compte. Date desc → Sénat → URL dosleg officielle → a. Tester avec les deux ordres `_prefer(a, b)` / `_prefer(b, a)` si on ajoute une priorité.

### Méthodologie

- **Doc officielle avant diag** — XSD AN, schemas Sénat, parser communautaire reconnu d'abord, puis tâtonnement. Règle persistante en mémoire.
- **Toujours `curl -I` une URL AN opendata** avant de la mettre dans `sources.yml`. Paths changent sans préavis (cas `amendements_legis` → `amendements_div_legis` en R11d). Noter la date de vérif en commentaire yaml.
- **Procédure législative comme prérequis** — Cyril veut qu'on maîtrise les étapes (dépôt → promulgation) avant de patcher tri/filtre dossiers. Règle persistante en mémoire.

---

## Historique

- 2026-04-23 : R22 (arbitrages AAI : Cour des comptes RSS + Autorité concurrence HTML ; Cassation/AMF/CNIL/HATVP/CADA/HCERES hors scope ; audit R19 étoffé). R22a (dédup dosleg : cumul `raw._merged_dossier_ids` aux passes 2a/2b pour préserver le bridge AN↔Sénat lu par la passe 2c — fix JOP Alpes 2030 pjl24-630). R22b (bump `SYSTEM_VERSION_LABEL` R19 → R22a + nouvelle fonction `_filter_disabled_sources` qui écarte à l'export les rows dont le `source_id` est disabled dans yaml ; 4 tests ; évite d'attendre l'expiration 30-180 j de la fenêtre après désactivation d'une source). Script `commit_R22b.command` avec stash ciblé runtime (`data/ site/data/ site/static/search_index.json`) via `git diff --quiet HEAD --` pour capter les diffs stagés.
- 2026-04-22 (fin d'après-midi) : R13-O → R15 → R16. Agendas ministériels (`min_educ_agenda`, `min_esr_agenda`, scraper `min_sports_agenda_hebdo`, handler `data_gouv_agenda`). `store.upsert_many` passe en `ON CONFLICT DO UPDATE`. Fix 6 sources à 0 items : `senat_agenda` désactivé (403 WAF) compensé par `senat_theme_sport_rss`, `elysee_sitemap` remplacé par `elysee_feed`, handler `_from_agenda_html` pour Élysée Agenda, URLs canoniques MEAE + Justice. Fix `SyntaxWarning \s` dans docstring site_export.py.
- 2026-04-21 : R13-L. Questions titre sans auteur (inline cliquable avant). Amendements fallback `raw.statut`. Agenda fenêtre 30 j, fallback `cycleDeVie.chrono.creation`, ODJ `resumeODJ.item`. Sidebar agenda inline. Dédup dosleg URL + Sénat prioritaire. Détection "Retiré" via `raw.is_retire`.
- 2026-04-20 : R11 → R13. Fix AN amendements (`amendements_div_legis`). Pivot Sénat amendements per-texte. Tz-bug `an_agenda` corrigé (naïf UTC partout). No-retry 4xx. UX/UI site (grille + sidebar + recherche). Cache `an_texte_to_dossier.json` persisté.

### Historique des refactorings majeurs (ancienne forme, conservée pour traçabilité)

| Tag | Description | Commit |
|---|---|---|
| R22b | `SYSTEM_VERSION_LABEL` R19 → R22a (hardcodé depuis R13-G). Nouvelle fonction `_filter_disabled_sources(rows)` dans `site_export._build` : lit `config/sources.yml`, écarte les rows dont `source_id ∈ disabled`. Idempotent et safe si yaml KO. 4 tests (`tests/test_site_export_disabled_sources.py`). Résout : items Google News (`alpes_2030_news` R17) + 4 textes Sénat JOP 2030 (`senat_theme_sport_rss` R19-B) qui survivaient en DB après désactivation. | (pending push) |
| R22a | Dédup dosleg : ajout `_merge_ids_into_winner(w, loser)` appelé à chaque fusion des passes 2a et 2b. Le winner cumule les IDs des losers dans `raw._merged_dossier_ids`, visibles par la passe 2c (`_item_dossier_ids`). Corrige le cas JOP Alpes 2030 : senat_promulguees gagnant en 2a écrase le senat_akn_* porteur du `url_an` → passe 2c perdait le lien vers DLR5L17N52100 (AN) → 2 items orphelins. | (pending push) |
| R22 | Arbitrage AAI/juridictions : Cour des comptes réactivée via RSS `ccomptes.fr/rss/publications` (remplace scraping HTML `/fr/publications` timeout 3×60 s). Autorité de la concurrence ajoutée par scraping HTML `/fr/communiques-de-presse` (pas de RSS officiel). Cassation / AMF / CNIL / HATVP / CADA / HCERES sortis du scope. Alpes 2030 : pas de source tant que COJOP n'est pas en ligne. Audit R19 étoffé avec arbitrages + reco matcher lexical. | `7d3ca26` |
| R21 / R21b | UX mobile + titre. Script `run_clean.sh` (lanceur local du pipeline sans email). | — |
| R20 | IGESR (rapports) + INJEP (RSS). CC reclassé en `communiques` pour les actualités (les décisions au JO restent en `jorf`). | — |
| R19-A..H | 8 rustines sur encoding (ISO-8859-15 Sénat), dédup dosleg, codes PA dans snippets, agenda sidebar/title, suppression Google News, purge DB résiduelle, snippets CR sans préambule Syceron, CC→JORF. | — |
| R18 | curl_cffi `impersonate: true` : 6 ministères Cloudflare-bloqués réactivés. Auto-deploy trigger push dans daily.yml. `_prefer()` tiebreak uniformisé dosleg. | — |
| R17 | Refonte dédup dosleg : URL canonicalisée (strip scheme) + sémantique bag-of-words avec seuils stricts. Alpes 2030 Google News écarté. | — |
| R16 | Correction des 6 sources à 0 items identifiées par R15. `senat_agenda` désactivé (403 WAF + 404 sub-paths), compensé par `senat_theme_sport_rss` (flux RSS thème 31). `elysee_sitemap` (10 URLs nav seulement) remplacé par `elysee_feed` (RSS `/feed`). `elysee_agenda` : handler dédié `_from_agenda_html` qui connaît `a.newsBlock-grid-link` + date FR texte. `min_justice` : URL canonique `/espace-presse` (plus de maintenance). `min_affaires_etrangeres` : URL canonique directe pour éviter le 301. Fix SyntaxWarning `site_export.py:839` (`\\s` au lieu de `\s` dans docstring). | — |
| R15 | Agendas ministériels : `min_educ_agenda` (dataset OpenDataSoft `fr-en-agenda-ministre-education-nationale` via `data.education.gouv.fr`, contournement du Cloudflare `education.gouv.fr`), `min_esr_agenda` (`fr-esr-agenda-ministre`, flux arrêté upstream). Scraper dédié `min_sports.py::min_sports_agenda_hebdo` (URL stable `sports.gouv.fr/`, parse bulletin hebdo `<h5>`/`<p>`). Handler `data_gouv_agenda` dans `data_gouv.py` (schéma OpenDataSoft `{uid, dtstart, dtend, description}`). `store.upsert_many` : `ON CONFLICT (hash_key) DO UPDATE` (re-ingestion sans reset_db). Menu reclassé. | `7950d1f` |
| R13-O-logos | SVG reconstruits plus fidèles aux logos officiels AN + Sénat (Cyril a fourni les PNG en image, sandbox ne pouvait pas lire les fichiers joints). AN : façade Palais Bourbon tricolore + texte bleu. Sénat : arcs dôme + bandes tricolores + texte serif noir. | `62528a1` |
| R13-O | Amendements : auteur retiré du titre (affiché avant via `.auteur-inline` cliquable comme pour questions) dans parsers AN + Sénat + fixup legacy. Fix `data-sort` vide qui empêchait le chip "Adopté" de passer en vert : passage de `{{ with .Params.sort_label }}` à `{{ if }}` pour ne pas altérer le contexte `.` d'accès à `sort_slug`. | `ca892bf` |
| R13-N | Parser dosleg AN : nouveau flag `raw.is_retire` détecté par scan des codeActe / libelleActe pour patterns `RETRAIT` / `CADUCITE` / `RENVOI` / "retirée". Se propage dans raw → `_fix_dossier_row` pose `status_label="Retiré"` → CSS `.status-retire` (fond rouge foncé). Limitation : certains dosleg pré-2026 ingèrent `actes_timeline = []`, la détection ne peut rien faire (cas laïcité 2025-07-09). min_sports_agenda désactivé (source non exploitable, bulletin hebdo unique). | `e26ca0d` |
| R13-M | Logos AN/Sénat SVG 56×56 (`site/static/logos/`) + réactivation layout `dossiers_legislatifs/list.html` via `SPECIFIC_LAYOUT_CATS`. Nouveau dispatcher dans `html_generic.fetch_source` pour formats `rss` (feedparser) et `sitemap` (lxml) — permet d'ajouter Conseil d'État (RSS `/rss/actualites-rss`), Conseil Constitutionnel QPC360 (actualités + décisions), Cour de Cassation (HTML), CNOSF réactivé via `sitemap.xml` Drupal (pas /actualites SPA). Sidebar agenda : `display: flex; align-items: baseline` sur `.side-body` pour que chambre + titre soient vraiment inline sur la même ligne. | `8670ab3` |
| R13-L (fix2) | Revert du dédup dosleg bag-of-words (trop agressif, effondrait 7/8 dosleg sur la prod en 1 seul). Retour au dédup par URL simple avec Sénat prioritaire en cas d'égalité de date. Accepte le doublon JO 2030 AN+Sénat temporairement. | `31171e9` |
| R13-L | Questions : auteur + groupe retirés du titre (parser + fixup regex), affiché AVANT le titre via `.auteur-inline` avec barre verticale `\|` séparateur (demande Cyril "même ligne, cliquable"). Amendements : fallback `raw.statut` pour le chip sort/état des items legacy. Agenda : fenêtre 30j (au lieu de 90), fallback date sur `cycleDeVie.chrono.creation` quand `timeStampDebut` absent, extraction ODJ `resumeODJ.item` quand `main_title` vide. Sidebar agenda : chambre + titre inline. Dédup dosleg par URL (passe simple, Sénat prioritaire à date égale). Détection "Retiré" dans `_fix_dossier_row` via `raw.is_retire` / `status_label` / `actes_timeline`. `type: dossiers_legislatifs` retiré du frontmatter (Hugo filtrait à 1 item). | `600bb03` |
| R13-K | CR : retour à liste plate (plus de séparation AN/Sénat, plus de badge "intégral/analytique"). Snippet CR 250 → 500. Text-fragment `#:~:text=<kw>` ajouté à `source_url` pour que Chrome/Edge/Safari saute directement à la 1re occurrence sport dans la page AN/Sénat. Fix publications manquantes : `type: <cat>` limité à `agenda` + `dossiers_legislatifs` (Hugo filtrait les .md sans `date:` valide quand `type:` custom). | `481ab76` |
| R13-J | Batch 16 patches UX Cyril : hover nav rouge (imite Rechercher), méta MAJ/version déplacée dans le header (droite), questions titre sans date, liens externes `target="_blank"`, titre agenda inline après badge chambre, fusion agenda (ajout `senat_agenda`), liens agenda AN → www2.assemblee-nationale.fr/agendas/les-agendas, auteur inline `.auteur-inline` avant titre, cartouche blanc arrondi `.items > li` pour pages listing, CNOSF différé (SPA), snippets 500/500/250 re-vérifiés, logo 90×90, TODO cache AMO évolutif documenté, sort/état amendement en `.sort-chip` coloré (rouge/vert/gris/noir) après la date, stockage `sort_label`+`sort_slug` dans frontmatter via `_write_item_pages`. | `f8466e1` |
| R13-H2 | `an_agenda.since_days`: 30 → 90 pour aligner sur `WINDOW_DAYS_BY_CATEGORY["agenda"]`. Récupère le backlog 30-90j perdu à chaque reset DB. | `2ea97ab` |
| R13-H | Plus de "Réunion (POxxx)" code brut dans le titre agenda AN. Parser `_normalize_agenda` : fallback "Réunion" / "Réunion de commission" tout court. `_fix_agenda_row` enrichit à l'export : résolution AMO si possible, sinon "Réunion [de commission] AN du DD/MM/YYYY", dernier recours "Réunion parlementaire". Guard parser élargie à `organe_ref` seul pour ne pas skipper les items enrichissables. | `ae4a539` |
| R13-G | 15 patches UX Cyril en un batch : sidebar meta (date MAJ + version système via site.Data.meta), recherche en bleu + crème, agenda 3 lignes via line-clamp + date rouge, Questions titre = analyse au lieu de rubrique, dosleg home 6 mois + libellés "Depuis X jours" / "Mis à jour depuis moins de 6 mois", JORF → "Journal Officiel", kw-tag #8E5A44 sans margin, Sénat violet #a85c9e, MinSports doré #d7b800, "Amendement n°" → "Amdt n°", "Séance AN du" → "Séance du", WWW → MinARMEES et autres ministères. Skip patch 12 (CR ancre, R14 dédié) et patch 15 (statut JO 2030, bloqué par procédure législative). | `8686964` |
| R13-F | Persistance de `data/an_texte_to_dossier.json` (4438 entrées, ~500 Ko) entre runs via le `git add` du workflow. Fixe la race au 1er run après reset_db entre `_normalize_dosleg` et `_normalize_amendement` (ThreadPoolExecutor parallèle). | `10f8e6a` |
| R13-E | Backfill amendements AN (`_fix_amendement_row` résout `Député PAxxx` résiduels pour les items pré-R13-A) + `KeywordMatcher.recapitalize` qui remappe les `matched_keywords` sur la forme affichable du yaml. First-wins dans l'index keywords pour préférer la variante accentuée. | `253dde5` |
| R13 | Bilan visuel Cyril : refresh cache AMO /17/ (10 députés XVIIe résolus, 3108 acteurs / 10790 organes), kws capitalisés dans `keywords.yml` avec séparateur `\|` entre tags, dépollution HTML des summaries Sénat amendements, extraits conditionnels par catégorie, statut dosleg en fond doré crème. | `03a8bbd` |
| R12a | Purge `content/items` à l'export + applique les fixups au digest. | `7db4d59` |
| R12 | Volet UX/UI en réponse aux 5 chantiers Cyril : agenda (liens+titres+taille), dossiers légis (3 ans + statut + majuscule), dates/mots-clés restylées, questions épurées + AMO résolu, CR visibles + extraits (`snippet` reconstruit à l'export, bug historique du schéma SQL qui n'a jamais persisté `snippet`). | `bd4a730` |
| R11g | Audit sources post-R11e : désactive doublons Sénat (`senat_dosleg`, `senat_questions`), désactive WAF-bloqués (`min_sante`, `min_travail`), désactive SPA Nuxt (`cnosf`), fix URL MEAE refondue (`/presse-et-ressources/decouvrir-et-informer/actualites/`). | `3c7648c` |
| R11f | Fix tz-bug `an_agenda` : `parse_iso` / `_parse_date_any` / `_parse_dt` normalisent en naïf UTC au lieu de propager l'aware ISO 8601. Convention "tout naïf" du projet enfin homogène. | `afa6cd7` |
| R11e | Durcissement `fetch_bytes_*` : no-retry 4xx, log.error explicite, récap `run_all`. A révélé 3 bugs latents auparavant invisibles (diplomatie 404, an_agenda tz-bug, 8 sources 0-items). | `f30ce2b` |
| R11d | Fix URL AN amendements (`amendements_legis` 404 → `amendements_div_legis`). Validé : 5683 AN ingérés, 3 matchés sport (Amdts 52/56/57 LFI). | `44a090e` |
| R11 | Fix AN amendements parser + pivot Sénat per-texte | `9c4af6b` |
| R10 | Publications : fenêtre 90j + strict published_at + réactive ANS | `c7c6f2e` |
| R9d | CR AN : date séance + thème depuis XML Syceron | `39c5fdb` |
| R8 | Layout site grille central + sidebar + recherche | (série R8a/b) |
| R7 | Padding thématiques homepage | — |
| R6 | Majuscules Sénat + dates manquantes | — |
| R5 | Auteur question code PA… résolu | — |
| R3 | CR non conformes + liens cassés | — |
| R2 | 0 amendements matchés (première itération — reprise en R11) | — |
| R1 | Menu thématiques en ligne avec titre | — |
| P0-P12 | Refonte UX/UI site + correction connecteurs | `84ab995` |
