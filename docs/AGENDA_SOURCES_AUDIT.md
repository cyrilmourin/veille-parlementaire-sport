# Audit & catalogue des sources agenda — Veille parlementaire sport

_Rédigé : 2026-04-22. Contexte : demande Cyril d'étendre la veille agenda à tous les ministères + AAI + juridictions, tout en corrigeant les bugs de date._

---

## 0. Résumé exécutif

Trois constats clés, sur lesquels tout le plan repose :

1. **AN agenda — le parser est correct, c'est le stockage qui est bugué.** Le dump officiel `Agenda.json.zip` contient 6412 fichiers dont 6412 ont `timeStampDebut` renseigné, et notre `_normalize_agenda` actuel extrait correctement la date pour 100 % d'entre eux. Pourtant la DB contient 6411/6412 items AN agenda avec `published_at = NULL`. Cause : `store.upsert_many` fait un `INSERT` pur + `except IntegrityError: pass`, donc les anciens items ingérés avec un parser antérieur (avant l'ajout du fallback `cycleDeVie.chrono.creation` en R13-L) ne sont jamais mis à jour, même quand le nouveau parser ré-ingère la même clé avec une date correcte.

2. **Cloudflare bloque la majorité des sites gouvernementaux** (info.gouv.fr, www.gouvernement.fr, plusieurs ministères) sur des requêtes HTTP non-navigateur. `senat.fr` aussi sur les sub-paths de `/agenda/` (cal.json + pages quotidiennes retournent "Accès restreint" ou 404 malgré le cache navigateur). L'agenda Sénat actuel (`html_generic` sur `/agenda/`) remonte donc quasiment rien d'exploitable en prod.

3. **Il n'existe pas d'Open Data agenda unifié** côté exécutif — chaque institution publie sa propre liste, souvent dans un format SPA/JS qui résiste au scraping direct. Conséquence : il faut un catalogue source-par-source avec la stratégie d'extraction adaptée (sitemap, JSON-LD, RSS, iCal, ou rendu headless).

**Plan d'action recommandé :**
- Phase A — **Fix structurel stockage** : passer `upsert_many` sur un `INSERT ... ON CONFLICT DO UPDATE` ciblé (au minimum pour `published_at`, `title`, `summary`, `raw`). Débloque 6411 dates AN agenda + ~5200 dates dossiers + ~1180 dates communiqués sans toucher aux scrapers.
- Phase B — **Fix Hugo frontmatter** : dans `site_export`, ne plus écrire `type: <cat>` si aucune `date:` n'a pu être déterminée (fallback = `inserted_at` ou date extraite du contenu). Arrête le filtre silencieux Hugo qui supprime les items sans date.
- Phase C — **Extensions agenda** : nouvelles sources priorisées par accessibilité (sitemaps XML + flux JSON > Open Data structurés > rendu headless via Chrome).

---

## 1. Diagnostic source par source

### 1.1 AN agenda (existant — `an_agenda`)

| Champ | Valeur |
|---|---|
| URL | `https://data.assemblee-nationale.fr/static/openData/repository/17/vp/reunions/Agenda.json.zip` |
| Format | `json_zip` (6412 JSON, ~25 Mo décompressé) |
| Accessible sandbox | Oui (HTTP 200, pas de WAF) |
| Champ date officiel | `reunion.timeStampDebut` (ISO8601, 100 % rempli) |
| Structure fichier | `RUANR5L{16,17}S{YYYY}IDC{NNN}.json` (commissions) ou `IDS{NNN}.json` (séances) |
| Parser actuel | `_normalize_agenda` dans `src/sources/assemblee.py:1395` — correct |

**Diagnostic terrain** (dump téléchargé aujourd'hui, 6412 fichiers analysés) :

```
L16 : dated=18   nodate=0 dropped=0
L17 : dated=6275 nodate=0 dropped=0
```

**État DB aujourd'hui** :
```
an_agenda : total=6412, nodate=6411  (99.98 % NULL)
```

**Cause racine confirmée** (voir `src/store.py:54-75`) : `INSERT` pur, `IntegrityError` avalée. Les items déjà présents avant R13-L gardent leur `published_at=NULL`, les re-ingestions ne patchent rien.

**Fix** : soit purge + re-fetch (`DELETE FROM items WHERE source_id='an_agenda'` puis relance pipeline), soit vrai upsert. Privilégier le vrai upsert car le même bug touche `dossiers_legislatifs` (5201 NULL) et `communiques` (1183 NULL).

**Priorité : P0 (bloquant tous les autres fix agenda).**

---

### 1.2 Sénat agenda (existant — `senat_agenda`)

| Champ | Valeur |
|---|---|
| URL config | `https://www.senat.fr/agenda/` (trailing slash) |
| URL réelle qui répond | `https://www.senat.fr/agenda` (sans slash, 6.6 Ko) |
| Format config | `html` (parser `html_generic`) |
| Accessible sandbox | Partiel — l'index répond, les sub-paths (cal.json, daily pages) non |

**Architecture effective du site sénatorial** : l'index `/agenda` est une SPA AngularJS (jQuery + clndr.js + moment.js) qui charge :
- `cal.json` (index des jours avec items)
- `Seance/agl{DDMMYYYY}.html`
- `Commissions/agl{DDMMYYYY}.html`
- `Missions/agl{DDMMYYYY}.html`
- `Delegation/agl{DDMMYYYY}.html`
- `Senat/agl{DDMMYYYY}.html` (bureau, conférence des Présidents)
- `GroupesPolitiques/agl{DDMMYYYY}.html`
- `Divers/agl{DDMMYYYY}.html`
- `Global/agl{DDMMYYYY}Print.html` (vue imprimable **tout-en-un** — la meilleure cible)

**Problème** : tous ces sub-paths retournent 404 + page "Accès restreint" (100 Ko) depuis la sandbox, même avec User-Agent + Referer complets. Diagnostic : restriction serveur (header WAF, peut-être session cookie posé par la SPA AngularJS). À re-tester depuis GitHub Actions — l'IP datacenter peut être acceptée.

**Flux iCal à investiguer** : la page SPA mentionne "M'abonner à l'agenda d'une instance" vers `Global/instances.html` — probablement des `.ics` publics par commission. Piste à prioriser car iCal = format stable et simple à parser.

**data.senat.fr** : domaine accessible (HTTP 200 sur racine) mais `/agenda`, `/senateurs` et autres sous-URLs renvoient 404. Pas d'agenda exposé en Open Data.

**Fix recommandé** :
1. Tester depuis GitHub Actions (runners Azure US/EU) si `agl{DDMMYYYY}Print.html` répond.
2. Si oui : nouveau format dédié `senat_agenda_daily` qui itère J-7 → J+30 et parse le HTML quotidien.
3. Sinon : fallback Chrome MCP en local pour un bootstrap mensuel, recharge incrementale via iCal.

**Priorité : P1 (valeur métier forte, blocage technique à lever).**

---

### 1.3 Matignon (existant désactivé — `matignon_agenda`)

| Champ | Valeur |
|---|---|
| URL historique config | `https://www.gouvernement.fr/agenda` (Cloudflare JS challenge, abandonnée) |
| Nouvelle URL (Cyril) | `https://www.info.gouv.fr/agenda/ministre/sebastien-lecornu` |
| Accessible sandbox | **Non** — `cf-mitigated: challenge` (Cloudflare managed challenge) |
| Sitemap | `sitemap.xml` = 403 ; `robots.txt` = 200 mais n'indexe pas d'agenda |

**Test direct** : tous les sous-chemins `/agenda`, `/api/agenda`, `/rss/*`, `/sitemap.xml` retournent `HTTP 403 + cf-mitigated: challenge`. Seul `robots.txt` passe (liste de disallow standard, rien d'exploitable).

**Stratégies possibles** :
1. **Chrome MCP** (local) pour un bootstrap, persistence en DB.
2. **Playwright** en CI (avec stealth) — complexe à stabiliser.
3. **Archive.org CDX** : fallback si on veut une couverture historique sans se confronter au WAF (latence J+2 à J+7).

**DB aujourd'hui** : 57 items historiques tous sans date (`nodate=57`). Ces items viennent de l'ancienne URL `gouvernement.fr/agenda` avant qu'elle ne soit bloquée. À purger après mise en place du nouveau scraper.

**Priorité : P1 (demande explicite Cyril, mais blocage technique — nécessite Chrome MCP côté local).**

#### 1.3.bis — Contournement R15 (2026-04-22) : conclusion négative

Recherche étendue faite après R15 sur les pistes alternatives :

1. **data.gouv.fr** — l'organisation « Premier ministre »
   (`534fffa5a3a7292c64a7809e`) publie 180 datasets mais **aucun**
   contenant `agenda` ou `déplacement` dans son titre/slug. Pas
   d'équivalent du `fr-esr-agenda-ministre` (ESR) ou
   `agenda-du-ministre-de-leducation-nationale` (EN) côté Matignon.
2. **vie-publique.fr** (qui archive toutes les déclarations PM) — tous
   les endpoints RSS / listing testés (`/rss.xml`, `/feed`, `/discours/rss.xml`,
   landing filtré par fonction) renvoient **230–280 octets de stub JS
   redirect**. L'API côté client requiert un rendu navigateur. Pas
   exploitable en HTTP direct.
3. **gouvernement.fr / info.gouv.fr** — Cloudflare managed challenge,
   bloqué sandbox et CI (cf. §1.3 ci-dessus).

**Conclusion R15** : pas de source Matignon exploitable en HTTP direct
sans rendu JS / bypass WAF. L'activité du PM continue d'être captée
indirectement via :
- `elysee_sitemap` → communiqués Conseil des ministres (hebdo, avec date)
- `jorf_dila` → décrets / ordonnances signés PM
- `an_questions` + `senat_questions` → questions au Gouvernement
  (porteur = PM pour questions d'actualité le mardi AN / mercredi Sénat)

Si besoin de l'agenda physique PM : Chrome MCP en local (hors CI) ou
Playwright-stealth en production séparée.

---

### 1.4 Élysée (existant — `elysee_agenda` + `elysee_sitemap`)

| Champ | Valeur |
|---|---|
| URL agenda direct | `https://www.elysee.fr/agenda` (200, 296 Ko) |
| URL sitemap | `https://www.elysee.fr/sitemap.static.xml` (rubrique `agenda`) |
| Accessible sandbox | Oui pour les deux |
| JSON-LD | **Absent** (0 `<script type="application/ld+json">`) |
| `<article>` | 0 (SPA React) |

**Diagnostic** : la page HTML rend des cards d'événements mais tout est hydraté côté client — scrapable en brut par le matcher de mots-clés, mais pas d'extraction date/lieu structurée possible sans rendu JS. Le `sitemap` existant capture bien les URLs canoniques des événements (`https://www.elysee.fr/emmanuel-macron/<slug>`) → c'est ce qui fonctionne aujourd'hui, via `elysee_sitemap` catégorie `communiques`.

**Observation** : la source `elysee_agenda` (catégorie agenda) semble redondante avec `elysee_sitemap` (catégorie communiques) qui filtre sur rubrique. Vérifier dans `sources.yml` si l'une des deux doit être supprimée ou re-targettée.

**Fix recommandé** : garder `elysee_sitemap`, supprimer `elysee_agenda` (ou le réorienter vers un parsing via Chrome MCP pour récupérer les dates d'événements structurées).

**Priorité : P2.**

---

### 1.5 Agenda des ministères (nouvelles sources — à créer)

**Principe** : chaque ministère a son propre site + agenda, mais la plupart sont derrière Cloudflare. Inventaire ciblé et stratégie par site :

| Ministère | URL probable agenda | Accès sandbox | Stratégie |
|---|---|---|---|
| Éducation nationale | `education.gouv.fr/agenda-ministre` | Cloudflare | Chrome MCP |
| Intérieur | `interieur.gouv.fr/agenda` | Cloudflare | Chrome MCP |
| Économie | `economie.gouv.fr/agenda` | Cloudflare | Chrome MCP |
| Travail | `travail-emploi.gouv.fr/actualites` | WAF | Chrome MCP |
| Santé | `sante.gouv.fr/actualites` | WAF | Chrome MCP |
| Sports (ministre délégué) | `sports.gouv.fr/agenda-previsionnel-de-marina-ferrari-1787` | Déjà validé (HTML statique) | Scraper dédié `min_sports_agenda_v2` |
| Justice | `justice.gouv.fr/agenda` | À tester | - |
| Culture | `culture.gouv.fr/Actualites/Agenda-du-ministre` | À tester | - |
| Transitions écologique | `ecologie.gouv.fr/agenda-ministre` | À tester | - |
| Transports | `ecologie.gouv.fr/ministre-delegue-transports` | À tester | - |
| Outre-mer | `outre-mer.gouv.fr` | À tester | - |
| Agriculture | `agriculture.gouv.fr` | À tester | - |
| Armées | `defense.gouv.fr/ministre-armees/agenda` | À tester | - |
| Europe / Affaires étrangères | `diplomatie.gouv.fr` | À tester | - |

**Observation clé** : une grande partie des ministères publient aussi leur agenda sur `info.gouv.fr/agenda/ministre/<slug>` (nouvelle plateforme unifiée). Mais elle est Cloudflare-protégée comme vu plus haut. Si Chrome MCP règle le challenge une fois, on peut avoir un **scraper unifié "info.gouv"** qui itère sur tous les slugs ministres via un sitemap (à trouver) plutôt que 14 scrapers séparés.

**Fix recommandé** :
1. Tester chaque URL ministère en CI (GitHub Actions) pour dresser la vraie matrice d'accessibilité (les IP datacenter sont parfois acceptées là où la sandbox est bloquée).
2. Pour les bloqués, scraper unique via `info.gouv.fr` avec Chrome MCP.
3. Pour les accessibles, un handler `ministry_agenda` générique qui accepte `url + selector` dans `sources.yml`.

**Priorité : P1 pour Sports, Éducation, Intérieur, Santé (domaines les plus sport-related) ; P2 pour le reste.**

---

### 1.6 Présidents des chambres (nouvelles sources)

| Source | URL probable | Stratégie |
|---|---|---|
| Président AN (Yaël Braun-Pivet) | `https://presidence.assemblee-nationale.fr/agenda` ou fil presse AN | À tester |
| Président Sénat (Gérard Larcher) | page dédiée senat.fr (via `/agenda` -> "Agenda du Président") | Même WAF que senat_agenda |

Le menu SPA `/agenda/` du Sénat mentionne explicitement un onglet **"Agenda du Président"** (`/agenda/President/index.html`). Même traitement technique que pour `senat_agenda`.

**Priorité : P2.**

---

### 1.7 Autorités administratives indépendantes (nouvelles — Cyril)

| AAI | Site | Rubrique agenda/actualités | Accessible sandbox (probable) |
|---|---|---|---|
| AFLD (Agence française de lutte contre le dopage) | `afld.fr` | `/actualites` ou flux presse | À tester |
| ARCOM | `arcom.fr` | `/nous-connaitre/actualites` ou `/presse` | À tester |
| ANJ (Autorité nationale des jeux) | `anj.fr` | `/presse/actualites` | À tester |
| ANS (Agence nationale du sport) | `agencedusport.fr` | `/presse` ou `/actualites` | À tester |

**Format attendu** : pas d'agenda public typiquement, mais des communiqués et actualités datés = catégorie `communiques` plutôt que `agenda`. L'info sport passe mieux dans `communiques` de toute façon (les AAI n'ont pas d'agenda public type parlementaire).

**Priorité : P2, mais valeur métier forte (AFLD + ANS = cœur de sujet).**

---

### 1.8 Juridictions (nouvelles — Cyril)

| Juridiction | Site | Rubrique utile | Accessible sandbox (probable) |
|---|---|---|---|
| Conseil d'État | `conseil-etat.fr` | `/decisions-de-justice` + `/actualites` | Oui (WAF léger) |
| Conseil constitutionnel | `conseil-constitutionnel.fr` | `/decisions` + `/actualite` | Oui |
| Cour de cassation | `courdecassation.fr` | `/decisions` + `/a-la-une` | Oui |

**Format** : décisions + audiences publiques datées. Pour le sport, les décisions QPC sur des questions sport/dopage sont rares mais importantes quand elles arrivent. Plutôt catégorie `communiques` ou nouvelle catégorie `jurisprudence`.

**Priorité : P3.**

---

## 2. Bug stockage `upsert_many` — détail technique

### Code actuel (`src/store.py:47-77`)

```python
def upsert_many(self, items: Iterable[Item]) -> int:
    """Insère les nouveaux items. Renvoie le nombre d'insertions."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur = self.conn.cursor()
    inserted = 0
    for it in items:
        try:
            cur.execute("INSERT INTO items (...) VALUES (...)", (...))
            inserted += 1
        except sqlite3.IntegrityError:
            pass  # ← silence total, aucune mise à jour
    self.conn.commit()
    return inserted
```

### Conséquences observées

- **AN agenda** : 6411/6412 items ont `published_at=NULL` (ingérés avec un parser antérieur).
- **Dossiers législatifs** : 5201/6766 items ont `published_at=NULL`.
- **Communiqués** : 1183/1326 items ont `published_at=NULL`.

**Pourquoi ça se voit sur le site** : `_fix_agenda_row` et cie tentent de rafraîchir la date au rendu, mais `_fix_agenda_row` ne remonte que ce que `raw` contient, or `raw` est lui aussi figé à la 1re insertion. Résultat : Hugo filtre silencieusement en raison du `date:` manquant dans le frontmatter.

### Fix proposé

```python
cur.execute("""
    INSERT INTO items (
        hash_key, source_id, uid, category, chamber, title, url,
        published_at, summary, matched_keywords, keyword_families,
        raw, inserted_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(hash_key) DO UPDATE SET
        -- Champs enrichis au fil du temps : on remplace
        published_at = COALESCE(excluded.published_at, items.published_at),
        title        = CASE WHEN excluded.title != '' THEN excluded.title ELSE items.title END,
        summary      = CASE WHEN length(excluded.summary) > length(items.summary)
                            THEN excluded.summary ELSE items.summary END,
        raw          = excluded.raw,
        matched_keywords = excluded.matched_keywords,
        keyword_families = excluded.keyword_families
        -- NB : on ne touche PAS inserted_at (tracer la 1re ingestion)
    WHERE excluded.published_at IS NOT NULL
       OR excluded.summary != items.summary
       OR excluded.matched_keywords != items.matched_keywords
""", (...))
```

**Invariants à respecter** :
- `inserted_at` : jamais modifié (utilisé par `fetch_matched_since` pour le digest quotidien).
- `hash_key`, `source_id`, `uid`, `category`, `chamber` : stables, ne sont pas mis à jour.
- `published_at` : ne jamais écraser une date connue par NULL (d'où `COALESCE`).
- `title` / `summary` : remplacer uniquement si le nouveau est non-vide ou plus riche.

### Tests à ajouter avant le merge

```python
def test_upsert_refills_null_date():
    store.upsert_many([item_sans_date])
    store.upsert_many([item_avec_date])  # même hash_key
    assert store.get(item.hash_key).published_at == item_avec_date.published_at

def test_upsert_preserves_inserted_at():
    store.upsert_many([item_v1])
    first_insert = store.get(item.hash_key).inserted_at
    time.sleep(1.1)
    store.upsert_many([item_v2])
    assert store.get(item.hash_key).inserted_at == first_insert

def test_upsert_does_not_blank_existing_date():
    store.upsert_many([item_avec_date])
    store.upsert_many([item_meme_hash_sans_date])
    assert store.get(item.hash_key).published_at is not None
```

---

## 3. Fix Hugo frontmatter — fallback date manquante

### Constat

`src/site_export.py:1496` écrit :

```python
if published_at:
    md.write(f"date: {published_at[:19]}Z\n")
```

Quand `published_at=None` → pas de `date:` dans le frontmatter. Combiné avec `type: agenda`, Hugo exécute un filtre implicite qui masque l'item de `/agenda/` (comportement TOML-strict).

### Fix proposé

```python
# Priorité : published_at → raw.date_fallback → inserted_at → build_date
date_str = (
    (published_at[:19] + "Z") if published_at
    else (raw.get("date_fallback", "")[:19] + "Z") if raw.get("date_fallback")
    else (inserted_at[:19] + "Z") if inserted_at
    else build_time.isoformat(timespec="seconds") + "Z"
)
md.write(f"date: {date_str}\n")
```

**Convention** : toujours émettre une `date:` dans le frontmatter pour éviter le filtrage Hugo. Le `inserted_at` est toujours disponible (contrainte NOT NULL en DB), donc le fallback final est fiable.

---

## 4. Plan d'implémentation proposé

### Sprint 1 — Débloquer l'existant (P0)

1. **Fix `store.upsert_many`** avec vraie logique upsert (`ON CONFLICT DO UPDATE`).
   - Tests unitaires de régression (3 cas ci-dessus).
   - Relance d'ingestion `an_agenda` + `an_dossiers_legislatifs` + `elysee_sitemap` → devrait repeupler les `published_at` NULL.
2. **Fix `site_export` frontmatter** : toujours émettre `date:` avec fallback `inserted_at`.
3. **Rebuild site + digest** : vérifier que agenda AN remonte ~100 items datés et dossiers ~10-15 datés.

**Effort estimé : ½ journée.**

### Sprint 2 — Consolider Sénat (P1)

4. **Tester depuis CI** si `senat.fr/agenda/Global/agl*Print.html` répond (IP datacenter).
5. Selon résultat : nouveau format `senat_agenda_daily` itérant sur ±30j.
6. Parser dédié extrayant : titre, organe, heure, lieu, ODJ par section (Séance, Commissions, Missions, Délégations, Bureau, Groupes, Divers).

**Effort estimé : 1 journée.**

### Sprint 3 — Scraper info.gouv.fr (P1)

7. Via Chrome MCP local : 1er bootstrap de tous les `/agenda/ministre/<slug>` (extract sitemap via JS eval).
8. Persistance DB.
9. Pipeline CI qui **n'essaie pas** de scraper info.gouv.fr (toujours bloqué Cloudflare) → se repose sur le dernier bootstrap local, ré-importé via artefact ou fichier commit.

**Alternative** : discussion Cyril sur faisabilité d'un Playwright stealth en CI.

**Effort estimé : 1-2 jours.**

### Sprint 4 — AAI + Juridictions (P2-P3)

10. Handler générique `structured_actus` (HTML + CSS selector + champ date).
11. Ajout de 4-7 sources (AFLD, ARCOM, ANJ, ANS, CE, CC, Cassation).

**Effort estimé : 1 journée.**

---

## 5. Annexes — tests terrain (2026-04-22)

### A. Parser AN agenda sur le dump officiel

```
Dump : https://data.assemblee-nationale.fr/static/openData/repository/17/vp/reunions/Agenda.json.zip
Taille : 6.7 Mo zipped, 6412 fichiers JSON
Filename : RUANR5L{16,17}S{YYYY}ID{C|S}{NNN}.json

Résultats parser (_normalize_agenda actuel) :
  L16 : 18 items dated, 0 nodate, 0 dropped
  L17 : 6275 items dated, 0 nodate, 0 dropped
  TOTAL : 6293 / 6293 avec date (100%)
```

**Conclusion** : le parser actuel est correct, aucune action parser-side requise.

### B. État DB actuel (veille.sqlite3, ce jour)

```
questions              total= 8599  nodate=    0  ✓
amendements           total= 8164  nodate=    8  ✓
dossiers_legislatifs  total= 6766  nodate= 5201  ✗ (77% NULL)
agenda                total= 6476  nodate= 6475  ✗ (99.9% NULL)
  └─ an_agenda        total= 6412  nodate= 6411
  └─ matignon_agenda  total=   57  nodate=   57
  └─ min_sports_agenda total=   7  nodate=    7
comptes_rendus        total= 3292  nodate=    0  ✓
jorf                  total= 1589  nodate=    0  ✓
communiques           total= 1326  nodate= 1183  ✗ (89% NULL)
nominations           total=  228  nodate=    0  ✓
```

### C. Tests d'accès sandbox (user-agent Safari 17 macOS)

```
✓ 200  data.assemblee-nationale.fr/.../Agenda.json.zip
✓ 200  www.senat.fr/agenda                     (SPA AngularJS, 6.6 Ko)
✗ 404  www.senat.fr/agenda/cal.json            (Accès restreint)
✗ 404  www.senat.fr/agenda/Global/agl*Print.html
✗ 404  www.senat.fr/agenda/Commissions/agl*.html
✓ 200  www.senat.fr/travaux-parlementaires/commissions.html  (pas d'agenda, juste listing)
✗ 403  www.info.gouv.fr/agenda/ministre/sebastien-lecornu  (CF challenge)
✗ 403  www.info.gouv.fr/sitemap.xml             (CF challenge)
✓ 200  www.info.gouv.fr/robots.txt
✗ 403  www.info.gouv.fr/rss/agenda.xml
✓ 200  www.elysee.fr/agenda                     (SPA React, 296 Ko, 0 JSON-LD)
✓ 200  www.elysee.fr/sitemap.static.xml
```

---

## 6. Questions ouvertes pour Cyril

1. **Upsert vs purge** : OK pour introduire un vrai `INSERT ... ON CONFLICT DO UPDATE` ? (alternative : `DELETE + INSERT` par source à chaque run, plus simple mais perd l'historique `inserted_at`).
2. **Fenêtre ingestion agenda** : actuellement le parser ingère toute la législature L17 (≈6000 items). On garde ça ou on filtre dès la source (ex. J-30 → J+60) pour garder la DB compacte ? Cyril a mentionné "30 derniers jours + futurs seulement" dans `sources.yml:50`.
3. **Chrome MCP pour info.gouv.fr** : faisable en local mais pas en CI GitHub. Acceptable de n'avoir Matignon + ministères info.gouv.fr qu'en bootstrap manuel hebdomadaire ? Ou il faut absolument un scrape quotidien ?
4. **Catégorie `agenda` vs `communiques`** pour AAI/juridictions : l'essentiel sera des communiqués datés plutôt que des rendez-vous à venir. Préférence pour une catégorie `publications_sport` distincte ou on les mélange dans `communiques` existant ?
