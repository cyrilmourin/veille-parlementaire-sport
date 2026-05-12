# R42-BO — Setup du proxy Cloudflare Workers

## Pourquoi ce proxy

Certains serveurs officiels bloquent durablement les IPs GitHub Actions (plage Azure US) au niveau WAF/firewall. Même avec `curl_cffi` + Chrome 120 TLS impersonate, les requêtes sont rejetées (HTTP 403, 418, ConnectTimeout). Vérifié sur :

- `www.info.gouv.fr` (Matignon + gouv transversal)
- `www.education.gouv.fr` (Min Éducation)
- `www.interieur.gouv.fr` (Min Intérieur)
- `www.sports.gouv.fr/rapports-de-l-igesr-...` (rapports IGESR sport)
- `www.insep.fr/fr/actualites.xml` (INSEP RSS)
- `injep.fr/sport/les-publications-sport/` (INJEP publications sport)
- `www.ccomptes.fr/rss/publications` (Cour des comptes)

Les mêmes URLs répondent HTTP 200 depuis n'importe quelle autre IP (poste local, Cloudflare, etc.). Solution : faire transiter ces requêtes par un Cloudflare Worker (plan Free → 100 000 req/jour, largement suffisant pour ~30 req/jour de la veille).

## Étape 1 — Compte Cloudflare

1. Va sur **https://dash.cloudflare.com/** — si tu n'as pas de compte, en créer un (gratuit, sans CB).
2. Une fois connecté, dans le menu de gauche : **Workers & Pages**.

## Étape 2 — Créer le worker

1. Clique **« Create »** (en haut à droite).
2. Choisis **« Create Worker »** (pas Pages).
3. Donne un nom : par exemple `veille-proxy`. L'URL finale sera `https://veille-proxy.<ton-pseudo>.workers.dev`.
4. Clique **« Deploy »** (un Hello World est déployé par défaut).
5. Une fois le worker créé, clique sur **« Edit code »** pour ouvrir l'éditeur.
6. **Supprime tout le code par défaut** et **colle le contenu de `scripts/cloudflare_worker.js`** (intégralement).
7. Clique **« Deploy »** en haut à droite.

## Étape 3 — Générer un token secret

Le worker exige un header `X-Proxy-Token` pour fonctionner (sans token, retour 403 — évite qu'un tiers utilise ton worker comme proxy ouvert).

1. Génère un token aléatoire fort :
   ```bash
   openssl rand -hex 32
   # exemple : 9f3a2b7e6c5d4e1f8a0b3c2d5e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f
   ```
2. Dans le dashboard du worker (`veille-proxy`), va dans **Settings** → **Variables and Secrets**.
3. Clique **« Add »** :
   - **Type** : Secret (pas Variable — le secret est chiffré)
   - **Variable name** : `PROXY_TOKEN`
   - **Value** : colle le token généré
4. Save.

## Étape 4 — Récupérer l'URL publique du worker

Dans la page d'accueil du worker, l'URL ressemble à :
```
https://veille-proxy.tonpseudo.workers.dev
```

## Étape 5 — Poser les secrets côté GitHub Actions

Depuis ta machine, dans le repo :

```bash
gh secret set CLOUDFLARE_PROXY_URL --body "https://veille-proxy.tonpseudo.workers.dev"
gh secret set CLOUDFLARE_PROXY_TOKEN --body "9f3a2b7e6c5d4e1f8a0b3c2d5e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f"
```

Adapte URL + token aux valeurs réelles.

## Étape 6 — Activer côté pipeline

C'est déjà fait dans le repo (R42-BO) :
- Les 7 sources concernées ont `proxy: cloudflare` dans `config/sources.yml`
- `daily.yml` expose les 2 secrets en env vars au step pipeline
- `src/sources/_common.py::fetch_text(via_proxy=True)` route via le worker si les env vars sont définies, sinon fallback fetch direct

## Étape 7 — Tester

Au prochain daily run (cron 06:28 / 15:30 ou push trigger), les logs doivent montrer :

```
INFO src.sources._common — GET (via CF proxy) https://www.info.gouv.fr/rss
INFO src.sources.html_generic — info_gouv_actualites : N items RSS
```

Si le worker est mal configuré (token absent, hostname pas dans la whitelist…), le step retournera HTTP 403 ou 400 et le scraper passera proprement en `RSS KO`.

Tu peux aussi tester depuis ta machine :
```bash
curl -H "X-Proxy-Token: <ton-token>" \
  "https://veille-proxy.tonpseudo.workers.dev/?url=https%3A%2F%2Fwww.info.gouv.fr%2Frss"
# Doit retourner du XML RSS
```

## Étape 8 (optionnel) — Ajouter de nouveaux hosts plus tard

Si une nouvelle source à scraper se trouve elle aussi WAF-bloquée :
1. Dans le code du worker (dashboard Cloudflare), ajoute l'hostname à `ALLOWED_HOSTS`.
2. Save & Deploy.
3. Dans `config/sources.yml`, ajoute `proxy: cloudflare` à la source concernée.

## Quota / monitoring

Cloudflare Workers Free :
- 100 000 requêtes/jour (réinitialisé à minuit UTC)
- 10 ms CPU/requête (largement suffisant pour un proxy HTTP simple)
- Pas de limite sur la bande passante

Estimation usage : ~30 req/jour (7 sources × ~4 runs/jour). Largement sous quota.

Pour suivre : dashboard Cloudflare → Workers & Pages → veille-proxy → **Metrics**.

## Sécurité

- ✅ Token obligatoire (header `X-Proxy-Token`)
- ✅ Whitelist d'hostnames stricte (~10 hosts officiels)
- ✅ Méthode GET uniquement (lecture seule)
- ✅ Pas de logs de requêtes côté Cloudflare (worker stateless)

Si tu soupçonnes une fuite du token : régénère un nouveau token et update `PROXY_TOKEN` côté worker + `CLOUDFLARE_PROXY_TOKEN` côté GHA. L'ancien deviendra immédiatement caduc.
