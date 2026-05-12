// R42-BO (2026-05-12) — Cloudflare Worker proxy HTTPS pour la veille
// parlementaire sport.
//
// Pourquoi : les serveurs ministériels (info.gouv.fr, education.gouv.fr,
// interieur.gouv.fr, sports.gouv.fr, enseignementsup-recherche.gouv.fr,
// insep.fr, injep.fr, ccomptes.fr) bloquent les IPs GitHub Actions
// (Azure US) au niveau WAF/firewall. Aucun fix code côté pipeline ne
// fonctionne. Solution : faire passer les requêtes par un worker
// Cloudflare qui a des IPs distribuées et acceptées par ces serveurs.
//
// Plan tarifaire : Cloudflare Workers Free = 100 000 requêtes/jour
// (largement suffisant pour ~10 sources × 3 runs/jour = 30 requêtes/j).
//
// Setup côté Cloudflare (par Cyril) :
//   1. https://dash.cloudflare.com → Workers & Pages → Create
//   2. Coller ce fichier intégralement comme code du worker
//   3. Settings → Variables and Secrets → Add :
//        Type: Secret | Name: PROXY_TOKEN | Value: <token aléatoire fort>
//   4. Save & Deploy → noter l'URL `https://<nom>.<sub>.workers.dev`
//   5. Communiquer l'URL + le token (ou les poser en secrets GHA :
//        gh secret set CLOUDFLARE_PROXY_URL --body "https://..."
//        gh secret set CLOUDFLARE_PROXY_TOKEN --body "<token>")
//
// Sécurité :
//   - Token obligatoire (header X-Proxy-Token doit matcher env.PROXY_TOKEN).
//     Sans token, retour 403. Empêche un proxy ouvert.
//   - Whitelist d'hostnames stricte (ALLOWED_HOSTS) — seules les sources
//     officielles de la veille peuvent transiter par ce worker.
//   - Méthode GET uniquement (lecture, pas d'écriture côté upstream).

const ALLOWED_HOSTS = new Set([
  // Premier ministre / gouvernement transversal
  "www.info.gouv.fr",
  // Ministères
  "www.education.gouv.fr",
  "www.interieur.gouv.fr",
  "www.sports.gouv.fr",
  "www.enseignementsup-recherche.gouv.fr",
  "sante.gouv.fr",
  "travail-emploi.gouv.fr",
  // Opérateurs publics
  "www.insep.fr",
  "injep.fr",
  "www.ccomptes.fr",
]);

const FORWARD_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  "Accept":
    "text/html,application/xhtml+xml,application/xml;q=0.9," +
    "application/rss+xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
};

export default {
  async fetch(request, env) {
    // 1. Auth — token obligatoire
    const token = request.headers.get("X-Proxy-Token");
    if (!env.PROXY_TOKEN || !token || token !== env.PROXY_TOKEN) {
      return new Response("Forbidden (missing/invalid X-Proxy-Token)", {
        status: 403,
      });
    }

    // 2. Méthode autorisée : GET uniquement
    if (request.method !== "GET") {
      return new Response("Method Not Allowed (GET only)", { status: 405 });
    }

    // 3. URL cible
    const url = new URL(request.url);
    const target = url.searchParams.get("url");
    if (!target) {
      return new Response("Missing required 'url' query parameter", {
        status: 400,
      });
    }

    let targetUrl;
    try {
      targetUrl = new URL(target);
    } catch (e) {
      return new Response("Invalid URL", { status: 400 });
    }

    if (targetUrl.protocol !== "https:" && targetUrl.protocol !== "http:") {
      return new Response("Only http(s) URLs allowed", { status: 400 });
    }

    // 4. Whitelist hosts
    if (!ALLOWED_HOSTS.has(targetUrl.hostname)) {
      return new Response(
        `Host not allowed: ${targetUrl.hostname}. ` +
          `Add to ALLOWED_HOSTS in worker code.`,
        { status: 403 },
      );
    }

    // 5. Forward upstream
    let upstream;
    try {
      upstream = await fetch(target, {
        method: "GET",
        headers: FORWARD_HEADERS,
        redirect: "follow",
      });
    } catch (e) {
      return new Response(`Upstream fetch error: ${e.message}`, {
        status: 502,
      });
    }

    // 6. Renvoyer la réponse (body streamé + Content-Type préservé)
    const responseHeaders = new Headers();
    const contentType =
      upstream.headers.get("Content-Type") || "application/octet-stream";
    responseHeaders.set("Content-Type", contentType);
    responseHeaders.set("X-Proxy-Status", String(upstream.status));
    responseHeaders.set("X-Proxy-Target", targetUrl.hostname);

    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  },
};
