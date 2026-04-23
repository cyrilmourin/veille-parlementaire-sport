---
title: Rollback — retour à R28
last_updated: 2026-04-24 (nuit, après série R29 → R33 audit)
---

# Rollback — retour à R28-stable

Ce document explique comment revenir à l'état R28 de prod si une release
ultérieure (R29, R30, R31, R32, R33) casse quelque chose de visible.

Le tag git `R28-stable` pointe sur le commit `bfc5289` — la dernière
version « R25b + R26 + R27 + R28 » poussée en prod le 2026-04-24 avant
la série audit.

## Scénarios et procédures

### 1. Rollback d'une seule release récente (le plus fréquent)

Tu viens de pusher Rxx et tu vois que quelque chose est cassé (site qui
ne rend plus, job GHA qui sort KO, digest vide, …). Tu veux revenir
**uniquement** sur cette release sans perdre les précédentes.

```
cd "/Users/cyrilmourin/Documents/Claude/Projects/Veille Parlementaire/veille-parlementaire-sport"
git revert HEAD --no-edit
git push origin main
```

`git revert` crée un commit inverse qui annule la dernière release sans
réécrire l'historique. Propre, atomique, visible dans `git log`.

### 2. Rollback de toute la série audit R29 → R33

Si tu veux **tout annuler d'un coup** et revenir à l'état R28 sur la
prod (rare : toute la série qui casse ensemble), c'est un reset dur
suivi d'un force-push :

```
cd "/Users/cyrilmourin/Documents/Claude/Projects/Veille Parlementaire/veille-parlementaire-sport"
git fetch origin
git reset --hard R28-stable
git push --force-with-lease origin main
```

`--force-with-lease` refuse le push si quelqu'un d'autre a poussé depuis
— filet de sécurité en cas d'activité concurrente (théoriquement nulle
sur ce repo mono-user, mais l'habitude se prend).

Après le force-push, le workflow GHA redéploie le site en R28. Compter
2-5 min pour voir le label header revenir à `R28 · <sha>`.

### 3. Rollback local uniquement (avant push)

Si tu découvres le problème avant de pusher et que les commits sont
uniquement locaux :

```
cd "/Users/cyrilmourin/Documents/Claude/Projects/Veille Parlementaire/veille-parlementaire-sport"
git log --oneline R28-stable..HEAD
git reset --hard R28-stable
```

Les fichiers retournent à l'état R28 dans le working tree. Aucun
impact prod puisque rien n'a été pushed.

### 4. Identifier qui a cassé quoi

Tous les commits de la série audit sont prefixés de leur numéro Rxx.
Pour voir ce qu'ajoute chaque release :

```
cd "/Users/cyrilmourin/Documents/Claude/Projects/Veille Parlementaire/veille-parlementaire-sport"
git log --oneline R28-stable..HEAD
git show <sha> --stat
git show <sha>
```

## Contenu de la série R29 → R33

Rappel de ce que chaque release fait (cf. HANDOFF.md pour le détail) :

- **R29** — `src/monitoring.py` + bloc « Santé du pipeline » dans le
  digest. Détecte sources cassées (4xx/5xx persistant, format drift,
  feed figé). Persiste l'état dans `data/pipeline_health.json` (fichier
  versionné, comme `ping_state.json`). **Sans effet sur le rendu du site**,
  modifie uniquement le contenu du mail quotidien.
- **R30** — `tests/regressions/` : 12 tests rétroactifs sur les bugs
  R13 → R28. **Aucun impact runtime**, uniquement de la CI.
- **R31** — `tests/contracts/` : shape-tests auto-générés par source
  active. **Aucun impact runtime**, uniquement de la CI.
- **R32** — `src/textclean.py` : centralisation du décodage bytes,
  strip HTML, strip bruit technique. Migration non-breaking : les
  connecteurs continuent d'utiliser leurs helpers locaux, textclean
  expose des fonctions dispo pour les futurs parsers. **Impact
  runtime minime** (pas de chemin hot critique modifié).
- **R33** — Colonnes DB `snippet`, `dossier_id`, `canonical_url`,
  `status_label`, `content_hash` (nullable, migration idempotente).
  `site_export` lit ces colonnes **prioritairement** mais garde les
  `_fix_*_row` en safety net si la colonne est NULL. **Régression
  possible si la migration foire** — c'est la release la plus risquée
  de la série, candidate au rollback en premier si les titres/snippets
  se corrompent en prod.

## Arbre de décision rapide

- Site affiche toujours un truc mais ça semble bizarre → vérifier le
  label header. Si R33, tenter revert de R33 seul (scénario 1).
- Site vide ou 500 → rollback total (scénario 2).
- Digest email reçu avec une section « Santé pipeline » pleine d'alertes
  bruit → problème de seuils dans R29, tenter revert de R29 seul.
- Tests CI rouges mais prod fonctionne → c'est R30 ou R31, corriger la
  fixture ou skip le test défaillant. Pas besoin de rollback.

## Vague 3 — explicitement NON livrée

La refonte dédup avec `dossier_id` comme clé primaire + fiche de dossier
+ JSON schema versionné + CSS découpé par composant (§4.4, §4.7 audit)
est **reportée**. Raison : chantier d'une semaine qui touche au template
Hugo, risque de casser le rendu du site sans possibilité de vérifier
visuellement avant le réveil de Cyril. À reprendre en session diurne
sur plusieurs itérations.
