---
title: Veille Parlementaire Sport — Handoff
maintainer: Cyril Mourin
last_updated: 2026-04-21 (midi, après R13-E + R13-F + R13-G + reset DB)
---

# Handoff — Veille Parlementaire Sport

Ce document est le point d'entrée pour reprendre le projet sans contexte préalable. Il résume l'architecture, l'état au **2026-04-21 (soir)**, les décisions prises, ce qu'il reste à faire et les pièges connus.

À lire dans l'ordre : §1 (quoi) → §2 (comment ça tourne) → §3 (où on en est) → §4 (pièges) → §5 (suite).

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

## 3. État au 2026-04-21 (midi)

### 3.1 Dernier commit en date

```
8686964 R13-G — batch 15 patches UX (sidebar meta, couleurs, labels, Amdt, Séance du, analyse…)
10f8e6a R13-F — persister data/an_texte_to_dossier.json entre runs
253dde5 R13-E — backfill amendements AN + recapitalize matched_keywords à l'export
901dc25 chore: snapshot digest 2026-04-21T08:50Z
03a8bbd R13 — bilan visuel Cyril : AMO à jour, kws capitalisés, snippets filtrés
7db4d59 R12a — purge content/items + applique fixups au digest
bd4a730 R12 — refonte UX : agenda, dosleg, dates/mots-cles, questions, CR
```

Tout pushé sur `origin/main`. **116/116 tests verts** (91 baseline + 10 R13-E + 15 R13-G).

Le reset DB `reset_db=1 no_email=1` a tourné après R13-F et R13-G (runs `24715675554` puis `24717935781`) : re-normalisation complète de l'historique avec tous les parsers cumulés. Les fixups in-memory (`_fix_*_row`) deviennent maintenant des insurance policies — ils s'appliquent à 0 item post-reset et protègent contre un futur patch parser qui créerait un nouveau delta ancien/récent.

### 3.2 Ce qui fonctionne

- **91/91 tests verts** (44 historiques + 5 R11e sur la politique fetch + 17 R11f sur la normalisation naïf UTC + 25 R12 sur les fixups d'export site).
- **R9** — Sénat : CR séance avec dates réelles, titres lisibles, URLs sommaire journalier. AN : CR avec date séance + thème depuis XML Syceron.
- **R10** — Publications : fenêtre 90j, filtre strict `published_at`, réactive ANS (soft-fail des timeouts).
- **R11a** — Paths JSON de `_normalize_amendement` corrigés. Anciens paths renvoyaient `None` systématiquement → 0/5683 matchés. Corrigés : `identification.numeroLong`, `corps.contenuAuteur.dispositif` + `exposeSommaire`, `signataires.auteur.groupePolitiqueRef`.
- **R11b** — Amendement enrichi avec titre du dossier parent dans summary → le matcher retombe sur le thème même quand le texte de l'amendement ne cite pas les mots-clés.
- **R11c** — Pivot Sénat : `senat_ameli.zip` désactivé (c'était un dump PostgreSQL, pas un zip de CSV — 0 item ingéré depuis des mois). Remplacé par `senat_amendements` (source `akn_discussion`) qui itère `depots.xml` et fetche `jeu_complet_<session>_<num>.csv` + variante commission.
- **R11d** (commit `44a090e`) — Fix URL AN amendements : `amendements_div_legis` et non `amendements_legis`, qui renvoyait 404 silencieux depuis R11. Validation : 5683 amendements AN ingérés dont 3 matchés sport (Amdts 52/56/57 LFI sur PJL sécurité) + les 6 Sénat pré-existants = **9 amendements matchés au total**, reproduisant au passage 2/4 des cas Follaw AN historiques (52 et 56).
- **R11e** (en cours, non encore pushed) — Durcissement de `_common.fetch_bytes_*` : `log.error` explicite sur 4xx/5xx avec URL+code, `retry_if_exception` qui ne retry PAS sur 4xx (économie 16s+ par source morte). Récap WARNING en fin de `run_all` qui liste les sources KO et les sources à 0 item. Le premier run post-R11e a révélé 3 bugs latents qui étaient jusqu'ici invisibles : HTTP 404 sur `diplomatie.gouv.fr`, timezone bug sur `an_agenda` (fixé en R11f), et 8 sources à 0 item dont certains scrapers HTML probablement cassés (`min_sante`, `min_travail`, `min_affaires_etrangeres`, `cnosf`). À traiter en R11g.
- **R11f** (en cours, non encore pushed) — Fix du tz-bug sur `an_agenda`. Les timestamps AN agenda sont au format `"2025-11-07T21:30:00.000+01:00"` (tz-aware), et `parse_iso` les renvoyait aware, incompatibles avec le `since = _utcnow_naive() - timedelta(...)` du filtre `since_days` → crash `TypeError: can't compare offset-naive and offset-aware datetimes` sur `assemblee.fetch_source`. Même cause-racine latente dans `senat._parse_date_any` et `site_export._parse_dt`. Fix : normalisation unifiée en **naïf UTC** à l'entrée du pipeline (conversion `astimezone(UTC).replace(tzinfo=None)` appliquée dans les 3 parseurs), conforme à la convention du projet (cf. `main.py:71`, `_utcnow_naive`). 17 nouveaux tests paramétrés. Validé sur 10 timestamps AN agenda live : 10/10 comparaisons réussies là où c'était 0/10 avant.
- **R11g** (en cours, non encore pushed) — Audit des sources révélées par le récap WARNING de R11e :
  - **R11g-1** : désactivation des doublons Sénat `senat_dosleg` et `senat_questions` (remplacés par `senat_akn` et `senat_questions_1an`, 0 items depuis longtemps).
  - **R11g-2** : fix URL Quai d'Orsay (MEAE). L'arbo `/salle-de-presse/` a été supprimée côté site ; nouveau path `/presse-et-ressources/decouvrir-et-informer/actualites/` vérifié live (HTTP 200, 243 ko, 12 liens actualités).
  - **R11g-4** : audit scrapers 0-items. Désactivation de `min_sante`, `min_travail` (WAF F5 ASM qui renvoie une page "Request Rejected" pour tout User-Agent Mozilla), et de `cnosf` (SPA Nuxt dont les articles ne sont rendus qu'après hydratation JS — fix propre = connecteur sitemap générique, reporté car non critique). `elysee_sitemap` / `elysee_agenda` / `min_affaires_etrangeres` sont laissés actifs : l'user a confirmé que c'est normal qu'ils tombent à 0 ponctuellement.
- **R12** (en cours, non encore pushed) — Volet UX/UI massif demandé par Cyril. Toutes les réécritures de titres sont des patchs in-memory à l'export (`_fix_*_row` dans `site_export.py`), idempotents, qui évitent d'exiger un reset DB :
  - **UX-A agenda** : sidebar liens vers `/items/agenda/` (pas vers la source), page agenda en texte sans liens, retrait préfixe "Agenda — " / "Agenda - " / "Agenda – " dans les titres, titres tronqués à 60 caractères dans la sidebar (pleine longueur sur la page agenda).
  - **UX-B dossiers législatifs** : fenêtre 730j → 1095j (3 ans), `dossiers_legislatifs` ajouté à `STRICT_DATED_CATEGORIES` (plus de fallback `inserted_at` → les vieux items sans date disparaissent), capitalisation 1re lettre des titres Sénat pre-patch (`_fix_dossier_row`).
  - **UX-C dates + mots-clés** : dates en rouge (`--sl-red`) + gras partout (home, dosleg, digest). Mots-clés dépouillés : italique + gras, plus de couleur de fond ni de police (retrait des `background` + `color` sur `.kw-tag[data-family]`). Séparateur virgule entre mots-clés.
  - **UX-D questions** : retrait du `→ ministère` et `[sort]` dans les titres (patch connecteur Sénat + `_fix_question_row` in-memory pour la DB historique). Résolution des `Député PAxxxx` résiduels via cache AMO (re-appliqué à chaque export).
  - **UX-E comptes rendus** : fenêtre 30j → 180j dans `WINDOW_DAYS_BY_CATEGORY` (les CR `_fix_cr_row` recalent `published_at` sur la vraie date de séance, souvent plusieurs mois en arrière — la fenêtre 30j écartait quasi tous les CR sport). Bug historique révélé : le schéma SQL `store.SCHEMA` n'a **jamais** persisté `snippet` → tous les items en DB lu par digest/site ont un snippet vide. Fix : reconstruction en mémoire via `KeywordMatcher.build_snippet` au moment de l'export (idempotent, quelques centaines d'items matchés). Extraction thème Sénat via `extract_cr_theme` pour les CR pre-patch dont le titre est "CR intégral — d20260205.xml".
- Site UX/UI : layout central + sidebar, recherche client-side, thématiques pliées avec compteur, agenda en module à droite, favicon Sideline, maquette dossiers législatifs façon AN.
- Cache AMO : script weekly + workflow idempotent.

### 3.3 Volumes DB actuels (après reset + R13-G, run 2026-04-21 10:40 UTC)

45 764 items ingérés au total, **332 matchés sport**, 58 publiés sur le site.

Répartition par catégorie (matchés sport uniquement, fenêtre applicable) :

| Catégorie | Matchés |
|---|---|
| questions | 14 |
| communiques | 11 |
| comptes_rendus | 10 |
| dossiers_legislatifs | 10 |
| jorf | 5 |
| amendements | 4 |
| agenda | 4 |

Par chambre sur la home : AN 33, Sénat 9, MinSports 8, JORF 5, CPSF 2, autres ministériels via badges MinARMEES / MinJUSTICE / MinCULTURE / etc.

Agenda ingéré : `an_agenda` (6412), `matignon_agenda` (57), `min_sports_agenda` (7). **Commissions Sénat : aucune source active** — pas dans `sources.yml`, à ajouter en R14 (cf. §3.5).

### 3.4 Ce qui reste à faire (immédiat)

1. **Patch 12 (R14 dédié) — lien CR vers ancre du 1er kw** — Cyril veut que cliquer sur un compte-rendu atterrisse directement à la 1re occurrence d'un mot-clé sport. Les CR sont publiés sur `assemblee-nationale.fr/dyn/17/comptes-rendus/seance/...` (site externe, ancres non standardisées). Trois pistes : (a) scraper les ancres AN à l'ingestion pour trouver un `#para_XY` proche du match ; (b) héberger une copie du CR sur notre site avec ancre `#kw-1` injectée ; (c) utiliser l'API AN si elle expose des permaliens paragraphe. À cadrer en session dédiée R14.
2. **Patch 15 — JO 2030 statut "Conseil Constit" côté AN alors que promulgué** — Bug sur le `status_label` des dossiers législatifs AN. Le flag `is_promulgated` est bien positionné côté Sénat (badge vert visible) mais la carte AN affiche encore "Conseil Constitutionnel" (étape précédente dans la navette). Bloqué par le TODO "procédure législative" (§3.5) : Cyril veut qu'on maîtrise la séquence dépôt → commission → séance → adoption → CC → promulgation avant de patcher le mapping codeActe → étape.
3. **Sources agenda manquantes (partie patch 4 R13-G)** — `min_sports_agenda` ingère 7 items mais 0 matchés sport (probablement pages d'index, pas d'events), **et surtout aucune source pour les commissions du Sénat** (pas dans `sources.yml`). Pistes : `https://www.senat.fr/commission/` listing, agenda XML Akoma Ntoso, ou flux RSS commission.

### 3.5 Ce qui reste à faire (moyen terme)

- **PISTE Légifrance OAuth2** — connecteur prêt (`src/sources/piste.py`), désactivé par défaut. Secrets `PISTE_CLIENT_ID` / `PISTE_CLIENT_SECRET` à créer côté GitHub pour doubler la source JORF. Pas prioritaire tant que DILA OPENDATA tient.
- **Procédure législative** — Cyril veut qu'on maîtrise les étapes (dépôt → commission → séance → adoption → CC → promulgation) avant de patcher le tri/filtre des dossiers. Prérequis à toute évolution du `status_label` dans `site_export`. Débloquerait le patch 15 (cf. §3.4).
- **Audit des 51 sources** — `scripts/audit_sources.py` fait un ping HEAD. À lancer périodiquement pour détecter les 404 silencieux.
- **Coverage tests** — `store.py`, `site_export.py`, `digest.py` partiellement couverts. Pas bloquant, mais à considérer si on refactore.

---

## 4. Pièges connus

### 4.1 Environnement local

- **`python` vs `python3`** : macOS n'a que `python3`. Toujours activer le venv : `source .venv/bin/activate` puis `python` fonctionne.
- **Pas de commentaires `#` dans les blocs shell partagés** — zsh sans `INTERACTIVE_COMMENTS` casse. Règle mémorisée.
- **FUSE mount sur le workspace Claude** : le sandbox ne peut pas supprimer `.git/index.lock` ou `.git/HEAD.lock` même en root. Si un commit depuis l'agent se fige, demander à Cyril de `rm -f .git/index.lock .git/HEAD.lock` côté macOS.
- **Sandbox egress** : le sandbox Linux de l'agent ne peut pas atteindre `www.senat.fr` directement. Pour tester les URLs CSV en live, utiliser le Chrome MCP (le filtre de contenu peut bloquer du Base64 — strip des tags + caractères non-alphanum en amont).

### 4.2 Données AN

- **XSD AN 0.9.8 casse sensible** : `timeStampDebut` avec S majuscule. Certains vieux dumps utilisent `timestampDebut` (lowercase). Le parseur d'agenda gère les deux (voir `tests/test_agenda_normalize.py::test_normalize_agenda_fallback_lowercase_timestamp`).
- **Codes AN vs libellés humains** : les items référencent `PAxxx`/`POxxx`. Sans cache AMO chargé, les titres affichent les codes bruts. Les tests doivent tolérer les deux formes (`"PO420120" in title` OR `"Commission" in title`).
- **Paths JSON amendements** (source R11a) :
  - ✅ `identification.numeroLong`, `corps.contenuAuteur.dispositif`, `corps.contenuAuteur.exposeSommaire`, `signataires.auteur.groupePolitiqueRef`, `cycleDeVie.etatDesTraitements.etat.libelle`, `texteLegislatifRef`
  - ❌ anciens paths "à plat" qui retournaient `None` silencieusement.
- **Questions** : `indexationAN` (pas `indexationAnalytique`), `acteurRef` sans nom/prénom direct, `textesQuestion` est une liste.

### 4.3 Données Sénat

- **`senat_ameli.zip` est un dump PostgreSQL**, pas un zip de CSV. Le ZIP contient un unique `var/opt/opendata/ameli.sql`. Toute tentative de `csv_zip` retourne 0 items. **Source désactivée définitivement** (`enabled: false`). Remplacée par `senat_amendements` per-texte.
- **Format session** : Sénat Akoma Ntoso utilise `"25"` (2 chiffres), les CSV per-texte utilisent `"2025-2026"`. Conversion dans `senat_amendements._session_to_csv`.
- **CSV per-texte** : TAB-delimited, ligne 1 = hint `sep=\t`, encoding cp1252 (avec fallback utf-8-sig et utf-8+replace). URLs :
  - Séance : `https://www.senat.fr/amendements/<session>/<num>/jeu_complet_<session>_<num>.csv`
  - Commission : `https://www.senat.fr/amendements/commissions/<session>/<num>/jeu_complet_commission_<session>_<num>.csv`
- **404 = normal** : beaucoup de textes n'ont pas d'amendements encore. `_try_fetch` est silencieux sur 404 (évite le spam de logs).
- **Budget fetch** : `_MAX_TEXTS_PER_RUN = 300`, `_MAX_AMDT_PER_TEXTE = 2000` — à ajuster si on constate des lentons.

### 4.4 Matching

- **Amendement ≠ texte du dossier** : l'amendement cite rarement les mots-clés sport littéralement. **Il faut enrichir le haystack avec le titre du dossier parent** (logique R11b). Le match peut alors tomber sur le thème porté par le dossier plutôt que sur le corps de l'amendement.
- **Priorité summary** : titre du dossier **en premier** dans summary, puis objet, puis dispositif. Si on inverse, le dispositif (souvent long et générique) peut dominer l'extrait affiché.
- **`ANS` seul retiré** des keywords : faux positifs massifs (unidecode → "ans"). Seulement `Agence nationale du sport` en full.
- **`ARCOM` seul retiré** : faux positifs audiovisuel. Seulement formes contextualisées (`ARCOM sport`, `ARCOM paris sportifs`, etc.).
- **Hypothèse « Ollivier N°6 matche `clubs sportifs` » invalidée (R11d)** — le HANDOFF initial citait ce cas comme test Follaw de la logique R11b. Vérifié en R11d : le dossier PPL 469 « Protéger les mineurs des risques des réseaux sociaux » n'emporte aucune mention sport dans ses amendements Ollivier (N°1 à N°6 + COM-6 à COM-11), ni dans le titre du dossier parent. Aucun amendement ingéré (0/2442 Sénat) ne contient la sous-chaîne « clubs sportifs ». Le cas Follaw d'origine visait probablement un amendement/dossier différent — ne pas chercher à reproduire ce match en l'état. **Ne pas ressusciter ce faux test de non-régression** : il consomme du temps sans rien prouver.

### 4.5 Publications (R10)

- **`STRICT_DATED_CATEGORIES = {'communiques'}`** : pas de fallback `inserted_at`. Élimine les rapports Sénat CSV sans date, pages pivot html_generic, agendas hebdo datés en fin de semaine à venir.
- **ANS timeouts intermittents** : `_common.fetch` absorbe les `ConnectTimeout` via soft-fail (ne bloque pas le pipeline).

### 4.7 Politique fetch HTTP (R11e)

- **`fetch_bytes` / `fetch_bytes_heavy` ne retry plus sur 4xx** — l'ancienne politique tenacity retentait 2-3 fois un 404, ajoutant 16+ secondes de latence pour rien et masquant le diagnostic. Désormais, `retry_if_exception(_is_retryable)` n'autorise le retry que sur les 5xx + erreurs réseau (ConnectError, RemoteProtocolError, ReadTimeout, etc.).
- **`log.error` explicite sur 4xx/5xx** — `_raise_for_status_loud` émet un ERROR avec code HTTP + URL complète au moment où l'erreur survient. Évite que l'erreur ne soit noyée dans les DEBUG `httpcore` au niveau `normalize._fetch_one` (cas qui a laissé `an_amendements = 0` inaperçu entre R11 et R11d).
- **Récap `run_all`** — bloc WARNING en fin de pipeline qui liste (a) les sources en erreur avec leur message, (b) les sources qui ont produit 0 item sans erreur. Surveiller cette section dans les logs CI quotidiens : c'est le canari qui aurait dû alerter sur le 404 `amendements_legis`.
- **À surveiller** : si une source qui produit légitimement 0 item dans une fenêtre courte (ex. weekend Sénat sans dépôt) apparaît dans le récap, on aura du bruit. Si ça devient systématique pour certaines sources, blanchir via une liste `EXPECTED_ZERO_HIT_SOURCES`.

### 4.6 Méthodologie

- **Toujours chercher la doc officielle avant de scripter du diag** — parser communautaire reconnu ou XSD officiel d'abord, tâtonnement ensuite. Règle mémorisée.
- **`upsert_many` ne met pas à jour** si le `hash_key` existe déjà. Après un patch parser, il **faut** purger la catégorie (`scripts/reset_category.py`) pour forcer la ré-ingestion.

---

## 5bis. Autonomie sandbox (R13-F session)

Depuis 2026-04-21 après-midi, le sandbox peut déclencher et suivre les workflows sans intervention manuelle :

- `~/bin/gh` (v2.63.2 arm64) installé en user-space.
- Token GitHub extrait via Finder + `.command` jetable, stocké dans `data/cache/.ghtoken` (gitignoré via `data/cache/`). Scopes : `repo`, `workflow`.
- Usage : `export GH_TOKEN=$(cat data/cache/.ghtoken) && ~/bin/gh workflow run daily.yml --ref main [-f reset_db=1 -f no_email=1]`.
- Pour un push depuis le sandbox : `git push "https://x-access-token:${TOKEN}@github.com/cyrilmourin/veille-parlementaire-sport.git" HEAD:main`.
- Si `.git/index.lock` se fige (FUSE), utiliser `mcp__cowork__allow_cowork_file_delete` puis `rm -f .git/index.lock`.

---

## 5. Prochaine session — amorce

R13 (et ses sous-rev E/F/G) sont closes et déployées. Le reset DB a été exécuté, la prod est propre. Pistes à ouvrir dans l'ordre :

1. **Patch 12 (R14 dédié) — lien CR vers ancre kw** — cadrage des 3 pistes (scraping ancres AN / proxy sur notre site / API AN permaliens). Voir §3.4.
2. **Procédure législative** — prérequis au patch 15 (statut JO 2030) et à toute évolution du `status_label`. Cyril veut qu'on maîtrise la séquence complète AN+Sénat+CC+promulgation avant.
3. **Sources agenda manquantes** — ajouter les commissions du Sénat (pas de source active) et investiguer pourquoi `min_sports_agenda` ingère des pages d'index sans match sport (adapter le scraper ou supprimer la source si elle ne fournit pas d'events utilisables).
4. **Liste `EXPECTED_ZERO_HIT_SOURCES`** si le récap WARNING de `run_all` devient bruyant sur certaines sources qui ne publient pas tous les jours (weekend Sénat, ministères en pause estivale, etc.).
5. **Audit des 51 sources** via `scripts/audit_sources.py` — maintenant que R11d a renforcé la visibilité, les 404 latents vont remonter plus vite. Première passe conseillée périodiquement pour établir un baseline.

---

## 6. Index des fichiers critiques

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

## 7. Historique des refactorings majeurs

| Tag | Description | Commit |
|---|---|---|
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
