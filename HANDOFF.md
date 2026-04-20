---
title: Veille Parlementaire Sport — Handoff
maintainer: Cyril Mourin
last_updated: 2026-04-20
---

# Handoff — Veille Parlementaire Sport

Ce document est le point d'entrée pour reprendre le projet sans contexte préalable. Il résume l'architecture, l'état au **2026-04-20**, les décisions prises, ce qu'il reste à faire et les pièges connus.

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

## 3. État au 2026-04-20

### 3.1 Dernier commit en date

```
9c4af6b R11 — Amendements : fix AN parser + pivot Sénat per-texte
```

Sur `main`, pushed to `origin/main`. Working tree propre sauf un fichier untracked (`data/an_texte_to_dossier.json` — cache généré, à décider s'il faut le tracker).

### 3.2 Ce qui fonctionne

- **44/44 tests verts**.
- **R9** — Sénat : CR séance avec dates réelles, titres lisibles, URLs sommaire journalier. AN : CR avec date séance + thème depuis XML Syceron.
- **R10** — Publications : fenêtre 90j, filtre strict `published_at`, réactive ANS (soft-fail des timeouts).
- **R11a** — Paths JSON de `_normalize_amendement` corrigés. Anciens paths renvoyaient `None` systématiquement → 0/5683 matchés. Corrigés : `identification.numeroLong`, `corps.contenuAuteur.dispositif` + `exposeSommaire`, `signataires.auteur.groupePolitiqueRef`.
- **R11b** — Amendement enrichi avec titre du dossier parent dans summary → le matcher retombe sur le thème même quand le texte de l'amendement ne cite pas les mots-clés. Test Follaw reproduit : Ollivier N°6 sur "Protéger mineurs" matche `clubs sportifs`.
- **R11c** — Pivot Sénat : `senat_ameli.zip` désactivé (c'était un dump PostgreSQL, pas un zip de CSV — 0 item ingéré depuis des mois). Remplacé par `senat_amendements` (source `akn_discussion`) qui itère `depots.xml` et fetche `jeu_complet_<session>_<num>.csv` + variante commission.
- Site UX/UI : layout central + sidebar, recherche client-side, thématiques pliées avec compteur, agenda en module à droite, favicon Sideline, maquette dossiers législatifs façon AN.
- Cache AMO : script weekly + workflow idempotent.

### 3.3 Volumes DB actuels

```
questions              8 337
dossiers_legislatifs   6 537
agenda                 6 468
comptes_rendus         3 292
communiques            1 160
amendements                0   ← purgés par reset_category avant re-ingest R11
```

Top sources :

```
an_agenda                 6 411
senat_questions_1an       5 263
senat_promulguees         4 291
senat_cri                 2 801
senat_qg                  2 330
an_dossiers_legislatifs   1 336
```

### 3.4 Ce qui reste à faire (immédiat)

1. **Re-ingest amendements post-R11** — la purge `reset_category amendements --yes` a été lancée (5683 items supprimés). Il faut maintenant lancer `python -m src.main run --since 7 --no-email -v` pour valider que :
   - les amendements AN sont matchés à nouveau (n'importe quel volume > 0),
   - les 4 amendements Follaw de référence apparaissent : AN 52/53/54/56 sur le dossier "Renforcer la sécurité…JO 2024" (match `paris 2024, les jo`),
   - l'amendement Sénat Ollivier N°6 sur "Protéger mineurs" apparaît avec `clubs sportifs`.
   - Lancer via venv : `source .venv/bin/activate && python -m src.main run --since 7 --no-email -v`.
2. **Décider du sort de `data/an_texte_to_dossier.json`** — actuellement untracked. Soit l'ajouter à `.gitignore` (cache local), soit le commit pour speed-up premier run CI. Logique actuelle : régénéré par `assemblee._harvest_texte_refs` si absent.
3. **Reset DB prévu** (memo en memory) — à faire lors de la prochaine modif de `daily.yml` pour re-normaliser l'historique complet avec les parsers R9+R10+R11.

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

- **Amendement ≠ texte du dossier** : l'amendement cite rarement les mots-clés sport littéralement. **Il faut enrichir le haystack avec le titre du dossier parent** (logique R11b). Sans ça, N°6 Ollivier sur les clubs sportifs échoue.
- **Priorité summary** : titre du dossier **en premier** dans summary, puis objet, puis dispositif. Si on inverse, le dispositif (souvent long et générique) peut dominer l'extrait affiché.
- **`ANS` seul retiré** des keywords : faux positifs massifs (unidecode → "ans"). Seulement `Agence nationale du sport` en full.
- **`ARCOM` seul retiré** : faux positifs audiovisuel. Seulement formes contextualisées (`ARCOM sport`, `ARCOM paris sportifs`, etc.).

### 4.5 Publications (R10)

- **`STRICT_DATED_CATEGORIES = {'communiques'}`** : pas de fallback `inserted_at`. Élimine les rapports Sénat CSV sans date, pages pivot html_generic, agendas hebdo datés en fin de semaine à venir.
- **ANS timeouts intermittents** : `_common.fetch` absorbe les `ConnectTimeout` via soft-fail (ne bloque pas le pipeline).

### 4.6 Méthodologie

- **Toujours chercher la doc officielle avant de scripter du diag** — parser communautaire reconnu ou XSD officiel d'abord, tâtonnement ensuite. Règle mémorisée.
- **`upsert_many` ne met pas à jour** si le `hash_key` existe déjà. Après un patch parser, il **faut** purger la catégorie (`scripts/reset_category.py`) pour forcer la ré-ingestion.

---

## 5. Prochaine session — amorce

1. Activer venv et lancer pipeline : `source .venv/bin/activate && python -m src.main run --since 7 --no-email -v`.
2. Interroger DB :
   ```bash
   sqlite3 data/veille.sqlite3 \
     "SELECT title, matched_keywords FROM items WHERE category='amendements' AND source_id='senat_amendements' LIMIT 20;"
   ```
3. Confirmer les cas Follaw (AN 52/53/54/56 JO 2024 + Ollivier N°6).
4. Si OK → commit la mise à jour du digest + push → laisser tourner le cron quotidien.
5. Si KO → re-diag avec `dry -v` et inspection des `raw` JSON des items qui devraient matcher.

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
