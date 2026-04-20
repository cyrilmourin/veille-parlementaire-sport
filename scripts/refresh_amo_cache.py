"""Regénère `data/amo_resolved.json` depuis le dump AMO de l'Assemblée.

Le dump AMO (~80 Mo zip, ~400 Mo JSON expandé) contient l'ensemble
des acteurs (PAxxx), organes (POxxx) et mandats (PMxxx) de la XVIIe
législature + historique. On n'a pas besoin de tout charger à chaque
run — on en extrait ~1 500 acteurs actifs + ~300 organes référencés +
les mandats "en cours" (i.e. pas de `dateFin`), condensés dans un
JSON unique ~100 Ko versionné dans le repo.

Usage :
    python -m scripts.refresh_amo_cache                # normal
    python -m scripts.refresh_amo_cache --force        # ignore TTL
    python -m scripts.refresh_amo_cache --max-age-days 14

Le script est pensé pour tourner en GitHub Actions une fois par semaine
et commit le fichier JSON résultant.

Structure du JSON produit :
    {
      "generated_at": "2026-04-20T...",
      "legislature": 17,
      "acteurs": {
        "PA720770": {
          "civ": "Mme", "prenom": "Marie", "nom": "Dupont",
          "groupe": "LFI-NFP", "groupe_ref": "PO800538",
          "qualites": ["Rapporteur spécial", ...]
        },
        ...
      },
      "organes": {
        "PO838901": {
          "libelle": "Commission des affaires culturelles...",
          "libelle_abrege": "Affaires culturelles",
          "libelle_abrev": "CAC", "type": "COMPER",
        },
        ...
      }
    }
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Import paresseux pour autoriser --dry-run sans dépendances installées.
try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

log = logging.getLogger("refresh_amo_cache")

# Dump historique unique : couvre toutes les législatures depuis la XIe (1997)
# jusqu'à aujourd'hui (donc la XVIIe incluse). Publié sous le path /15/ pour
# raisons historiques — c'est l'URL référencée par la FAQ AN open data.
AMO_URL = (
    "https://data.assemblee-nationale.fr/static/openData/repository/"
    "15/amo/tous_acteurs_mandats_organes_xi_legislature/"
    "AMO30_tous_acteurs_tous_mandats_tous_organes_historique.json.zip"
)
# Miroir communautaire (Tricoteuses) en dernier recours — mêmes données,
# nettoyées et versionnées sur framagit. Utile si l'AN est en panne.
AMO_URL_FALLBACK = (
    "https://framagit.org/tricoteuses/open-data-assemblee-nationale/"
    "AMO30_tous_acteurs_tous_mandats_tous_organes_historique/-/archive/"
    "master/AMO30_tous_acteurs_tous_mandats_tous_organes_historique-master.zip"
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

DEFAULT_CACHE_PATH = Path("data/amo_resolved.json")
DEFAULT_MAX_AGE_DAYS = 7


# ---------------------------------------------------------------------------
# Helpers JSON
# ---------------------------------------------------------------------------


def _text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, dict):
        if "#text" in node:
            return str(node["#text"]).strip()
        if "@xsi:nil" in node or "xsi:nil" in node:
            return ""
        return ""
    return str(node).strip()


def _first(obj: Any, *paths: str, default: Any = "") -> Any:
    """Résout une liste de chemins pointés (ex. "etatCivil.ident.nom")."""
    for path in paths:
        cur: Any = obj
        ok = True
        for p in path.split("."):
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False
                break
        if ok and cur not in (None, "", {}):
            return cur
    return default


def _iter_records(obj: Any, key: str) -> Iterable[dict]:
    """Itère sur des enregistrements du dump AN — structure variable.

    Le dump AMO regroupe souvent `{"acteurs": {"acteur": [...]}}` mais
    parfois juste `{"acteur": {...}}` ou `{"export": {...}}`.
    """
    if obj is None:
        return
    if isinstance(obj, list):
        for item in obj:
            yield from _iter_records(item, key)
        return
    if isinstance(obj, dict):
        # Direct hit
        if key in obj:
            val = obj[key]
            if isinstance(val, list):
                for it in val:
                    if isinstance(it, dict):
                        yield it
            elif isinstance(val, dict):
                yield val
            return
        # Descente récursive (un niveau) pour les wrappers type "export"
        for v in obj.values():
            if isinstance(v, (dict, list)):
                yield from _iter_records(v, key)


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------


def download_amo(url: str = AMO_URL, timeout: float = 300.0,
                 _tried_fallback: bool = False) -> bytes | None:
    """Télécharge le zip AMO (~80 Mo).

    Retourne `None` (au lieu de lever) en cas d'erreur HTTP/réseau — le
    caller traite l'absence de dump comme un WARN non bloquant afin que
    le pipeline continue avec le cache existant.
    """
    if httpx is None:
        log.error("httpx non installé (pip install httpx)")
        return None
    log.info("GET %s", url)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT,
                                   "Accept": "application/zip, */*",
                                   "From": "veille@sideline-conseil.fr"}) as c:
            r = c.get(url)
            if r.status_code >= 400:
                log.warning("HTTP %d sur %s", r.status_code, url)
                if not _tried_fallback and url != AMO_URL_FALLBACK:
                    log.info("Tentative fallback : %s", AMO_URL_FALLBACK)
                    return download_amo(AMO_URL_FALLBACK, timeout=timeout,
                                        _tried_fallback=True)
                return None
            log.info("Téléchargé %.1f Mo", len(r.content) / 1024 / 1024)
            return r.content
    except (httpx.HTTPError, OSError) as exc:
        log.warning("Échec téléchargement AMO (%s) : %s", url, exc)
        if not _tried_fallback and url != AMO_URL_FALLBACK:
            log.info("Tentative fallback : %s", AMO_URL_FALLBACK)
            return download_amo(AMO_URL_FALLBACK, timeout=timeout,
                                _tried_fallback=True)
        return None


# ---------------------------------------------------------------------------
# Extraction acteurs / organes / mandats
# ---------------------------------------------------------------------------


def extract_acteur(rec: dict) -> tuple[str, dict] | None:
    """Extrait un dict compact depuis un record acteur (`ActeurType`).

    XSD : uid (IdActeur_type = PAxxx), etatCivil.ident.{civ,prenom,nom}.
    """
    uid = _text(_first(rec, "uid.#text", "uid"))
    if not uid or not uid.startswith("PA"):
        return None
    civ = _text(_first(rec, "etatCivil.ident.civ"))
    prenom = _text(_first(rec, "etatCivil.ident.prenom"))
    nom = _text(_first(rec, "etatCivil.ident.nom"))
    if not (prenom or nom):
        return None
    out = {"civ": civ, "prenom": prenom, "nom": nom}
    # Alpha (nom de famille normalisé) parfois utile pour tri
    alpha = _text(_first(rec, "etatCivil.ident.alpha"))
    if alpha and alpha.lower() != nom.lower():
        out["alpha"] = alpha
    return uid, out


# Libellé "humain" des types d'organe (codes AN non normalisés).
_ORGANE_TYPE_LABELS = {
    "COMPER": "Commission permanente",
    "COMSPSS": "Commission spéciale",
    "COMENQ": "Commission d'enquête",
    "MISINFO": "Mission d'information",
    "DELEGBUREAU": "Délégation",
    "DELEG": "Délégation",
    "OFFPAR": "Office parlementaire",
    "GP": "Groupe politique",
    "GROUPE": "Groupe politique",
    "ASSEMBLEE": "Assemblée nationale",
    "MINISTERE": "Ministère",
    "GOUVERNEMENT": "Gouvernement",
    "CNPS": "Conseil national",
    "GE": "Groupe d'études",
    "GA": "Groupe d'amitié",
    "API": "API",
    "PARPOL": "Parti politique",
    "ORGEXTPARL": "Organe parlementaire extérieur",
    "ORGAINT": "Organisation internationale",
}


def extract_organe(rec: dict) -> tuple[str, dict] | None:
    """Extrait un dict compact depuis un record organe (`OrganeAbstrait_Type`).

    XSD : uid (POxxx), codeType, libelle, libelleAbrege, libelleAbrev.
    """
    uid = _text(_first(rec, "uid.#text", "uid"))
    if not uid or not uid.startswith("PO"):
        return None
    libelle = _text(_first(rec, "libelle"))
    if not libelle:
        return None
    out: dict[str, str] = {"libelle": libelle}
    abr = _text(_first(rec, "libelleAbrege"))
    if abr and abr != libelle:
        out["libelle_abrege"] = abr
    acronyme = _text(_first(rec, "libelleAbrev"))
    if acronyme:
        out["libelle_abrev"] = acronyme
    code_type = _text(_first(rec, "codeType"))
    if code_type:
        out["type"] = code_type
    # Seuls les organes actifs nous intéressent pour l'affichage des
    # titres en cours : on garde uniquement organeActif=True.
    date_fin = _text(_first(rec, "viMoDe.dateFin"))
    if date_fin:
        out["actif"] = False
    return uid, out


def extract_mandat(rec: dict) -> tuple[str, dict] | None:
    """Extrait un mandat actif minimal.

    Garde uniquement les mandats *actifs* (`dateFin` vide) pour :
      - retrouver le groupe politique d'un acteur
      - retrouver les qualités (président, rapporteur, ...)
    """
    acteur_ref = _text(_first(rec, "acteurRef"))
    if not acteur_ref or not acteur_ref.startswith("PA"):
        return None
    # Skip les mandats historiques clos
    date_fin = _text(_first(rec, "dateFin"))
    if date_fin:
        return None

    # Organe de rattachement : le chemin diffère selon le type.
    # Mandat parlementaire → organes.organeRef (potentiellement liste).
    organe_refs = _first(rec, "organes.organeRef", default=None)
    if isinstance(organe_refs, str):
        organes = [organe_refs]
    elif isinstance(organe_refs, list):
        organes = [_text(x) for x in organe_refs if _text(x)]
    elif isinstance(organe_refs, dict):
        # Parfois {"organeRef": "POxxx"} imbriqué
        organes = [_text(_first(organe_refs, "#text", default=""))]
    else:
        organes = []
    organes = [o for o in organes if o and o.startswith("PO")]

    type_organe = _text(_first(rec, "typeOrgane"))
    qualite_code = _text(_first(rec, "infosQualite.codeQualite"))
    qualite_lib = _text(_first(rec, "infosQualite.libQualite"))

    out: dict[str, Any] = {}
    if organes:
        out["organes"] = organes
    if type_organe:
        out["type_organe"] = type_organe
    if qualite_code:
        out["code_qualite"] = qualite_code
    if qualite_lib:
        out["lib_qualite"] = qualite_lib
    if not out:
        return None
    out["acteur_ref"] = acteur_ref
    return _text(_first(rec, "uid")) or acteur_ref, out


# ---------------------------------------------------------------------------
# Parse du zip
# ---------------------------------------------------------------------------


def parse_zip(payload: bytes) -> dict:
    """Parse le zip AMO et retourne le dict compact prêt à sérialiser.

    Le dump peut être au format :
      (a) un unique `acteur.json` contenant {"export": {...}}
          avec sous-clés "acteurs"/"organes"/"mandats"
      (b) un répertoire de fichiers unitaires : acteur/PA###.json,
          organe/PO###.json, mandat/PM###.json

    On essaie (a) puis (b) en fallback.
    """
    acteurs: dict[str, dict] = {}
    organes: dict[str, dict] = {}
    mandats_by_acteur: dict[str, list[dict]] = {}

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".json")]
        log.info("%d fichiers JSON dans le zip", len(names))

        # Heuristique : si > 100 fichiers, on est en format (b).
        unitaire = len(names) > 100

        for i, name in enumerate(names):
            if i % 5000 == 0 and i > 0:
                log.info("  parsed %d/%d", i, len(names))
            try:
                raw = json.loads(zf.read(name))
            except Exception as e:
                log.debug("JSON fail on %s: %s", name, e)
                continue

            if unitaire:
                # Un fichier = une entité
                lname = name.lower()
                if "/acteur" in lname or lname.endswith("/acteur.json") or lname.startswith("acteur"):
                    rec = raw.get("acteur") or raw
                    r = extract_acteur(rec)
                    if r:
                        acteurs[r[0]] = r[1]
                elif "/organe" in lname or lname.endswith("/organe.json") or lname.startswith("organe"):
                    rec = raw.get("organe") or raw
                    r = extract_organe(rec)
                    if r:
                        organes[r[0]] = r[1]
                elif "/mandat" in lname or lname.endswith("/mandat.json") or lname.startswith("mandat"):
                    rec = raw.get("mandat") or raw
                    r = extract_mandat(rec)
                    if r:
                        uid, m = r
                        mandats_by_acteur.setdefault(m["acteur_ref"], []).append(m)
            else:
                # Format (a) : dump global — parcours complet
                for rec in _iter_records(raw, "acteur"):
                    r = extract_acteur(rec)
                    if r:
                        acteurs[r[0]] = r[1]
                for rec in _iter_records(raw, "organe"):
                    r = extract_organe(rec)
                    if r:
                        organes[r[0]] = r[1]
                for rec in _iter_records(raw, "mandat"):
                    r = extract_mandat(rec)
                    if r:
                        _, m = r
                        mandats_by_acteur.setdefault(m["acteur_ref"], []).append(m)

    log.info("Extrait %d acteurs, %d organes, mandats actifs pour %d acteurs",
             len(acteurs), len(organes), len(mandats_by_acteur))

    # Enrichit acteurs avec groupe politique + qualités notables.
    # Groupe = mandat dont l'organe cible est un "GP" ou le type est "GP".
    for pa_uid, mandats in mandats_by_acteur.items():
        acteur = acteurs.get(pa_uid)
        if not acteur:
            continue
        qualites = []
        for m in mandats:
            type_org = (m.get("type_organe") or "").upper()
            lib_q = m.get("lib_qualite") or ""
            for org_ref in m.get("organes", []):
                org = organes.get(org_ref)
                if not org:
                    continue
                org_type = (org.get("type") or "").upper()
                if org_type in ("GP", "GROUPE"):
                    # Groupe politique : on prend le libellé abrégé si dispo
                    acteur["groupe"] = org.get("libelle_abrev") or org.get("libelle_abrege") or org.get("libelle") or ""
                    acteur["groupe_ref"] = org_ref
            if lib_q and lib_q not in ("Membre", "Sénateur", "Député", "Députée"):
                q = lib_q
                if type_org == "COMPER" and m.get("organes"):
                    # Rattache la qualité à une commission quand pertinent
                    org = organes.get(m["organes"][0]) if m["organes"] else None
                    if org:
                        q = f"{lib_q} — {org.get('libelle_abrev') or org.get('libelle_abrege') or org.get('libelle')}"
                qualites.append(q)
        if qualites:
            # Dédoublonner en gardant l'ordre
            seen = set()
            acteur["qualites"] = [q for q in qualites if not (q in seen or seen.add(q))][:5]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "legislature": 17,
        "acteurs": acteurs,
        "organes": organes,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def is_fresh(path: Path, max_age_days: int) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
        ts = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
    except Exception:
        return False
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age < max_age_days * 86400


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_CACHE_PATH,
                        help="Chemin du fichier JSON à écrire")
    parser.add_argument("--force", action="store_true",
                        help="Ignore le TTL, refetch quand même")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                        help=f"TTL en jours (défaut {DEFAULT_MAX_AGE_DAYS})")
    parser.add_argument("--url", default=AMO_URL, help="URL du dump AMO")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose > 1 else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if not args.force and is_fresh(args.output, args.max_age_days):
        log.info("Cache %s est frais (<%dj), skip", args.output, args.max_age_days)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    payload = download_amo(args.url)
    log.info("Download %.1fs", time.monotonic() - t0)

    if payload is None:
        if args.output.exists():
            log.warning("Échec refresh AMO — cache existant conservé (%s)",
                        args.output)
        else:
            log.warning("Échec refresh AMO — aucun cache local ; "
                        "le pipeline fallback sur libellés PAxxx/POxxx bruts")
        return 0

    t0 = time.monotonic()
    try:
        result = parse_zip(payload)
    except Exception as exc:
        log.error("Parse zip AMO a échoué : %s — cache existant conservé", exc)
        return 0
    log.info("Parse %.1fs", time.monotonic() - t0)

    tmp = args.output.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(args.output)

    size_kb = args.output.stat().st_size / 1024
    log.info("Écrit %s (%.1f Ko — %d acteurs, %d organes)",
             args.output, size_kb, len(result["acteurs"]), len(result["organes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
