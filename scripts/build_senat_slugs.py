"""R25b-A (2026-04-23) — Construit l'index nom→slug senfic des 348 sénateurs.

Source : https://www.senat.fr/senateurs/senatl.html (liste alphabétique
officielle publique, HTML stable). Pour chaque sénateur, on extrait :

  - `slug` (ex. `wattebled_dany19585h`) — identifiant utilisé par
    /senateur/<slug>.html ET /senimg/<slug>_carre.jpg (même slug).
  - `nom_usuel`, `prenom_usuel` (lisibles, extraits du texte de l'ancre).
  - `key` normalisée (token-sort unidecode minuscules) pour lookup.

Le JSON produit (`data/senat_slugs.json`) est versionné et rechargeable
sans réseau par `src/senat_slugs.py` côté export. Taille ~30 ko pour
348 entrées.

Pourquoi pas un build à l'export ?
--------------------------------
L'export site tourne en CI (GitHub Actions) : ajouter un HTTP fetch au
build augmente la fragilité (timeout, blocage CDN). Pré-calculer le JSON
est plus prévisible. À régénérer manuellement après chaque renouvellement
sénatorial (élections partielles, en moyenne quelques fois par an).

Usage : `python scripts/build_senat_slugs.py` depuis la racine du repo.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.senat.fr/senateurs/senatl.html"
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "senat_slugs.json"

# Regex du lien fiche sénateur :
#   <a href="/senateur/<slug>.html">NOM Prénom</a>
# Le slug contient [a-z0-9_-]+ (apostrophes et espaces déjà convertis en _
# côté serveur). Exemple : wattebled_dany19585h, anglars_jean_claude20032t.
_HREF_RE = re.compile(r"^/senateur/([a-z0-9_-]+)\.html$")
_CIV_TOKENS = {"m.", "mme", "mlle", "dr", "pr", "m", "mme.", "mlle."}


def _normalize(name: str) -> str:
    """Même algo que `_normalize_auteur_name_senat` côté site_export :
    unidecode + lowercase + retrait civilité + tri des tokens.
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()
    s = re.sub(r"[.,;]", " ", s)
    tokens = [t for t in s.split() if t and t not in _CIV_TOKENS]
    if not tokens:
        return ""
    return " ".join(sorted(tokens))


def fetch_senat_list(url: str = SOURCE_URL) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "veille-parl-sport/R25b-A"})
    r.raise_for_status()
    # La page est en UTF-8 (déclaré dans <meta charset="utf-8">).
    r.encoding = "utf-8"
    return r.text


def parse_senat_list(html: str) -> list[dict]:
    """Extrait toutes les ancres `/senateur/<slug>.html` et leur texte."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen_slugs: set[str] = set()
    for a in soup.find_all("a", href=True):
        m = _HREF_RE.match(a["href"])
        if not m:
            continue
        slug = m.group(1)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        # Le texte de l'ancre est du type "WATTEBLED\u00a0Dany" ou
        # "WATTEBLED Dany" selon la version du template Sénat. On récupère
        # tout, on normalise les espaces.
        text = a.get_text(" ", strip=True)
        if not text:
            continue
        # Séparation heuristique nom usuel / prénom usuel : tout ce qui
        # est en majuscules côté gauche = nom ; le reste = prénom. Le
        # Sénat capitalise systématiquement le nom en MAJUSCULES dans
        # ses listes officielles.
        tokens = text.split()
        nom_tokens = []
        prenom_tokens = []
        for t in tokens:
            if t == t.upper() and any(c.isalpha() for c in t):
                nom_tokens.append(t)
            else:
                prenom_tokens.append(t)
        nom_usuel = " ".join(nom_tokens).strip()
        prenom_usuel = " ".join(prenom_tokens).strip()
        full = f"{prenom_usuel} {nom_usuel}".strip() if prenom_usuel else nom_usuel
        key = _normalize(full)
        if not key:
            continue
        out.append({
            "slug": slug,
            "nom_usuel": nom_usuel,
            "prenom_usuel": prenom_usuel,
            "key": key,
            "photo_url": f"https://www.senat.fr/senimg/{slug}_carre.jpg",
            "fiche_url": f"https://www.senat.fr/senateur/{slug}.html",
        })
    return out


def main() -> int:
    print(f"[build_senat_slugs] fetching {SOURCE_URL}")
    html = fetch_senat_list()
    entries = parse_senat_list(html)
    print(f"[build_senat_slugs] {len(entries)} sénateurs extraits")
    if len(entries) < 300:
        print(
            f"[build_senat_slugs] /!\\ suspicious count ({len(entries)}, attendu ~348) — "
            "structure HTML peut avoir changé",
            file=sys.stderr,
        )
    # Dédup par key (défensif : homonymes exacts sont rares mais possibles,
    # dans ce cas le premier senateur gagne).
    by_key: dict[str, dict] = {}
    conflicts: list[tuple[str, str, str]] = []
    for e in entries:
        k = e["key"]
        if k in by_key:
            conflicts.append((k, by_key[k]["slug"], e["slug"]))
            continue
        by_key[k] = e
    if conflicts:
        print(f"[build_senat_slugs] /!\\ {len(conflicts)} collisions sur key normalisée :")
        for k, a, b in conflicts:
            print(f"    {k!r} : {a} vs {b}")
    # Sortie JSON stable pour diff propre (tri par slug).
    sorted_entries = sorted(by_key.values(), key=lambda x: x["slug"])
    payload = {
        "source_url": SOURCE_URL,
        "count": len(sorted_entries),
        "entries": sorted_entries,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[build_senat_slugs] écrit {OUTPUT} ({OUTPUT.stat().st_size} octets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
