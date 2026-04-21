---
title: Veille Parlementaire Sport — Handoff
maintainer: Cyril Mourin
last_updated: 2026-04-21 (nuit, après R11g + R12 UX)
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

## 3. État au 2026-04-21 (soir)

### 3.1 Dernier commit en date

```
afa6cd7 R11f — fix tz-bug an_agenda : normalisation naive UTC   (commit local)
f30ce2b R11e — durcissement _common.fetch + recap run_all       (pushed)
44a090e R11d — fix URL AN amendements (amendements_div_legis)
9840f3a docs: handoff complet du projet (R11)
9c4af6b R11 — Amendements : fix AN parser + pivot Sénat per-texte
```

Sur `main`. **Deux commits restants à préparer + push** :
- R11g : audit sources (désactivation doublons + WAF + Nuxt + fix URL MEAE)
- R12 : volet UX (5 chantiers Cyril) + 25 tests fixups

Tous testés (91/91 verts au dernier run sandbox). À pusher demain matin via le script §5bis.

Working tree (snapshot soir 2026-04-21, 23h) :
```
M  config/sources.yml          (R11g — désactivations + URL MEAE)
M  src/sources/senat.py        (R12 UX-D — retrait ministère du titre questions)
M  src/site_export.py          (R12 UX-A/B/D/E — _fix_*_row + snippet rebuild)
M  src/digest.py               (R12 UX-E + UX-C — snippet rebuild + restyle dates/kws)
M  site/static/style.css       (R12 UX-C — dates rouge/gras, kw italique sans bg)
M  site/layouts/agenda/list.html         (R12 UX-A — pas de liens dans page agenda)
M  site/layouts/partials/sidebar.html    (R12 UX-A — sidebar liens vers /agenda)
M  HANDOFF.md                  (mise à jour finale)
?? tests/test_site_export_fixups.py      (R12 — 25 tests, nouveau)
?? data/an_texte_to_dossier.json         (cache, à arbitrer .gitignore)
?? site/data/                            (généré par Hugo au build)
?? site/static/search_index.json         (généré par site_export)
```

Les fichiers `test_common_fetch.py` (R11e) et `test_parse_iso_naive.py` (R11f) sont déjà tracked dans `f30ce2b` / `afa6cd7`. Seul `test_site_export_fixups.py` (R12) est à ajouter au prochain commit.
```

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

### 3.3 Volumes DB actuels (après R11d, run 2026-04-20 22:00)

```
questions              8 337
amendements            8 163   ← 5 721 AN + 2 442 Sénat (était 0 avant R11d)
dossiers_legislatifs   6 765
agenda                 6 475
comptes_rendus         3 292
jorf                   1 575
communiques            1 322
nominations              215
```

Amendements matchés sport : **9** (3 AN + 6 Sénat).

Top sources :

```
an_agenda                 6 411
an_amendements            5 721   ← R11d
senat_questions_1an       5 263
senat_promulguees         4 291
senat_cri                 2 801
senat_amendements         2 442   ← R11c
senat_qg                  2 330
an_dossiers_legislatifs   1 336
```

### 3.4 Ce qui reste à faire (immédiat)

1. **~~Re-ingest amendements post-R11~~** — ✅ fait en R11d. Résultats observés :
   - AN : 5721 items ingérés, 3 matchés sport (Amdts 52/56/57 LFI sur « Renforcer la sécurité, rétention administrative et prévention des risques »). 2/4 des cas Follaw historiques reproduits (52 et 56 ✅, 53 et 54 ❌ absents — probablement filtrés par la fenêtre `since_days: 30` car plus anciens, à confirmer si besoin en élargissant).
   - Sénat : 2442 items ingérés, 6 matchés sport (4 Gouvernement sur JO 2030, 1 VÉRIEN sur `dopage`, 1 CANALÈS sur polices municipales).
   - Ollivier N°6 « Protéger mineurs » : l'amendement est bien ingéré mais **ne matche pas** — voir §4.4 piège n°5 (hypothèse R11b invalidée).
2. **Décider du sort de `data/an_texte_to_dossier.json`** — actuellement untracked. Soit l'ajouter à `.gitignore` (cache local), soit le commit pour speed-up premier run CI. Logique actuelle : régénéré par `assemblee._harvest_texte_refs` si absent.
3. **Reset DB prévu** (memo en memory) — à faire lors de la prochaine modif de `daily.yml` pour re-normaliser l'historique complet avec les parsers R9+R10+R11+R11d.
4. **Audit des 4 cas Follaw manquants** — AN 53/54 absents, et Ollivier N°6 ne matche pas. À recroiser avec l'état actuel des dossiers dans Follaw (les textes ont pu être amendés ou retirés côté source depuis la rédaction initiale du HANDOFF).

### 3.5 Ce qui reste à faire (moyen terme)

- **PISTE Légifrance OAuth2** — connecteur prêt (`src/sources/piste.py`), désactivé par défaut. Secrets `PISTE_CLIENT_ID` / `PISTE_CLIENT_SECRET` à créer côté GitHub pour doubler la source JORF. Pas prioritaire tant que DILA OPENDATA tient.
- **Procédure législative** — Cyril veut qu'on maîtrise les étapes (dépôt → commission → séance → adoption → promulgation) avant de patcher le tri/filtre des dossiers. Voir `reference/procedure_legislative.md` si créé, sinon prérequis avant de toucher au status_label dans `site_export`.
- **Audit des 51 sources** — `scripts/audit_sources.py` fait un ping HEAD. À lancer périodiquement pour détecter les 404 silencieux.
- **Coverage tests** — `store.py`, `site_export.py`, `digest.py` pas de test unitaire (sauf smoke test digest). Pas bloquant, mais à considérer si on refactore.

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

## 5bis. Scripts de commit (préparés pour le bash final de Cyril)

**R11f est déjà commit localement (`afa6cd7`).** Il reste donc **2 commits** à enchaîner demain matin.

Tests à lancer avant chacun pour validation rapide :

```bash
source .venv/bin/activate && python -m pytest tests/ -q
```

Attendu : `91 passed`.

### Commit 1 — R11g (audit sources)

```bash
git add config/sources.yml
git commit -m "R11g — audit sources post-R11e : desactivations + fix URL MEAE

Nettoyage des sources 0-items et URL obsoletes revelees par le recap
WARNING de R11e :

1. Doublons Senat (R11g-1) : senat_dosleg et senat_questions remplaces
   fonctionnellement par senat_akn et senat_questions_1an depuis longtemps
   (0 items recurrents en prod). enabled: false.

2. MEAE (R11g-2) : l arbo /salle-de-presse/ du site diplomatie.gouv.fr a
   ete totalement supprimee (404 sur racine + toutes sous-pages). Le listing
   communiques vit maintenant sous
   /presse-et-ressources/decouvrir-et-informer/actualites/ (verifie live :
   HTTP 200, 243 ko, 12 liens actualites).

3. Scrapers WAF-bloques (R11g-4) : sante.gouv.fr et travail-emploi.gouv.fr
   servent une page 'Request Rejected' (WAF F5 ASM) a tout User-Agent
   Mozilla. Desactivation jusqu a ce qu un navigateur headless soit
   disponible, ou qu un RSS officiel soit trouve.

4. CNOSF SPA Nuxt (R11g-4) : cnosf.franceolympique.com/actualites ne rend
   les articles qu apres hydratation JS (sitemap.xml disponible pour fix
   propre ulterieur, non critique). Desactivation."
```

### Commit 2 — R12 (volet UX/UI)

```bash
git add src/site_export.py src/sources/senat.py src/digest.py \
        site/static/style.css \
        site/layouts/agenda/list.html \
        site/layouts/partials/sidebar.html \
        tests/test_site_export_fixups.py \
        HANDOFF.md
git commit -m "R12 — refonte UX : agenda, dosleg, dates/mots-cles, questions, CR

Repond aux 5 chantiers UX demandes par Cyril. Tous les fix sont in-memory
a l export (_fix_*_row dans site_export.py), idempotents, evitent un reset DB.

UX-A : agenda
  - Sidebar : les liens pointent vers /items/agenda/ (pas vers la source).
    Titres tronques a 60 chars.
  - Page /items/agenda/ : plus de liens dans les occurrences (consultation
    calendaire). Titres en pleine longueur.
  - _fix_agenda_row retire le prefixe 'Agenda - ' / 'Agenda - ' / 'Agenda - '
    en tete des titres agenda/communiques.

UX-B : dossiers legislatifs
  - Fenetre 730j -> 1095j (3 ans). dossiers_legislatifs ajoute a
    STRICT_DATED_CATEGORIES (plus de fallback inserted_at -> les vieux
    items sans date, type dopage 1990, disparaissent).
  - _fix_dossier_row capitalise la 1re lettre des titres Senat pre-patch
    (projet de loi -> Projet de loi).

UX-C : dates + mots-cles
  - Dates en rouge (#DA4431) + gras, partout (home, dosleg-card, digest).
  - Mots-cles : retrait background + color, italique + gras, separateur
    virgule entre kw. .kw-tag[data-family] rendu uniforme.

UX-D : questions
  - Retrait de '-> ministere' et '[sort]' dans les titres (patch
    connecteur Senat + _fix_question_row pour la DB historique).
  - Resolution des 'Depute PAxxxx' residuels via cache AMO a chaque export.

UX-E : comptes rendus
  - Fenetre 30j -> 180j (_fix_cr_row recale published_at sur la date de
    seance, souvent plusieurs mois en arriere).
  - Bug revele : store.SCHEMA n a jamais persiste 'snippet' -> digest et
    site affichaient tous les CR sans extrait. Fix : rebuild en memoire
    via KeywordMatcher.build_snippet au moment de l export.
  - Extraction theme Senat via extract_cr_theme pour les CR pre-patch
    dont le titre etait 'CR integral - d20260205.xml'.

Tests : 25 nouveaux cas dans test_site_export_fixups.py. Total 91/91 verts."
```

### Push final (R11f + R11g + R12 d'un coup)

```bash
git push origin main
```

---

## 5. Prochaine session — amorce

L'étape R11d (fix URL AN + durcissement fetch) est close. Pistes à ouvrir dans l'ordre :

1. **Décision `data/an_texte_to_dossier.json`** — tracker ou gitignore. Si tracker, faire un commit séparé avec le cache à jour (~570 Ko) pour speed-up premier run CI après reset DB.
2. **AN 53/54 manquants** — si l'audit Follaw les juge importants, élargir `since_days` de `an_amendements` à 60 ou 90 et relancer un run isolé. Si leur absence est due à un filtre date, ce sera résolu par la purge complète lors du prochain `reset_db` workflow.
3. **Procédure législative** — Cyril veut qu'on maîtrise les étapes avant de patcher le tri/filtre des dossiers. Prérequis à toute évolution du `status_label` dans `site_export`.
4. **Liste `EXPECTED_ZERO_HIT_SOURCES`** si le récap WARNING de `run_all` devient bruyant sur certaines sources qui ne publient pas tous les jours (weekend Sénat, ministères en pause estivale, etc.).
5. **Audit des 51 sources** via `scripts/audit_sources.py` — maintenant que R11d a renforcé la visibilité, les 404 latents vont remonter plus vite. Première passe conseillée la semaine prochaine pour établir un baseline.

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
| R12 | Volet UX/UI en réponse aux 5 chantiers Cyril : agenda (liens+titres+taille), dossiers légis (3 ans + statut + majuscule), dates/mots-clés restylées, questions épurées + AMO résolu, CR visibles + extraits (`snippet` reconstruit à l'export, bug historique du schéma SQL qui n'a jamais persisté `snippet`). | — |
| R11g | Audit sources post-R11e : désactive doublons Sénat (`senat_dosleg`, `senat_questions`), désactive WAF-bloqués (`min_sante`, `min_travail`), désactive SPA Nuxt (`cnosf`), fix URL MEAE refondue (`/presse-et-ressources/decouvrir-et-informer/actualites/`). | — |
| R11f | Fix tz-bug `an_agenda` : `parse_iso` / `_parse_date_any` / `_parse_dt` normalisent en naïf UTC au lieu de propager l'aware ISO 8601. Convention "tout naïf" du projet enfin homogène. | — |
| R11e | Durcissement `fetch_bytes_*` : no-retry 4xx, log.error explicite, récap `run_all`. A révélé 3 bugs latents auparavant invisibles (diplomatie 404, an_agenda tz-bug, 8 sources 0-items). | — |
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
