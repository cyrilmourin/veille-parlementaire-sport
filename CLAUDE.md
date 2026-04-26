# CLAUDE.md — briefing Claude Code

Ce fichier est lu automatiquement par Claude Code à chaque session ouverte
dans ce repo. Il sert de point d'entrée pour reprendre le projet rapidement.
La doc complète vit dans **`HANDOFF.md`** — lis-la systématiquement avant
toute intervention non triviale.

---

## Identité du projet

**Veille Parlementaire Sport** — pipeline Python qui scrape l'open data du
Parlement (AN, Sénat) et les contenus officiels de l'État (JORF, Élysée,
ministères, AAI, juridictions, opérateurs publics sport, mouvement
sportif), filtre sur un dictionnaire de mots-clés sport et publie un site
statique Hugo (`https://veille.sideline-conseil.fr`) + un email quotidien.

Maintainer : Cyril Mourin (`cyrilmourin@gmail.com`).

---

## Lis ces fichiers en premier

1. **`HANDOFF.md`** — état actuel, décisions clés, TODO, pièges connus,
   historique R-tag par R-tag. Source de vérité unique sur le projet.
2. `config/sources.yml` — sources actives / disabled.
3. `config/keywords.yml` — dictionnaire de matching.
4. `config/blocklist.yml` — liste rouge d'items à exclure manuellement (R39-O).
5. `src/main.py` — point d'entrée du pipeline (`run`, `dry`, `ping`).

---

## Préférences utilisateur (Cyril)

- **Toujours répondre en français.** Ne pas basculer en anglais sauf demande
  explicite, même si le code et les commentaires sont mixtes.
- **Pas de commentaires `#` dans les blocs shell partagés.** Le zsh de Cyril
  n'a pas `INTERACTIVE_COMMENTS` activé → les commentaires cassent le
  copier-coller. Mettre les commentaires AVANT le bloc, pas dedans.
- **Doc officielle avant tâtonnement.** Toujours viser le XSD AN, le schema
  Sénat, ou un parser communautaire reconnu avant de scripter du diag.
- **Procédure législative comme prérequis** — maîtriser `dépôt → commission
  → séance → adoption → CC → promulgation` avant de patcher tri/filtre des
  dossiers.
- **PAT GitHub par repo, jamais croisés Sport/Lidl.** Le PAT fine-grained de
  ce repo est déjà embarqué dans `.git/config` côté `[remote "origin"]` (URL
  `https://x-access-token:<PAT>@github.com/cyrilmourin/veille-parlementaire-sport.git`).
  → `git push origin main` fonctionne directement, pas besoin de demander un
  `.command` ou de pousser via une autre machine. Ne jamais réutiliser ce
  PAT pour le repo Lidl (`veille-parlementaire-lidl`).
- **Pas de patch manuel sans cause racine identifiée.** Si un item manque,
  ne pas se contenter de le forcer en DB — comprendre pourquoi le pipeline
  l'a écarté, fixer la cause structurelle, ajouter un test.

---

## Workflow standard (convention R-N)

Chaque mini-livraison s'inscrit dans un tag `R<n>` ou `R<n>-<lettre>` :

1. **Branche unique `main`.** Pas de feature branches sur ce repo (mono-dev,
   livraisons quotidiennes).
2. **Commits atomiques par R-tag.** Un sujet = un R-tag = un commit. Le
   message commit commence par `feat(R<tag>):` / `fix(R<tag>):` /
   `doc(R<tag>):` / `refactor(R<tag>):`.
3. **Bump `SYSTEM_VERSION_LABEL`** (ligne 28 de `src/site_export.py`) quand
   un cycle Rxx complet est livré. Format : `R<numéro>` (ex. `R39`,
   `R40`). Le label apparaît en bas de chaque page du site.
4. **Tests obligatoires.** Avant push, `python -m pytest -q` doit passer.
   Toute fix d'incident structurel doit être couvert par un test de
   non-régression dans `tests/test_r<tag>_<sujet>.py`.
5. **Mise à jour HANDOFF.** Toute livraison non triviale ajoute une entrée
   dans la section Historique de `HANDOFF.md` (date + résumé R-tag).
6. **Push direct vers `origin main`.** Le push déclenche un run automatique
   du workflow GHA `daily.yml` (pipeline + déploiement Pages). Site re-bâti
   en ~3-5 min.

---

## Commandes utiles

```
source .venv/bin/activate                  # active venv Python
python -m pytest -q                        # 621+ tests verts attendus
python -m src.main run --since 7 --no-email -v   # pipeline complet sans mail
python -m src.main dry -v                  # fetch + match, pas d'écriture DB
python scripts/reset_category.py amendements --yes   # purge ciblée DB + state files (R39-M)
```

Pour déclencher un run GHA à la demande (ex. après modif keywords) :

```
gh workflow run daily.yml -f since_days=7
gh workflow run daily.yml -f reset_category=comptes_rendus
```

Ou via l'API REST (sans `gh` CLI) en utilisant le PAT du remote — voir le
pattern dans le HANDOFF / l'historique R39-M.

---

## Outils Claude Code à utiliser pour ce projet

Outils natifs Claude Code suffisent (pas de MCP externes nécessaires) :

- **Read / Write / Edit** pour les fichiers du repo
- **Bash** pour pytest, git, scripts/reset_category.py, etc.
- **Glob / Grep** pour naviguer dans la base de code
- **WebFetch** pour vérifier des URLs source (open data AN/Sénat,
  pages prod) — Cyril privilégie la lecture de sources canoniques avant
  de scripter du diag
- **WebSearch** pour les schémas / XSD officiels et les changements de
  CMS ministériels
- **TodoWrite** pour suivre les tâches d'un cycle R-tag complexe (ex.
  R36-A → R36-P avec 16 sous-items)
- **Task / sub-agents** pour les recherches multi-étapes dans le repo
  (ex. retrouver tous les call-sites d'une fonction sur un gros refactor)

---

## Pièges courants à éviter

(extrait — lire `HANDOFF.md > Pièges connus` pour la liste complète)

- **`reset_category` doit purger DB + state files.** Patché en R39-M : la
  purge supprime aussi `data/an_cr_state.json`. Si un nouveau scraper
  incrémental est ajouté, étendre `_purge_incremental_state` dans
  `scripts/reset_category.py`.
- **Bypass organe R27 désactivé depuis R39-K.** Ne pas le réactiver sans
  arbitrage explicite avec Cyril (« je veux pas garder des CR sans mots
  qui taggent »). La whitelist `SPORT_RELEVANT_ORGANES` reste en place
  pour ré-activation rapide si le choix change.
- **Faux positif keyword.** Première intention : `config/blocklist.yml`
  (R39-O), pas `config/keywords.yml`. Cf. décision dans HANDOFF.
- **`upsert_many` ne refresh pas `raw.*` à hash_key constant.** Si un patch
  parser change la structure du `raw`, lancer `scripts/reset_category.py`
  pour ré-ingérer, sinon les items legacy gardent l'ancien shape.
- **AN URLs `/dyn/17/` cassent à la bascule de législature.** Cf.
  HANDOFF > Pièges connus > « Bascule de législature AN » pour la
  checklist sed à appliquer le jour J.

---

## Quand ouvrir une session Claude Code ici

Premier message type pour reprendre le projet :

> Lis `HANDOFF.md` pour le contexte complet et liste-moi les TODO ouverts.
> Ensuite je te donne la prochaine tâche.

Ou directement :

> Patch R<NN>-<lettre> : <description>. Lis HANDOFF.md, fais le patch +
> tests + commit + push, bump SYSTEM_VERSION_LABEL si fin de cycle.

Le PAT GitHub étant déjà dans `.git/config`, le push fonctionne sans
intervention manuelle.
