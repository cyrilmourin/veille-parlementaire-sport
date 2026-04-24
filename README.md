# Veille parlementaire sport — Sideline Conseil

Outil de veille institutionnelle automatisé dédié au secteur sport :
- agrège les publications officielles (Parlement, Élysée, Matignon, ministères, JORF, autorités, instances sportives) ;
- filtre sur une liste de mots-clés sport dédiée (`config/keywords.yml`) ;
- envoie un email quotidien à 06:30 (Europe/Paris) et publie un site statique sur `https://veille.sideline-conseil.fr`.

Exclusivement des **sources officielles publiques** — aucune collecte de réseaux sociaux.

## 1. Architecture

```
veille-parlementaire-sport/
├── config/
│   ├── sources.yml       # inventaire des sources (AN, Sénat, PISTE, exécutif, autorités)
│   └── keywords.yml      # liste de mots-clés (acteur, federation, dispositif, evenement, theme)
├── src/
│   ├── main.py           # orchestration (run / dry)
│   ├── normalize.py      # dispatcher vers connecteurs
│   ├── keywords.py       # matcher regex + normalisation accents
│   ├── store.py          # SQLite + dédup
│   ├── digest.py         # email HTML
│   ├── site_export.py    # génération JSON + Markdown pour Hugo
│   ├── models.py         # Item pivot (pydantic v2)
│   └── sources/
│       ├── assemblee.py               # zips JSON open data AN
│       ├── assemblee_rapports.py      # rapports AN (HTML listing, R28)
│       ├── an_cr_commissions.py       # CR commissions AN (HTML + PDF pypdf, R35-B)
│       ├── senat.py                   # CSV/ZIP/RSS data.senat.fr
│       ├── senat_amendements.py       # amendements Sénat per-texte
│       ├── senat_commission_agenda.py # agenda commissions Sénat (page HTML officielle, R35-E)
│       ├── elysee.py                  # sitemap.static.xml
│       ├── dila_jorf.py               # JORF DILA (NOTICE + articles CID, R26/R35-A)
│       ├── piste.py                   # Légifrance / JORF via API PISTE (OAuth2, optionnel)
│       └── html_generic.py            # scraping ministères + autorités
├── site/                 # site Hugo (layouts, content, data, static)
├── scripts/
│   ├── audit_sources.py  # ping HEAD toutes les sources
│   └── backfill.py       # premier run sur 7 jours
├── tests/                # pytest
├── .github/workflows/daily.yml  # cron GitHub Actions
└── pyproject.toml
```

## 2. Catégories Follaw.sv

Les 9 catégories retenues : Dossiers législatifs, JORF, Amendements, Questions,
Comptes-rendus, Publications, Nominations, Agenda, Communiqués.

## 3. Mise en production — checklist

### 3.1. Créer le dépôt GitHub

1. Crée un repo privé `sideline-conseil/veille-parlementaire-sport`.
2. Depuis ce dossier local :
   ```bash
   cd "veille-parlementaire-sport"
   git init && git add -A && git commit -m "init: veille parlementaire sport"
   git branch -M main
   git remote add origin git@github.com:sideline-conseil/veille-parlementaire-sport.git
   git push -u origin main
   ```

### 3.2. Journal officiel (JORF)

**Aucune action requise.** Le pipeline récupère le JORF via le dump DILA OPENDATA
public (`https://echanges.dila.gouv.fr/OPENDATA/JORF/`), qui ne demande aucun credential.

Si tu souhaites doubler la source via l'API PISTE (Légifrance) plus tard : crée un
compte sur https://piste.gouv.fr, demande l'accès à Légifrance via Démarches
Simplifiées, récupère `client_id` / `client_secret` et ajoute-les en secrets
GitHub `PISTE_CLIENT_ID` / `PISTE_CLIENT_SECRET`. Décommente les 2 lignes
correspondantes dans `.github/workflows/daily.yml`. Le connecteur est prêt.

### 3.3. SMTP (OVH Pro ou autre)

Configure un compte `veille@sideline-conseil.fr` côté OVH (ou tout autre SMTP
authentifié TLS sur le port 587).

### 3.4. DNS — sous-domaine veille.sideline-conseil.fr

Chez ton registrar, crée un enregistrement CNAME :
```
veille.sideline-conseil.fr  →  sideline-conseil.github.io
```
Le fichier `site/static/CNAME` est déjà présent.

### 3.5. Secrets GitHub Actions

Dans `Settings ▸ Secrets and variables ▸ Actions`, crée les 6 secrets suivants :

| Secret              | Valeur                                                  |
|---------------------|---------------------------------------------------------|
| `SMTP_HOST`         | ex. `ssl0.ovh.net`                                      |
| `SMTP_PORT`         | `587`                                                   |
| `SMTP_USER`         | `veille@sideline-conseil.fr`                            |
| `SMTP_PASS`         | mot de passe SMTP                                       |
| `SMTP_FROM`         | `Sideline Veille <veille@sideline-conseil.fr>`          |
| `DIGEST_TO`         | `cyrilmourin@sideline-conseil.fr`                       |

Optionnels (seulement si tu actives PISTE) : `PISTE_CLIENT_ID`, `PISTE_CLIENT_SECRET`.

### 3.6. Activer GitHub Pages

`Settings ▸ Pages ▸ Source = GitHub Actions`.

### 3.7. Premier run — backfill 7 jours

Deux options :
- **En local** :
  ```bash
  pip install -e .
  export PISTE_CLIENT_ID=... PISTE_CLIENT_SECRET=...
  export SMTP_HOST=... SMTP_PORT=587 SMTP_USER=... SMTP_PASS=...
  export DIGEST_TO=cyrilmourin@sideline-conseil.fr
  python scripts/backfill.py
  ```
- **Via GitHub Actions** : onglet `Actions ▸ Veille parlementaire sport — daily ▸ Run workflow`, saisir `since_days=7`.

## 4. Utilisation quotidienne

- Email à 06:30 dans ta boîte.
- Site consultable : https://veille.sideline-conseil.fr.
- Replay manuel possible à tout moment via `Actions ▸ Run workflow`.
- Historique SQLite commité à chaque run (`data/veille.sqlite3`) — utilisable localement pour des requêtes ad hoc.

## 5. Maintenance

- **Audit mensuel des sources** : `python scripts/audit_sources.py`. Signale les 404 / changements d'URL.
- **Ajout d'un mot-clé** : éditer `config/keywords.yml` (aucun redéploiement de code nécessaire).
- **Ajout d'une source HTML** : ajouter une entrée dans `config/sources.yml` avec `format: html` — le connecteur générique se charge du reste.
- **Ajout d'une source non standard** : créer un nouveau module dans `src/sources/` et l'enregistrer dans `ROUTER` de `src/normalize.py`.
- **Faux positifs / négatifs** : affiner les termes dans `config/keywords.yml` (suppression d'un terme trop générique ; ajout d'un bigramme plus ciblé).

## 6. Extensions possibles (roadmap)

- Élargir la couverture agenda commissions Sénat — R35-E a activé Commission culture (PO211490) ; ajouter Commission des lois, Commission des finances, Commission des affaires étrangères en 1 ligne yaml chacune (cf. HANDOFF §TODO priorité haute).
- Alerte temps réel sur les nominations sport au JORF (webhook).
- Digest hebdomadaire thématique (Alpes 2030, Pass'Sport, intégrité).
- API JSON publique sur le site pour intégration dans un autre outil Sideline.
- Ajout d'un filtre "chambre" (AN / Sénat / Élysée…) dans l'email.
- Vague 3 audit (cf. `docs/AUDIT_R19.md` §5) — refonte dédup autour de `dossier_id` comme clé primaire + fiche de dossier (document + événements rattachés) + JSON schema versionné.

## 7. Développement

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
python -m src.main dry -v          # fetch + match, sans écriture
python -m src.main run --since 7 --no-email -v
```

## 8. Conformité

- **Sources** : uniquement des portails publics officiels (data.assemblee-nationale.fr,
  data.senat.fr, Légifrance via API PISTE, sites `.gouv.fr`, agencedusport.fr, afld.fr,
  franceolympique.com, france-paralympique.fr, cojop.fr, ccomptes.fr, anj.fr, arcom.fr,
  defenseurdesdroits.fr).
- **User-Agent déclaratif** : `SidelineVeilleBot/0.1 (+https://veille.sideline-conseil.fr)`.
- **Politesse réseau** : backoff exponentiel (tenacity), parallélisme limité à 6.
- **Aucune donnée personnelle collectée** — l'outil ne publie que des contenus déjà publics.

## 9. Contacts

- Éditeur : **Sideline Conseil** — cyrilmourin@sideline-conseil.fr
- Support technique : via le dépôt GitHub (issues).
