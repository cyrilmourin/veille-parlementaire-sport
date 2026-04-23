# Audit global — Veille Parlementaire Sport (R19 → R22b)

*Rédigé le 2026-04-23 après R19 déployé. Mis à jour le 2026-04-23 avec R20 (IGESR + INJEP RSS + CC en communiqués), R21 (UX mobile + titre), R21b (script run_clean.sh), R22 (arbitrages AAI / juridictions : Cour des comptes réactivée en RSS, Autorité de la concurrence ajoutée, Cour de cassation / AMF / CNIL / HATVP / CADA / HCERES sortis du scope), R22a (passe dédup dosleg durcie : cumul `_merged_dossier_ids` dans le winner des passes 2a/2b pour que la passe 2c `dossier_id` voie encore le bridge AN↔Sénat — corrige le cas JOP Alpes 2030 où senat_promulguees écrasait le senat_akn_* porteur du `url_an`) et R22b (filtre `_filter_disabled_sources` au chargement de l'export + bump `SYSTEM_VERSION_LABEL` hardcodé R19 → R22a qui était resté figé depuis R13-G). Aucune refonte structurelle n'accompagne encore ce document : c'est un texte d'orientation pour sortir de la dynamique « rustines » et rendre l'outil durable.*

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

- **`site_export.py` fait tout** : charger, filtrer par fenêtre, dédupliquer par dossier, nettoyer les titres, reconstruire les snippets, écrire JSON + Markdown Hugo. ~1700 lignes, plus de 10 fonctions `_fix_*_row`. C'est le point où la majorité des régressions apparaissent.
- **Chaque incident produit une rustine balisée `R13-A`, `R13-B`, …, `R18`, `R19-A..H`, `R22a`, `R22b`** — une trentaine à ce jour. Les commentaires datés rendent l'historique lisible mais la logique métier finit dispersée dans des `re.sub`, des tests `source_id == …` et des conditions par catégorie. Exemple R22b : `SYSTEM_VERSION_LABEL = "R19"` hardcodé en ligne 28 de `site_export.py` est resté figé depuis R13-G alors que 10 tags se sont empilés au-dessus — un simple `version.py` lu en CI suffirait à couper ce vecteur.
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

Cela transforme l'accumulation « R13 → R19 » en un filet de protection plutôt qu'un cimetière de commentaires.

## 5. Plan de transition en trois vagues

Objectif : passer des rustines à une base propre sans bloquer la veille quotidienne. Chaque vague tient en 1–2 jours de travail et est livrable indépendamment.

**Vague 1 — Filet de sécurité (1 j)**

- Monitoring pipeline (4.6).
- Suite de tests « incidents » rétroactive sur les bugs R13–R19 (4.8).
- Contrat de source auto-généré : pour chaque source active, un test « ≥ 1 item / 30 j ».

**Vague 2 — Normalisation du cœur (2 j)**

- Extraire `textclean.py` (4.2) et y déplacer `_clean_html`, décodage bytes, strip bruit technique.
- Persister `snippet`, `dossier_id`, `canonical_url` en DB (4.5), migration douce.
- Éclater `site_export.py` en `load.py` / `dedup.py` / `export.py` (4.1).

**Vague 3 — Modèle documentaire (2 j)**

- Refondre la dédup dossier autour de `dossier_id` comme clé primaire (4.4).
- Fiche de dossier = document + événements rattachés, rendue via un nouveau template Hugo.
- JSON schema versionné (4.7) + CSS découpé par composant.

À l'issue, `R-N` devient un vrai numéro de release sémantique et non plus un marqueur de patch dispersé dans le code.

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
