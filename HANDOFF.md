---
title: Veille Parlementaire Sport — Handoff
maintainer: Cyril Mourin
last_updated: 2026-04-22 (matin, après R13-H → R13-O)
---

# Handoff — Veille Parlementaire Sport

Ce document est le point d'entrée pour reprendre le projet sans contexte préalable. Il résume l'architecture, l'état au **2026-04-22 (matin, après R13-H → R13-O)**, les décisions prises, ce qu'il reste à faire et les pièges connus.

À lire dans l'ordre : §1 (quoi) → §2 (comment ça tourne) → §3 (où on en est) → §4 (décisions) → §5 (TODO) → §6 (pièges) → §7 (autonomie sandbox) → §9 (historique).

---

## 1. Ce que fait le projet

Agrégation automatisée de la production institutionnelle française (Parlement, Élysée, Matignon, ministères, JORF, autorités indépendantes, instances sportives) filtrée sur un dictionnaire de mots-clés sport. Deux livrables :

1. **Email quotidien** à 06:30 Europe/Paris (`digest.py`, template Jinja2).
2. **Site statique Hugo** publié sur `https://veille.sideline-conseil.fr` (GitHub Pages).

Catégories Follaw.sv couvertes : dossiers législatifs, JORF, amendements, questions, comptes-rendus, publications, nominations, agenda, communiqués.

Sources exclusivement officielles et publiques — pas de scraping de réseaux sociaux.

---

## 2. Comment ça tourne

### 2.1 Pipeline

`src/main.py run --since N` orchestre :

1. `normalize.run_all` itère `config/sources.yml` et appelle pour chaque source le connecteur approprié (`src/sources/*.py`). Retourne une liste d'`Item` pivot (`src/models.py`, pydantic v2).
2. `keywords.KeywordMatcher.match(item)` calcule `(matched_keywords, families)` à partir du haystack `title + summary + raw`. Seuls les items avec `matched_keywords` non vides sont conservés.
3. `store.upsert_many` inscrit en SQLite (`data/veille.sqlite3`, dédup par `hash_key`).
4. `digest.build_digest(since_days=N)` construit le HTML et l'envoie (sauf `--no-email`).
5. `site_export` génère les JSON + pages Hugo pour le site.

Deux sous-commandes :
- `python -m src.main run --since 7 --no-email -v` : pipeline complet sans mail (usage local pour valider).
- `python -m src.main dry -v` : fetch + match, pas d'écriture DB ni d'email.

### 2.2 Orchestration (GitHub Actions)

`.github/workflows/daily.yml` tourne tous les jours à 06:00 UTC. Inputs `workflow_dispatch` :

- `since_days` (défaut `1`)
- `no_email` (`1` pour dry-run)
- `reset_db` (`1` purge complète avant run, utile après refacto parser)
- `reset_category` (ex. `amendements` — purge ciblée avant run, utilise `scripts/reset_category.py`)

Persistance SQLite via `actions/cache` (clé `veille-sqlite-v3-${run_id}`, restore-key `veille-sqlite-v3-`). DB non trackée en git (>100 Mo).

### 2.3 Connecteurs

| Source | Fichier | Format(s) |
|---|---|---|
| AN open data | `src/sources/assemblee.py` | `json_zip` (Dossiers_Legislatifs, Amendements, Questions_*, Agenda) |
| Sénat dosleg CSV | `src/sources/senat.py` | `csv`, `csv_zip` |
| Sénat Akoma Ntoso | `src/sources/senat_akn.py` | `akn_index` (depots.xml, adoptions.xml) |
| Sénat amendements per-texte | `src/sources/senat_amendements.py` | `akn_discussion` (depots.xml → jeu_complet_*.csv) |
| Élysée | `src/sources/elysee.py` | sitemap.static.xml |
| JORF DILA | `src/sources/dila_jorf.py` | `xml_zip` dump hebdo |
| PISTE Légifrance | `src/sources/piste.py` | OAuth2 API (désactivé par défaut, secrets requis) |
| Ministères + autorités | `src/sources/html_generic.py` | scraping HTML générique |

Le routeur est dans `src/normalize.py::_dispatch` (dispatche par `group` + `format`).

### 2.4 Cache AMO

`data/amo_resolved.json` (~100 Ko, tracké en git) : mapping `PAxxx`/`POxxx` → civ+prénom+nom / libellé d'organe. Refresh hebdo via `scripts/refresh_amo_cache.py` (étape `Refresh AMO cache (weekly)` dans `daily.yml`). `src/amo_loader.py` expose les accesseurs.

Depuis R11b : un second cache `data/an_texte_to_dossier.json` mappe `texteLegislatifRef` → titre du dossier parent. Utilisé pour enrichir les amendements côté AN (et équivalent côté Sénat via `bill.akn.xml`).

### 2.5 Configuration

- `config/sources.yml` — 51 sources déclarées, groupées par émetteur. Champ `enabled: false` pour désactiver sans supprimer.
- `config/keywords.yml` — 5 familles : `acteur`, `dispositif`, `evenement`, `federation`, `theme`. Matching insensible casse + accents.

### 2.6 Tests

`pytest` — 44 tests verts au dernier run (R11). Fichiers :

- `tests/test_keywords.py` — matcher
- `tests/test_amo_loader.py` — cache AMO
- `tests/test_refresh_amo_cache.py` — script de refresh
- `tests/test_agenda_normalize.py` — XSD AN 0.9.8 (séance + commission)
- `tests/test_digest.py` — assemblage HTML

---

## 3. État au 2026-04-22 (matin, après R13-H → R13-O)

### 3.1 Dernier commit et version prod

Tout pushé sur `origin/main`. Version système affichée en header (dans `.nav-meta`) : **R13-O · {short_sha}**.

```
62528a1 R13-O-logos — SVG plus fidèles aux logos officiels AN + Sénat
ca892bf R13-O — amendements : auteur retiré du titre + fix sort_slug vert
e26ca0d R13-N — détection retrait dosleg + désactivation min_sports_agenda
8670ab3 R13-M — logos AN/Sénat, CE/CC/Cassation RSS, CNOSF sitemap, sidebar agenda flex inline
d002faa R13-L bump SYSTEM_VERSION_LABEL → R13-L
600bb03 R13-L — questions format, dedup dosleg, agenda 30j, sidebar inline, chip Adopte fallback statut
481ab76 R13-K — CR liste plate + snippet 500 + text-fragment + fix publications manquantes
```

**122 tests verts** — 91 baseline + 10 R13-E + 15 R13-G + 5 R13-H (agenda Réunion PO) + 1 R13-J (date dupliquée questions).

Plusieurs `reset_db=1 no_email=1` se sont enchaînés pour re-normaliser la DB à chaque évolution parser (derniers : R13-M run `24744176282` puis R13-N run `24747245510` puis R13-O run `24747816253`). Les fixups in-memory côté export (`_fix_*_row`) sont cumulés mais ne devraient avoir à retoucher que marginalement les items frais.

### 3.2 Ce qui fonctionne en prod

**Header + navigation**
- Logo Sideline 90×90 (R13-J patch 13), bande tricolore rouge sous le header.
- Nav avec hover rouge type "Rechercher" sur toutes les entrées sauf Rechercher lui-même (R13-J patch 1).
- Méta MAJ + version système poussés à droite du menu (`.nav-meta`), passage à la ligne en viewport réduit (R13-J patch 2).
- Entrée "Journal Officiel" au lieu de "JORF" (R13-G patch 7).

**Page d'accueil**
- Bloc "Dernières 24 h" + sections thématiques `<details>` pliables avec libellé "Depuis X jours" ou "Mis à jour depuis moins de 6 mois" pour dosleg (R13-G patches 6 + 6bis).
- Cartes blanches arrondies bordurées pour chaque occurrence (R13-G patch 10, sélecteur `.listing .items > li`).
- Dates en rouge bold format long "JJ mois complet AAAA" via tableau `$monthsFR` Hugo (R13-G patch 3).

**Publications** (catégorie `communiques`)
- Sources actives : MinSports presse + actualités, ANS, AFLD, ARCOM, ANJ, Cour des comptes, DDD, CPSF, ministères via html_generic (MinARMEES, MinJUSTICE, MinCULTURE, MinECOLOGIE…), Élysée, senat_rss, senat_rapports.
- **Nouvelles sources R13-M** : `conseil_etat` (RSS `/rss/actualites-rss`), `conseil_constit_actualites` (RSS QPC360), `conseil_constit_decisions` (RSS QPC360 classé en JORF), `cour_cassation` (HTML scraping), `cnosf` (sitemap.xml Drupal au lieu du SPA Nuxt).
- Chamber badges : MinARMEES (doré), MinSports (`#d7b800`), Sénat violet (`#a85c9e`), CE, CC, Cassation.

**Agenda**
- Sources : `an_agenda` (opendata AN), `matignon_agenda`, `senat_agenda` (HTML ajouté en R13-J).
- Fenêtre 30j retour (R13-L) — récupère futurs + 30 derniers jours.
- Titres AN informatifs post-R13-H : "Commission des affaires culturelles…", "Séance n°138 — Discussion de la PPL…". Plus de "Réunion (POxxx)" brut. Fallback ODJ `resumeODJ.item` si `main_title` vide (R13-L).
- Sidebar : chambre + titre en flex baseline sur **la même ligne** (R13-M), 3 lignes max via line-clamp, date en rouge à gauche.
- Liens AN pointent vers `https://www2.assemblee-nationale.fr/agendas/les-agendas` (patch 7 R13-J — liens individuels cassés).

**Dossiers législatifs**
- 10 items visibles en moyenne (post-reset_db R13-N). Fenêtre 1095j côté page, 180j côté home.
- Layout cards dédié `layouts/dossiers_legislatifs/list.html` avec logos **SVG locaux 56×56** : `/logos/an.svg`, `/logos/senat.svg`, `/logos/dossier.svg` (R13-M + R13-O-logos). Reconstruits à la main en reproduisant la façade Palais Bourbon tricolore (AN) et les arcs dôme + bandes (Sénat).
- Parser AN `_normalize_dosleg` pose un flag `raw.is_retire` quand un acte porte un code/libellé RETRAIT/CADUCITE/RENVOI (R13-N) → `_fix_dossier_row` réécrit `status_label="Retiré"` qui déclenche le CSS `.status-retire` (fond rouge foncé). Mais **actes_timeline est vide** pour certains dosleg pré-2026 (ex. laïcité 2025-07-09) → la détection ne se déclenche pas. Cause à investiguer, cf. §5.

**Questions**
- Titre : `{type} : {sujet}` (date + auteur retirés). Priorité `analyse > tete_analyse > rubrique` (R13-G patch 8) → "Soutien financier aux associations sportives" au lieu de juste "sports".
- Auteur en lien cliquable AVANT le titre via `.auteur-inline` (target="_blank" vers fiche député/sénateur), barre verticale `|` comme séparateur (R13-L).

**Amendements**
- Titre épuré : `Amdt n°X · art. Y · sur « dossier »` (plus d'auteur, groupe, statut `[Discuté]`). Auteur cliquable en `.auteur-inline` devant (R13-O).
- Chip sort/état coloré après la date : rouge (rejeté), **vert (adopté)**, gris (non soutenu), noir (irrecevable), bleu par défaut. Template `_default/list.html` utilise maintenant `{{ if }}` au lieu de `{{ with }}` pour que `data-sort="adopte"` ne se vide pas (R13-O).

**Comptes rendus**
- Liste plate (plus de séparation AN/Sénat ni badge "intégral/analytique") — R13-K.
- Snippet 500 chars au lieu de 250 (R13-K).
- Lien `source_url` enrichi avec text-fragment `#:~:text=<1er kw matché>` — Chrome/Edge/Safari 16.4+ saute directement à la 1re occurrence sport dans la page AN/Sénat (R13-K).

**CSS / palette**
- `.kw-tag` : couleur `#8E5A44`, margin 0, séparateur ` | ` (R13-G patches 8 + 13).
- `.sort-chip` : rouge/vert/gris/noir selon `data-sort` (R13-J patch 16).
- Chamber Sénat violet `#a85c9e` (R13-G patch 9), MinSports doré `#d7b800` (R13-G patch 14), badges MinARMEES/MinJUSTICE/etc. automatiques via `html_generic._chamber` (R13-G patch 17).
- Statut "Retiré" prêt côté CSS (`.status-retire`, fond `#8a1a1a`) mais non déclenché pour l'instant.

### 3.3 Volumes DB actuels

Aux alentours de **45 000 items ingérés** au total par run reset_db (37 sources actives).

Répartition matchés sport au dernier run propre (R13-N reset_db) — environ :

| Catégorie | Matchés | Remarques |
|---|---|---|
| questions | ~15 | AN + Sénat |
| communiques | ~12 | MinSports, MinARMEES, CPSF, ANS, + CE (1) |
| comptes_rendus | ~10 | Liste plate, snippet 500 + text-fragment |
| dossiers_legislatifs | ~10 | Cards avec logos AN/Sénat |
| agenda | ~100 sur 30j | Majoritairement AN |
| amendements | 4 | AN 3 (LFI PJL sécurité 52/56/57) + Sénat 1 |
| jorf | ~5 | DILA XML + Conseil constit décisions (R13-M) |

Chambres visibles : AN, Sénat (violet), MinSports (doré), MinARMEES, CPSF, CE, Élysée, JORF.

---

## 4. Décisions clés (architecture & UX)

**Site Hugo, pas de JS serveur** — le pipeline Python génère des `.md` Markdown/frontmatter + JSON sous `site/content/items/<cat>/`, `site/data/*.json`, et Hugo 0.128 compile à plat. Le JavaScript côté client est limité à la recherche (search_index.json) et au text-fragment natif des CR.

**Fixups à l'export plutôt que reset DB** — pattern établi depuis R12 : `site_export._fix_<cat>_row(r)` réécrit title/url/status en mémoire au moment de l'export, pas dans la DB. Idempotent, couvre les items pré-patch sans migration. `upsert_many` n'update pas les `hash_key` existants, donc le reset_db reste nécessaire si on veut re-normaliser les `matched_keywords` ou `raw.*` persisté en SQLite.

**Naïf UTC partout** (R11f) — `parse_iso`, `_parse_date_any`, `_parse_dt` strippent la tz à l'entrée. Convention cohérente avec `main.py::_utcnow_naive`. Évite les crashes `offset-naive vs offset-aware` sur les flux AN tz-aware.

**Pas de retry tenacity sur 4xx** (R11e) — `_common._is_retryable` whitelist les 5xx + erreurs réseau. Économie ~16 s par source morte, et surtout un log.error explicite au moment où l'erreur survient. Le récap WARNING de `run_all` liste les sources en erreur ET à 0 item ; c'est le canari à surveiller dans les logs CI.

**`type: <cat>` sélectif** (R13-K, R13-M) — dans les `_index.md` générés sous `items/<cat>/`, on ne met `type: "<cat>"` que pour les catégories qui ont un layout dédié (`agenda`, `dossiers_legislatifs`). Si on le met partout, Hugo filtre silencieusement les pages sans `date:` valide (cas des senat_promulguees avec published_at null) et n'affiche que 1-3 items. Les autres catégories retombent sur `_default/list.html` qui affiche tout.

**Dédup dosleg : URL exclusif, pas sémantique** (R13-L fix2) — la bag-of-words triée (R13-L fix) effondrait 7/8 dosleg sur la prod (clés trop génériques). Retour à un dédup par URL simple avec Sénat prioritaire en cas d'égalité de date. On accepte temporairement le doublon JO 2030 AN+Sénat (2 URLs distinctes). Dédup sémantique correct nécessite `texteLegislatifRef` commun — non exposé actuellement par les parsers.

**CNOSF via sitemap.xml** (R13-M) — le site est Nuxt SPA, scraping HTML classique retourne 0 item. Solution : lire `/sitemap.xml` Drupal (statique) via le nouveau dispatcher `html_generic._from_sitemap_generic`. Filtre URLs contenant "actualite"/"news"/"communique" + cutoff 120j + titre reconstruit depuis slug.

**Agenda AN : liens vers page globale** (R13-J patch 7) — les permaliens unitaires `/dyn/17/reunions/RUANR...` sont cassés. Le template `agenda/list.html` force tous les items chamber="AN" vers `https://www2.assemblee-nationale.fr/agendas/les-agendas`. Les autres chambres (Sénat, Matignon) gardent leur source_url individuel.

**`an_texte_to_dossier.json` persisté** (R13-F) — cache AN `texteLegislatifRef → titre dossier` (4438 entrées ~500 Ko), écrit par `_normalize_dosleg` et lu par `_normalize_amendement`. `normalize.run_all` parallélise via ThreadPoolExecutor, donc sans pré-chargement il y a une race au 1er run après reset_db. Le workflow commit ce fichier en fin de job, comme `data/amo_resolved.json` et `data/last_digest.html`.

---

## 5. TODO

### 5.1 À faire (priorité haute)

1. **Actes_timeline vide pour certains dosleg** — le dossier laïcité 2025-07-09 signalé par Cyril comme retiré le 9 juillet 2025 a `raw.actes_timeline = []` en DB, donc `_fix_dossier_row` ne peut pas détecter le retrait même avec le flag `raw.is_retire` en place côté parser. Cause probable : structure JSON différente pour les dossiers pré-XVIIe ou dossiers retirés tôt. À investiguer sur le JSON source.
2. **Patch 12 — CR → ancre texte précise** — le text-fragment `#:~:text=<kw>` via URL fonctionne sur Chrome/Edge/Safari mais pas Firefox. Explorer en R14 : (a) scraper les paragraphes `<paragraphe id="X">` du XML Syceron à l'ingestion pour stocker une ancre absolue ; (b) proxy Hugo qui injecte l'ancre côté notre domaine. Préférence (a) si XSD stable.
3. **Dédup sémantique dosleg via `texteLegislatifRef`** — exposer ce champ dans le parser AN (`_normalize_dosleg.raw["texte_ref"]`) et le Sénat équivalent (`akn_discussion.xml`). Permettra de dédupliquer le doublon JO 2030 sans fausses fusions.
4. **min_sports_agenda scraper dédié** — la page `/agenda-previsionnel-de-marina-ferrari-1787` est un bulletin hebdo (1 page, pas un listing). Source désactivée en R13-N. Scraper à écrire : 1 item par fetch = la semaine courante (titre H1 + date extraite du H2).

### 5.2 À faire (priorité moyenne)

- **Procédure législative** — maîtriser dépôt → commission → séance → adoption → CC → promulgation avant de patcher `_map_code_acte` et le `status_label`. Débloquerait le patch "JO 2030 affiché Conseil Constit côté AN alors que promulgué".
- **Cache AMO évolutif** — fusionner le dump historique `/17/` avec `AMO10_deputes_actifs_mandats_actifs_organes_divises_XVII.json.zip` (organes actifs, mis à jour en quasi temps-réel) pour résoudre les POxxx / PAxxx ultra-récents absents du dump historique. Le fixup actuel (fallback date de séance) couvre le pire cas mais moins informatif qu'une résolution native.
- **Agenda commissions Sénat** — `senat_agenda` pointe sur www.senat.fr/agenda/ via html_generic, à surveiller au prochain cycle. Si 0 items utilisables : connecteur spécifique ou Akoma Ntoso.
- **Liste `EXPECTED_ZERO_HIT_SOURCES`** — blanchir les sources qui ne publient pas tous les jours (weekend Sénat, ministères en pause estivale) pour que le récap WARNING de `run_all` reste signalant.
- **Logos AN/Sénat officiels** — Cyril a fourni les PNG officiels, mais le sandbox ne peut pas lire les fichiers attachés aux messages. Les SVG actuels sont stylisés maison (façade tricolore AN, arcs dôme Sénat). Si besoin de remplacement fidèle, déposer directement dans `site/static/logos/an.svg` et `/senat.svg`.

### 5.3 À faire (priorité basse)

- **PISTE Légifrance OAuth2** — connecteur prêt (`src/sources/piste.py`) désactivé. Secrets `PISTE_CLIENT_ID` / `PISTE_CLIENT_SECRET` à créer côté GitHub pour doubler JORF. Pas prioritaire tant que DILA OPENDATA tient.
- **Audit 51 sources** via `scripts/audit_sources.py` périodiquement.
- **Coverage tests** `store.py`, `digest.py` partiels.

---

## 6. Pièges connus

### 6.1 Environnement local

- **`python` vs `python3`** : macOS n'a que `python3`. Toujours activer le venv (`source .venv/bin/activate`) pour que `python` fonctionne.
- **Pas de commentaires `#` dans les blocs shell partagés** — zsh sans `INTERACTIVE_COMMENTS` casse le copier-coller. Règle mémoire.
- **FUSE mount sandbox** : le sandbox ne peut pas supprimer `.git/index.lock` / `.git/HEAD.lock` même en root. Utiliser `mcp__cowork__allow_cowork_file_delete` puis `rm -f` côté bash, ou demander à Cyril côté macOS.
- **Sandbox egress limité** : Wikimedia Commons, certains sites AN, CDN Cloudflare peuvent renvoyer 404/429. Pour tester une URL live, passer par `curl -sI` (le sandbox a accès aux domaines publics majoritaires mais pas tous). Les fichiers images attachés aux messages Cyril ne sont PAS accessibles via FS côté sandbox — demander un dépôt direct dans le repo si besoin.

### 6.2 Données AN (Assemblée nationale opendata)

- **XSD AN 0.9.8 casse sensible** : `timeStampDebut` avec S majuscule. Certains vieux dumps utilisent `timestampDebut` (lowercase). Le parseur d'agenda gère les deux + fallback `cycleDeVie.chrono.creation` (R13-L) pour les items sans timeStampDebut.
- **Codes AN vs libellés humains** — items référencent `PAxxx` (acteurs) / `POxxx` (organes). Sans cache AMO chargé, les titres affichent les codes bruts. Les tests tolèrent les deux formes. Le dump `/17/amo/tous_acteurs_...` est régénéré quotidiennement mais a 24-48h de retard sur les nouveaux POxxx (commissions créées).
- **Paths JSON amendements** (R11a) : `identification.numeroLong`, `corps.contenuAuteur.dispositif` + `exposeSommaire`, `signataires.auteur.groupePolitiqueRef`, `cycleDeVie.etatDesTraitements.etat.libelle`, `texteLegislatifRef`. R13-J a séparé `cycleDeVie.sort.libelle` (final) de `cycleDeVie.etatDesTraitements.etat.libelle` (transitoire), stockés distinctement dans `raw.sort` / `raw.etat`.
- **Questions** : `indexationAN` (pas `indexationAnalytique`), `acteurRef` sans nom/prénom direct, `textesQuestion` liste. **R13-G patch 8** : priorité sujet `analyse > tete_analyse > rubrique` (la rubrique donne juste "sports", l'analyse donne "Financement des équipements sportifs scolaires").
- **Dossier agenda JSON AN** : la racine est `{"reunion": {...}}`, pas directement les champs. Le parser unwrap via `_iter_records(obj, "reunion")`.
- **URL agenda AN unitaires cassées** — `/dyn/17/reunions/RUANR...` renvoie souvent 404. Le template agenda force tous les items AN vers `https://www2.assemblee-nationale.fr/agendas/les-agendas` (R13-J patch 7).
- **actes_timeline vide** (observé R13-N) — certains dossiers AN pré-2026 ou retirés tôt ingèrent `actes_timeline = []`. Empêche la détection automatique du retrait. Cause à investiguer.

### 6.3 Données Sénat

- **`senat_ameli.zip` = dump PostgreSQL** (pas CSV zip). Désactivé définitivement, remplacé par `senat_amendements` per-texte.
- **Format session** : Sénat Akoma Ntoso utilise `"25"` (2 chiffres), les CSV per-texte `"2025-2026"`. Conversion dans `senat_amendements._session_to_csv`.
- **CSV per-texte** : TAB-delimited, ligne 1 hint `sep=\t`, encoding cp1252 (fallback utf-8-sig + utf-8+replace). Séance : `https://www.senat.fr/amendements/<session>/<num>/jeu_complet_<session>_<num>.csv`, Commission : `/commissions/<session>/<num>/jeu_complet_commission_<session>_<num>.csv`.
- **404 = normal** : beaucoup de textes sans amendements encore. `_try_fetch` silencieux.
- **Budget fetch** : `_MAX_TEXTS_PER_RUN = 300`, `_MAX_AMDT_PER_TEXTE = 2000`.

### 6.4 Matching

- **Amendement ≠ texte du dossier** — l'amendement ne cite rarement les mots-clés sport. Enrichir le haystack avec le titre du dossier parent (R11b). Match tombe sur le thème porté par le dossier.
- **Priorité summary** : titre du dossier EN PREMIER, puis objet, puis dispositif. Sinon le dispositif (long, générique) domine l'extrait.
- **Kws ANS et ARCOM nus retirés** — `ANS` → unidecode "ans" → faux positifs massifs. `ARCOM` seul → faux positifs audiovisuel. Seulement formes étendues ("Agence nationale du sport", "ARCOM paris sportifs").
- **Cas Follaw Ollivier N°6 invalidé** (R11d) — ne pas chercher à reproduire le match "clubs sportifs" sur ce dossier, il n'existe pas dans le JSON AN.

### 6.5 Publications + catégories strict-dated

- **`STRICT_DATED_CATEGORIES = {"communiques", "dossiers_legislatifs"}`** — pas de fallback `inserted_at`. Élimine les rapports Sénat CSV sans date, pages pivot html_generic, agendas hebdo datés en fin de semaine à venir. Pour les dosleg : `senat_promulguees` (11 items matchés sport) sont filtrés car `published_at = None`.
- **ANS timeouts intermittents** — `_common.fetch` absorbe les `ConnectTimeout` via soft-fail.
- **`MinARMEES` et autres ministères via fallback `www.`** (R13-G patch 17) — `html_generic._chamber` strip "www." et mappe les domaines connus (defense → MinARMEES, justice → MinJUSTICE, etc.). Sinon `Www` apparaissait comme chambre.

### 6.6 Politique fetch HTTP (R11e)

- **`fetch_bytes` pas de retry 4xx** — `_is_retryable` whitelist 5xx + erreurs réseau. `_raise_for_status_loud` émet ERROR explicite avec code + URL.
- **Récap `run_all`** — bloc WARNING en fin de pipeline liste sources en erreur + sources 0-item. Canari qui a révélé le 404 `amendements_legis` (R11d) et le tz-bug `an_agenda` (R11f). Bruit possible sur sources qui publient rarement → blanchir via `EXPECTED_ZERO_HIT_SOURCES` si nécessaire.

### 6.7 Hugo / Site export

- **Store ne persiste pas `snippet`** (R12 UX-E) — rebuild à la volée via `KeywordMatcher.build_snippet` dans `_load`. Pas de migration nécessaire.
- **`upsert_many` n'update pas** un hash_key existant (store / DB). Après un patch parser qui change la forme du title/summary/raw, **il faut reset_db** ou `scripts/reset_category.py <cat>` pour forcer la ré-ingestion. Sinon les items legacy gardent leur forme pré-patch.
- **Fixups in-memory = insurance policy** — chaque `_fix_<cat>_row` dans `site_export.py` gère le delta DB legacy / parser courant à l'export. Après reset_db, les fixups ne touchent plus rien (items frais direct). Conserver les fixups permet de ne pas perdre d'items quand on déploie un nouveau parser sans reset.
- **Slugs et doublons .md** (R12a) — les slugs dépendent du titre. Quand un fixup réécrit le title, le slug change. Solution : `shutil.rmtree(items_dir)` au début de chaque export.
- **`type: <cat>` dans frontmatter** — à n'utiliser QUE pour les catégories ayant un layout Hugo dédié (`agenda`, `dossiers_legislatifs`). Sinon Hugo filtre silencieusement les pages sans `date:` valide.
- **Hugo `{{ with }}` change le contexte `.`** — pour accéder à `$.Params.<autre>` dans un bloc `with`, utiliser `$.` explicite. Alternative plus robuste : `{{ if }}` qui n'altère pas `.`. Cause du bug sort_slug vide pré-R13-O.
- **Text-fragment** `#:~:text=<kw>` fonctionne sur Chrome/Edge/Safari 16.4+, dégrade silencieusement sur Firefox (URL ignorée → renvoie vers page non-ancrée).

### 6.8 Méthodologie

- **Doc officielle avant diag** — XSD AN, schemas Sénat, parser communautaire reconnu (anpy) d'abord, puis tâtonnement. Règle mémoire.
- **Toujours `curl -I` une URL AN opendata** avant de la mettre dans `sources.yml`. Les paths changent sans préavis (cas `amendements_legis` → `amendements_div_legis` en R11d). Noter la date de vérif en commentaire.

---

## 7. Autonomie sandbox (depuis R13-F)

Le sandbox peut déclencher et suivre les workflows sans intervention manuelle :

- `~/bin/gh` (v2.63.2 arm64) installé en user-space (pas de sudo).
- Token GitHub extrait via Finder + `.command` jetable, stocké dans `data/cache/.ghtoken` (gitignoré via `data/cache/`). Scopes : `repo`, `workflow`.
- **Usage** : `export GH_TOKEN=$(cat data/cache/.ghtoken) && ~/bin/gh workflow run daily.yml --ref main [-f reset_db=1 -f no_email=1]`.
- **Push depuis le sandbox** : `git push "https://x-access-token:${TOKEN}@github.com/cyrilmourin/veille-parlementaire-sport.git" HEAD:main`.
- **`.git/index.lock` figé** : `mcp__cowork__allow_cowork_file_delete <path>` puis `rm -f`.
- **Deps pytest** dans le sandbox : `pip install --user pyyaml unidecode tenacity httpx jinja2 pytest python-dateutil feedparser lxml beautifulsoup4 pydantic rich` (Python 3.10 sandbox, pyproject exige 3.11 donc `pip install -e .` échoue).

---

## 8. Index des fichiers critiques

| Fichier | Rôle |
|---|---|
| `src/main.py` | CLI + orchestration `run` / `dry` |
| `src/normalize.py` | Dispatcher `group` + `format` → connecteur |
| `src/keywords.py` | Matcher (regex + unidecode) |
| `src/store.py` | SQLite upsert + hash_key dedup |
| `src/digest.py` | Email HTML (Jinja2) |
| `src/site_export.py` | JSON + Markdown pour Hugo |
| `src/models.py` | Pivot `Item` (pydantic v2) |
| `src/amo_loader.py` | Cache AMO (acteurs + organes + textes→dossiers) |
| `src/sources/assemblee.py` | Connecteur AN (tous formats json_zip) |
| `src/sources/senat.py` | Routeur Sénat (délègue à senat_akn ou senat_amendements) |
| `src/sources/senat_akn.py` | Dossiers législatifs Sénat Akoma Ntoso |
| `src/sources/senat_amendements.py` | Amendements Sénat per-texte (R11c) |
| `config/sources.yml` | 51 sources déclarées |
| `config/keywords.yml` | Dictionnaire mots-clés 5 familles |
| `.github/workflows/daily.yml` | Cron 06:00 UTC + Pages deploy |
| `scripts/reset_category.py` | Purge ciblée avant re-ingest post-patch |
| `scripts/refresh_amo_cache.py` | Refresh hebdo cache AMO |
| `scripts/audit_sources.py` | Ping HEAD des 51 sources |

---

## 9. Historique des refactorings majeurs

| Tag | Description | Commit |
|---|---|---|
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
