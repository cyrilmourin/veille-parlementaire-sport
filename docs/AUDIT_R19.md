# Audit global — Veille Parlementaire Sport (R19 → R25)

*Rédigé le 2026-04-23 après R19 déployé. Mis à jour le 2026-04-23 avec R20 (IGESR + INJEP RSS + CC en communiqués), R21 (UX mobile + titre), R21b (script run_clean.sh), R22 (arbitrages AAI / juridictions : Cour des comptes réactivée en RSS, Autorité de la concurrence ajoutée, Cour de cassation / AMF / CNIL / HATVP / CADA / HCERES sortis du scope), R22a (passe dédup dosleg durcie : cumul `_merged_dossier_ids` dans le winner des passes 2a/2b pour que la passe 2c `dossier_id` voie encore le bridge AN↔Sénat — corrige le cas JOP Alpes 2030 où senat_promulguees écrasait le senat_akn_* porteur du `url_an`) et R22b (filtre `_filter_disabled_sources` au chargement de l'export + bump `SYSTEM_VERSION_LABEL` hardcodé R19 → R22a qui était resté figé depuis R13-G). Ajout le 2026-04-23 (soir) : R22c (fix CNOSF URLs protocol-relative + `url_filter` permissif), R22d (fix ANS extraction date dans cousins Drupal Views), R22e (parsing date FR numérique DD/MM/YYYY dans `html_generic` + flag `fetch_meta` activé sur 20 sources pour enrichir les summaries vides via meta description), R22f (cron daily déplacé de 06:00 UTC à 02:00 UTC = 04:00 Europe/Paris heure d'été), R22g (réécriture à l'export des titres legacy des questions AN pré-R13-L au format `"<auteur> | Question X n°NN — PAxxxx (GROUPE) : …"`), R22h (`questions` ajouté à `STRICT_DATED_CATEGORIES` + fenêtre 90 j explicite — plus de fallback `inserted_at` pour les questions sans `published_at`, cas concret 17-11612QE publié 2025-12-09 visible en prod alors que hors fenêtre), et le passage du titre du site à « Veille Institutionnelle Sport » (majuscule à « Institutionnelle »).*

*Addendum 2026-04-23 (nuit) — série R23/R24/R25 livrée dans la même journée :*

- ***R23-A → R23-O*** *— 14 rustines UX : sort prime l'état amendements (A), groupe parlementaire court (B), portraits parlementaires corrigés + agrandis 56×56 + Sénat via CSV « Fiche Sénateur » (C/C2/C3/C4/C5), retrait préfixe « Question de +1 an sans réponse » (D/D2), logo chambre au lieu du badge texte sur CR (E), extrait CR sans préambule Syceron (F), titre agenda AN précis — commission + objet (G), filtre sources par groupe v1 (H), nouvelles sources INSEP + FDSF (I + J), retrait préfixe « En cours : » dossiers (M), refonte filtres UI 5 familles `operateurs_publics` + `mouvement_sportif` splittés depuis `operateurs`, `jorf` retiré du filtre, fix `SPECIFIC_LAYOUT_CATS` avec `communiques` (O), puis R23-N plus tard — portraits sénateurs sur questions Sénat via cache amendements normalisé (lowercase + unidecode + civilité retirée + tokens triés, zéro réseau).*
- ***R24-A → R24b*** *— mode `ping` 17:30 : `PingState` persiste les UIDs matchés dans `data/ping_state.json` (A), CLI `python -m src.main ping` lit la DB pure et envoie un email court s'il y a des nouveautés (B/C), tests complets (D). Puis R24b étend le cron 17:30 à un run complet sans email pour que le site Hugo soit rebuild + redeploy aussi l'après-midi (le mail de 17:30 pointe vers du contenu frais, plus vers la version de 24 h). Impact GHA ~600 → ~900 min/mois.*
- ***R25-A → R25-H + R23-N*** *— bundle micro-ajustements UX livré en un seul commit (`800372c`) : Montserrat sur titres (A), extraits 500c amendements / 800c CR (B), dédup QAG vs question écrite via suffixe `G` réécrit en « Question au gouvernement n°nnnnG » avant la passe 1 de dédup (C), labels chambre longs CE / CC / CourComptes via partial `chamber-badge.html` (D), palette chambre complète — plus de gris par défaut, CNOSF bleu olympique `#0055a4` (E), AFLD + IGESR migrés `autorites → operateurs_publics` car AFLD = EPA sport et IGESR = service d'inspection État (F), « Autres autorités » en minuscule (G), bypass keywords pour `{ans, insep, injep, afld, cnosf, france_paralympique, fdsf, min_sports_actualites, min_sports_presse}` via `_apply_source_bypass` dans `src/main.py` (H). Bump `SYSTEM_VERSION_LABEL` R23h → R25. 341 → 346 tests.*

*Aucune refonte structurelle n'accompagne encore ce document : c'est un texte d'orientation pour sortir de la dynamique « rustines » et rendre l'outil durable. L'accumulation R23 + R24 + R25 en une seule journée (≈ 25 sous-items) confirme en particulier les §2.2 et §3 ci-dessous — le rythme de patch ne ralentit pas tant que les vagues 1-3 du §5 ne sont pas engagées.*

## 1. Objet initial — rappel

L'outil doit livrer, chaque jour, l'actualité **institutionnelle** du sport :

- **Exhaustivité** : couvrir tout ce qui compte côté Parlement (AN + Sénat), Gouvernement (Matignon + ministères), JORF, AAI, juridictions.
- **Précision** : ne remonter que ce qui est réellement « sport », sans bruit (presse tierce, doublons internes d'un même dossier, codes techniques).
- **Stabilité dans la durée** : fonctionner sans intervention humaine chaque matin, avec une capacité à absorber les évolutions de sources (changements d'URL, d'encodage, de format) sans effondrement silencieux.

## 2. Où en est-on aujourd'hui

### 2.1 Ce qui tient la route

- Le modèle `sources.yml` + handlers par format (RSS, HTML générique, XML zip, CSV, opendata AN, Syceron) est bon : ajouter une source = une entrée yaml + au plus un handler.
- Le matcher `keywords.py` est simple, testable, et livre un extrait.
- La pipeline quotidienne tourne sur GitHub Actions avec auto-déploiement du site Hugo. Le cycle « push → redeploy » est fiable.
- La DB SQLite (`store.py`) est compacte, versionnée, reproductible. C'est un bon socle.

### 2.2 Les signaux de dette

- **`site_export.py` fait tout** : charger, filtrer par fenêtre, dédupliquer par dossier, nettoyer les titres, reconstruire les snippets, écrire JSON + Markdown Hugo. ~1700 lignes, plus de 10 fonctions `_fix_*_row` + désormais une section « legacy title » pour les questions AN (R22g). C'est le point où la majorité des régressions apparaissent.
- **Chaque incident produit une rustine balisée `R13-A`, `R13-B`, …, `R18`, `R19-A..H`, `R22a..h`** — plus d'une trentaine à ce jour, avec un rythme R22c → R22h de 5 patches en une seule journée. Les commentaires datés rendent l'historique lisible mais la logique métier finit dispersée dans des `re.sub`, des tests `source_id == …` et des conditions par catégorie. Exemples récents :
  - **R22b** : `SYSTEM_VERSION_LABEL = "R19"` hardcodé en ligne 28 de `site_export.py` est resté figé depuis R13-G alors que 10 tags se sont empilés au-dessus — un simple `version.py` lu en CI suffirait à couper ce vecteur.
  - **R22g** : les questions AN ingérées avant le patch R13-L gardent leur ancien titre (`"<auteur> | Question X n°NN — PAxxxx (GROUPE) : …"`) dans la DB persistée via cache GHA. Il a fallu ajouter une regex `PA\d+ (…)` + reconstruction depuis `raw.analyse / tete_analyse / rubrique` **au moment de l'export** pour afficher un titre propre. Deux options alternatives existaient — reset DB ou migration ciblée — toutes deux rejetées pour ne pas perdre l'historique. Le patch est stable mais confirme qu'on paye l'absence de `title_clean` persisté en DB (§4.5).
  - **R22h** : une question publiée 2025-12-09 est restée visible en prod au 2026-04-23 alors que la fenêtre nominale questions est de 30 j — parce que `STRICT_DATED_CATEGORIES` n'incluait pas `questions` et que le fallback `inserted_at` laissait passer tout item sans `published_at`. Fix en deux lignes, mais cas emblématique du §3.3 (confondre filtre et nettoyage) : le contrat « publié depuis ≤ 90 j » n'était encodé nulle part, juste présumé par la valeur de `WINDOW_DAYS`.
- **Encoding** : l'épisode `ï¿œ` (R19-A, Sénat ISO-8859-15) et le décodage `cp1252 / utf-8 / iso-8859-1` (`assemblee._decode`) montrent qu'on gère l'encodage au cas par cas, handler par handler. Il n'existe pas de stratégie centralisée « bytes-in, texte propre-out ».
- **Dédup dossiers législatifs** : trois passes empilées (`_dedup_dosleg`, mapping AN↔Sénat, passe 2c par dossier_id) + un tiebreak URL dossier-législatif. Quand une source nouvelle a remonté les documents INTERNES d'un dossier (R19-B : `senat_theme_sport_rss`), la dédup n'a pas tenu → 8 doublons pour JOP Alpes 2030. Suite R22a : il a fallu ajouter `_merged_dossier_ids` pour que les IDs d'un loser survivent aux passes 2a/2b et restent visibles en 2c (sinon la clé `url_an` portée par un senat_akn_* disparaît quand un senat_promulguees plus récent gagne le tiebreak, et l'AN orphelin reste). Symptôme du problème de fond : la dédup devrait avoir `dossier_id` comme clé primaire et non pas patcher chaque passe intermédiaire pour ne pas perdre l'info (cf. 4.4).
- **Désactivation d'une source != disparition des items** : quand une source passe `enabled: false`, le fetcher s'arrête mais les rows déjà en DB continuent d'être ré-exportées jusqu'à expiration de la fenêtre (30 à 180 jours). Il a fallu R22b pour exposer `_filter_disabled_sources(rows)` dans `site_export._build`. Patch correct mais symptôme d'un modèle où la DB et la liste des sources actives vivent leur vie séparément — et où la conséquence pratique (`alpes_2030_news` R17 + `senat_theme_sport_rss` R19-B qui continuaient à polluer le site deux semaines après désactivation) n'a été détectée qu'après plainte utilisateur.
- **Sources silencieusement KO** : l'audit `scripts/audit_sources.py` a déjà révélé 6 sources à 0 items (R19 remonte à R12-13). Rien n'empêche qu'une source tombe à 0 demain sans qu'on le voie avant qu'un utilisateur se plaigne d'un trou.
- **Snippet** : la logique de construction de l'extrait vit à trois endroits (`keywords.build_snippet`, `site_export._load`, `digest.py`). Le préambule Syceron XML des CR AN a traversé plusieurs révisions avant d'être nettoyé (R19-G) parce que personne n'était clairement « propriétaire » de la forme finale du snippet.
- **CSS** : `site/static/style.css` est un monolithe. Chaque itération UX (sidebar, agenda, cartes dossiers, snippets) y ajoute des blocs. Pas de scoping par composant.
- **Pas de test de non-régression sur un incident passé** : on a un SDK de tests pytest, mais pas un pattern « pour chaque bug trouvé, un test qui y referme la porte ». Résultat : plusieurs régressions (codes PA, encoding, JOP Alpes 2030) sont réapparues sous une forme voisine.

## 3. Les cinq erreurs récurrentes

1. **Patcher au point de douleur plutôt qu'au point de cause.** Le `ï¿œ` a d'abord été masqué par un `.replace()`, puis corrigé en amont (passer `bytes` à feedparser). Systémiser : dès qu'une rustine vit en aval de la source, ouvrir le ticket « remonter à la source ».
2. **Ajouter une source sans contrat.** Une entrée dans `sources.yml` devrait être accompagnée d'une assertion lisible : « cette source renvoie ≥ N items / fenêtre Y, champs X présents, URL canonique format Z ». Sans ça, les sources dérivent (thème Sénat qui remontait 8 docs internes, Google News parasite).
3. **Confondre "filtre" et "nettoyage".** `_fix_question_row`, `_fix_cr_row`, `_fix_dosleg_row` font les deux. Un filtre (exclure) et un normalisateur (réécrire) devraient être des étapes distinctes avec des assertions propres.
4. **Supposer que la DB suffit.** Le schéma DB est minimal : on recalcule le snippet, le dossier_id, le status_label à chaque export. Chaque recalcul est une chance de régresser.
5. **Pas de détection proactive des trous.** Un scheduler silencieux qui fait 60 fetchs et log 50 succès, 10 « 0 items, 0 matched » ne déclenche aucun signal. Il faudrait que `0 items sur une source active` émette un warning visible dans le digest et dans un canal (Slack / email).

## 4. Recommandations structurantes

### 4.1 Pipeline en étapes pures et testables

```
fetch (bytes) ──▶ decode (texte propre) ──▶ parse (Item brut)
  ──▶ normalize (titre/url/date propres) ──▶ dedup (par dossier_id + fingerprint)
  ──▶ classify (catégorie + chambre + famille mot-clé)
  ──▶ snippet (1 seul module, 1 seule règle) ──▶ persist (DB)
  ──▶ export (JSON + Markdown)
```

Chaque étape prend des `Item` et retourne des `Item`. Chacune est testable isolément avec 5 à 10 fixtures de référence (un item AN, un Sénat, un JORF, etc.). `site_export.py` ne devrait être qu'un orchestrateur + template engine.

### 4.2 Centraliser le « cleanup texte »

Un seul module `src/textclean.py` qui gère :

- **Décodage depuis bytes** (respecte la PI XML + meta HTML + BOM, fallback ordonné). Plus de `.decode("utf-8", errors="replace")` silencieux dispersé.
- **Strip HTML + entités** (déjà dans `_clean_html`, à déplacer et exposer).
- **Strip bruit technique** : préambule Syceron (R19-G), codes PA résiduels (R19-C), métadonnées JORF boilerplate, etc. — avec un test unitaire par pattern.
- **Smart truncate** pour snippets et titres (évite de couper à l'intérieur d'un mot composé).

### 4.3 Contrat de source

Chaque entrée `sources.yml` devrait produire :

- un test auto « `pytest tests/contracts/test_source_{id}.py` » qui fetche une fois (ou utilise un cache), vérifie : ≥ 1 item sur 30 j, shape de titre conforme, URL canonique, pas de champ vide critique.
- un enregistrement « baseline » mis à jour mensuellement (snapshot de 3 items exemples) pour détecter les changements de format.

C'est la contrepartie de l'exhaustivité : 60 sources sans contrat = 60 sources qui peuvent tomber en silence.

### 4.4 Dédup avec `dossier_id` comme clé primaire

Aujourd'hui la dédup se fait par empilement de passes. La dédup devrait être :

1. **Chaque parseur est responsable d'extraire un `dossier_id` canonique** (clé normalisée type `pjlXX-YYY` ou `pplXX-YYY`) — R19 a déjà introduit ça côté AN et Sénat, mais ce n'est pas encore la clé de dédup primaire.
2. **Une seule passe de merge** : pour un `dossier_id` donné, on garde une fiche unique et on y rattache N événements (dépôt, adoption, promulgation, amendements, CR). C'est un modèle documentaire, pas un modèle « liste d'items à plat ».
3. Cette refonte permettrait aussi d'afficher une **fiche de dossier** (procédure législative complète avec jalons), ce qui est l'ambition initiale que la veille énonce mais que l'outil actuel ne rend pas lisiblement.

### 4.5 Persister ce qu'on recalcule

Colonnes supplémentaires à ajouter dans `items` :

- `snippet` (TEXT, nullable) — produit au matching, consommé tel quel à l'export.
- `dossier_id` (TEXT, indexé).
- `canonical_url` (TEXT) — l'URL dossier-législatif quand elle existe, sinon l'URL source.
- `status_label` (TEXT) — pour les dossiers législatifs.
- `content_hash` (TEXT) — pour détecter un « refresh » silencieux de contenu.

Avantage : plus de `_fix_*_row` recalculant à chaque export, moins de points de régression.

### 4.6 Monitoring & self-test quotidien

Le digest quotidien devrait finir par une section « Santé du pipeline » :

- Nb de sources actives / désactivées / en erreur 4xx / en erreur 5xx / à 0 items.
- Écart volumétrique vs moyenne J-7 / J-30 (détection de collapse silencieux).
- Liste des encodings détectés non-UTF-8 (signal faible de dérive).
- Freshness : date max de l'item le plus récent par source.

Si une métrique dépasse un seuil (ex : ≥ 3 sources à 0 items alors qu'elles étaient OK J-1), le workflow GH Actions sort en échec et écrit une issue GitHub. C'est 30 lignes de Python pour couper court à la moitié des incidents futurs.

### 4.7 Séparer données et rendu

Le site Hugo consomme déjà `site/data/*.json`. L'idéal :

- Un **JSON schema** figé pour ces fichiers (versionné). Tout changement de format = bump de version.
- Les templates Hugo consomment le schema, jamais directement des ad-hoc de `_fix_*_row`.
- Le CSS découpé par composant (`components/dosleg-card.css`, `components/agenda-row.css`) importé dans `style.css` — un composant = un fichier qui vit ou meurt ensemble.

### 4.8 Une suite « incidents » en pytest

Pour chaque ticket R-N résolu, un test court qui referme la porte :

- `test_r19a_senat_iso_8859_15_encoding` : nourrit le parser avec un flux factice contenant `œ`, assert que le titre n'est pas corrompu.
- `test_r19b_theme_rss_skip_non_initial_docs` : le handler ne doit retenir que `/leg/pjl|ppl`.
- `test_r19c_question_snippet_no_pa_prefix` : un snippet produit ne commence pas par `Député PAXXXXX —`.
- `test_r19g_cr_snippet_strips_syceron_preamble` : un summary AN commençant par `CRSANR5…` produit un snippet centré sur le keyword, pas sur l'ID.
- `test_r22a_dedup_merges_dossier_ids_into_winner` : 4 items AN+Sénat d'un même dossier, la passe 2c doit en sortir exactement 1 — aucune des passes 2a/2b ne doit perdre le `url_an`.
- `test_r22b_filter_drops_items_from_disabled_source` : un row dont `source_id` est marqué `enabled: false` doit être écarté avant `_fix_*_row` et `_filter_window`.
- `test_r22g_legacy_question_title_rewritten_on_export` : un row persisté au format `"… | Question orale n°83 — PA795136 (LFI-NFP) : M."` est réécrit en `"Question orale : <analyse>"` à l'export, même sans reset DB.
- `test_r22h_questions_strict_dates_no_inserted_at_fallback` : une question sans `published_at` ne passe PAS le filtre fenêtre (même si `inserted_at` est récent), et une question publiée il y a > 90 j est exclue.
- `test_r22e_html_generic_parses_dd_mm_yyyy` : un bloc HTML avec `<time>15/03/2026</time>` produit un `published_at = 2026-03-15`, le flag `fetch_meta` complète un `summary` initialement vide.

Cela transforme l'accumulation « R13 → R19 » en un filet de protection plutôt qu'un cimetière de commentaires.

## 5. Plan de transition en trois vagues

Objectif : passer des rustines à une base propre sans bloquer la veille quotidienne. Chaque vague tient en 1–2 jours de travail et est livrable indépendamment.

**Vague 1 — Filet de sécurité (1 j) — ✅ Réalisée (R29 / R30 / R31, 2026-04-24 nuit)**

- ✅ **Monitoring pipeline (4.6)** → livré R29 via `src/monitoring.py` + alertes `ERR_PERSIST` / `FORMAT_DRIFT` / `FEED_STALE` + bloc HTML dans le digest + persistance `data/pipeline_health.json`. R34 a ajouté le volumétrie ring buffer + `VOLUMETRY_COLLAPSE` + exit code CI opt-in.
- ✅ **Suite de tests « incidents » rétroactive (4.8)** → livré R30 via `tests/test_regressions_r13_r28.py` (20 tests ciblés sur R19-A/B/C, R19-G/R23-F, R22e-1, R22g, R22h ; les régressions déjà protégées par un fichier dédié ne sont pas redupliquées).
- ✅ **Contrat de source auto-généré (4.3)** → livré R31 via `tests/test_contracts_sources.py` (validation invariants Item : source_id, uid, category, chamber, title, url, published_at naïf UTC, raw dict + garde-fou automatique sur toute nouvelle chamber via grep `src/sources/*.py`).

**Vague 2 — Normalisation du cœur (2 j) — ✅ Partiellement réalisée (R32 / R33, 2026-04-24 nuit)**

- ✅ **Extraire `textclean.py` (4.2)** → livré R32. Module `src/textclean.py` (4 primitives : `strip_html`, `decode_bytes`, `strip_technical_prefix`, `smart_truncate`). `keywords._clean_html`, `senat._strip_html`, `senat_amendements._strip_html` délèguent désormais à la primitive centrale. +39 tests `tests/test_textclean.py`.
- ✅ **Persister `snippet`, `dossier_id`, `canonical_url`, `status_label`, `content_hash` en DB (4.5)** → livré R33. Migration idempotente `migrate_items(conn)` + `ALTER TABLE ADD COLUMN` + index `idx_items_dossier_id`. `upsert_many` persiste les 5 colonnes avec règles COALESCE pour ne jamais écraser un champ déjà renseigné par un parseur appauvri. +19 tests `tests/test_r33_persist_columns.py`. Rétrocompatibilité : les exports lisent encore depuis `raw.*` en fallback — cutover progressif, pas big-bang.
- ⏳ **Éclater `site_export.py` en `load.py` / `dedup.py` / `export.py` (4.1)** → non fait. `site_export.py` reste un gros fichier (~1900 lignes) mais les colonnes persistées en R33 réduisent la charge des `_fix_*_row`. À programmer hors-nuit avec vérif visuelle du site après refactor.

**Vague 3 — Modèle documentaire (2 j) — ⏳ Volontairement différée**

- Refondre la dédup dossier autour de `dossier_id` comme clé primaire (4.4). L'index R33 `idx_items_dossier_id` est posé, la plomberie est prête — reste le refactor des 3 passes de dédup.
- Fiche de dossier = document + événements rattachés, rendue via un nouveau template Hugo.
- JSON schema versionné (4.7) + CSS découpé par composant.

*Rationale différé* : la vague 3 nécessite une vérification visuelle du site post-refactor (nouveau template Hugo, JSON schema), à programmer hors-nuit avec Cyril. Les vagues 1 et 2 ont pu être livrées sans supervision car purement techniques (monitoring, tests, refactor module utilitaire, migration DB non destructive).

À l'issue de R33, `R-N` est passé du statut « marqueur de patch dans un commentaire » à celui de « release documentée avec tests de régression dédiés » — l'ambition du §5 est en bonne voie, plus que la vague 3 à franchir.

## 5bis. Addendum R35 (2026-04-24 matin) — correctifs bruit prod

Après les 5 vagues d'audit (R29 → R34) bouclées dans la nuit, 5 correctifs ciblés sur le bruit observé en prod R28-stable :

- **R35-A** — JORF matche aussi sur le corps des textes via les fichiers `<ARTICLE>` référencés par `cid` dans `<TEXTE>`. Résout `JORFTEXT000053930076` (titre générique, « pratique sportive » dans l'article 2).
- **R35-B** — Nouveau connecteur `src/sources/an_cr_commissions.py` : scrape les CR des commissions AN (HTML + PDF via `pypdf`) pour alimenter `haystack_body` avec le contenu texte complet, pas seulement le titre.
- **R35-C** — Retrait de `cnosf` / `france_paralympique` / `fdsf` du `BYPASS_KEYWORDS_SOURCES`. Les articles institutionnels de ces sources citent presque toujours des keywords explicites ; le bypass générique injectait surtout du bruit (événements locaux, JO étrangers, communication non-française). Set final : `{ans, insep, injep, afld, min_sports_actualites, min_sports_presse}`.
- **R35-D** — Retrait de `PO420120` (Affaires sociales AN) et `PO211493` (Affaires sociales Sénat) du `SPORT_RELEVANT_ORGANES`. Ces commissions généraient >90 % de bruit off-topic (retraites, assurance maladie, droit du travail). Les rares réunions sport remontent encore via match keyword.
- **R35-E** — Agenda Sénat réactivé via un chemin officiel robuste. `senat_agenda` reste bloqué WAF, mais la page `/travaux-parlementaires/commissions/{slug}/agenda-de-la-commission.html` répond HTTP 200 avec un bloc TYPO3 structuré. Nouveau connecteur `src/sources/senat_commission_agenda.py` + format yaml `senat_commission_agenda_html`. Une seule source active à ce stade (commission culture/éducation/communication/sport, PO211490) — élargissement possible par commission individuelle.

Résultat : 551 → 586 tests. Le label `SYSTEM_VERSION_LABEL` passe R34 → R35.

## 6. Ce que je garderais tel quel

- `sources.yml` comme format de déclaration (il est lisible et git-friendly).
- Matcher mots-clés actuel — il est correct, il fera juste 3 kg de moins une fois `textclean` extrait.
- SQLite comme store (versionnable, reproductible, idéal pour cette volumétrie).
- GitHub Actions comme scheduler + l'auto-déploiement Pages derrière.
- Le principe « un fichier par source » côté handlers — modulaire, supportable par deux personnes.

## 7. Ce qui, à terme, mérite un chantier dédié

- **AAI & juridictions — scope arbitré R22 (2026-04-23).** La couverture était hétérogène : ARCOM / ANJ / AFLD / Défenseur des droits / Conseil d'État / Conseil constitutionnel actifs ; Cour des comptes en timeout chronique ; Cour de cassation derrière un anti-bot JS ; AMF / CNIL / HATVP / CADA / HCERES jamais branchées. Arbitrages Cyril :
  - **Cour des comptes** → réactivée en R22 via le flux RSS officiel `ccomptes.fr/rss/publications` (remplace le scraping HTML `/fr/publications` qui timeoutait 3×60 s par run).
  - **Autorité de la concurrence** → ajoutée en R22 par scraping HTML `/fr/communiques-de-presse` (pas de RSS officiel ; 2 datasets data.gouv.fr existent mais orientés archive, pas actualité).
  - **Cour de cassation** → sortie définitivement du scope (site JS-only insurmontable en HTTP pur, décisions de principe captées au JORF).
  - **AMF** → sortie du scope (hors cœur sport).
  - **CNIL / HATVP / CADA / HCERES** → reportées. Ne seront ajoutées que si une actualité passe à travers les mailles et le justifie.
- **Qualification « sport » — le choix des mots plutôt qu'une deuxième passe.** Le matcher reste volontairement lexical. Plutôt qu'une passe ML zero-shot sur les items borderline (risques : faux positifs d'analogie, coût en CI, ajout d'une dépendance lourde), l'axe retenu est d'**améliorer le lexique mots-clés** quand un angle mort apparaît : ajouter un terme métier, une variante, un sigle, un nom d'équipement ou d'événement. Plus léger, plus déterministe, plus lisible dans les diffs.
- **Alpes 2030** — ~~chantier dédié~~ *(retiré R22)*. Le site officiel COJOP n'est pas encore en ligne ; on attend sa publication plutôt que de bricoler du Playwright sur olympics.com. Décision documentée : pas de source Alpes 2030 dans la veille tant que le canal officiel n'existe pas.

---

**En un mot** : l'outil est plus mûr que son code ne le laisse penser. Le modèle métier est bon, les rustines ont été utiles pour apprendre où ça casse. Il est maintenant temps de transformer les cicatrices en tests, de dé-monolithiser `site_export`, et de détecter activement les sources qui tombent. Trois vagues de deux jours suffisent pour que R19 soit la dernière révision où le numéro est dans un commentaire de code plutôt que dans un changelog.
