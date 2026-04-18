"""Connecteur Assemblée nationale — open data (zip JSON quotidiens)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Iterable

from ..models import Item
from ._common import fetch_bytes, parse_iso, unzip_members

log = logging.getLogger(__name__)

BASE_URL = "https://data.assemblee-nationale.fr"


def _flatten(obj, path=""):
    """Aplatit récursivement un JSON en paires (chemin, valeur)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{path}[{i}]")
    else:
        yield path, obj


def _first(obj: dict, *keys: str, default=None):
    for k in keys:
        cur = obj
        ok = True
        for p in k.split("."):
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return default


def _text_of(node) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        # forme {"#text": "..."} fréquente dans les dumps AN
        if "#text" in node:
            return str(node["#text"])
        return json.dumps(node, ensure_ascii=False)
    return str(node)


def fetch_source(src: dict) -> list[Item]:
    """Récupère et normalise un dataset AN."""
    fmt = src.get("format", "json_zip")
    if fmt != "json_zip":
        log.warning("Format %s non supporté pour %s", fmt, src["id"])
        return []
    data = fetch_bytes(src["url"])
    items: list[Item] = []
    for name, payload in unzip_members(data):
        if not name.endswith(".json"):
            continue
        try:
            obj = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception as e:
            log.debug("JSON KO %s: %s", name, e)
            continue
        items.extend(_normalize(src, name, obj))
    log.info("%s : %d items", src["id"], len(items))
    return items


def _normalize(src: dict, name: str, obj) -> Iterable[Item]:
    """Dispatch par type de dataset."""
    sid = src["id"]
    cat = src["category"]

    if sid == "an_amendements":
        yield from _normalize_amendement(obj, src, cat)
    elif sid == "an_dossiers_legislatifs":
        yield from _normalize_dosleg(obj, src, cat)
    elif sid in ("an_questions_ecrites", "an_questions_gouvernement"):
        yield from _normalize_question(obj, src, cat)
    elif sid == "an_agenda":
        yield from _normalize_agenda(obj, src, cat)
    else:
        log.debug("Pas de normaliseur pour %s", sid)


def _normalize_amendement(obj, src, cat):
    # Cas typique : un fichier = un amendement, avec racine "amendement"
    root = obj.get("amendement") if isinstance(obj, dict) else None
    if not root:
        return
    uid = _first(root, "identifiant.numero", "uid", default=None)
    if not uid:
        return
    title = _text_of(
        _first(root, "corps.contenuAuteur.auteurs.auteur.acteurRef", default="")
    )
    dispo = _text_of(_first(root, "corps.dispositif", default=""))
    expose = _text_of(_first(root, "corps.exposeSommaire", default=""))
    sort = _text_of(_first(root, "cycleDeVie.sort.sortEnSeance", default=""))
    dossier_titre = _text_of(_first(root, "identifiant.numeroLong", default=""))
    num = _text_of(uid)
    yield Item(
        source_id=src["id"],
        uid=str(num),
        category=cat,
        chamber="AN",
        title=f"Amendement {num} — {dossier_titre}",
        url=f"https://www.assemblee-nationale.fr/dyn/17/amendements/{num}",
        published_at=parse_iso(_first(root, "cycleDeVie.dateDepot", default=None)),
        summary=(dispo or expose)[:500] + (f" — Sort : {sort}" if sort else ""),
        raw={"path": "assemblee:amendement"},
    )


def _normalize_dosleg(obj, src, cat):
    # Structure : dossierParlementaire > dossier > ...
    root = obj.get("dossierParlementaire") if isinstance(obj, dict) else None
    if not root:
        return
    uid = _text_of(_first(root, "uid", default="")) or _text_of(
        _first(root, "dossier.uid", default="")
    )
    if not uid:
        return
    titre = _text_of(
        _first(root, "dossier.titreDossier.titre", "titreDossier.titre", default="")
    )
    chrono = _first(root, "dossier.actesLegislatifs", default=None)
    date_depot = None
    if isinstance(chrono, dict):
        # on tente d'attraper la première date trouvée
        for p, v in _flatten(chrono):
            if "date" in p.lower() and isinstance(v, str) and len(v) >= 10:
                date_depot = parse_iso(v[:10])
                break
    yield Item(
        source_id=src["id"],
        uid=uid,
        category=cat,
        chamber="AN",
        title=titre or f"Dossier {uid}",
        url=f"https://www.assemblee-nationale.fr/dyn/17/dossiers/{uid}",
        published_at=date_depot,
        summary=_text_of(
            _first(root, "dossier.titreDossier.titreChemin", default="")
        )[:500],
        raw={"path": "assemblee:dossier"},
    )


def _normalize_question(obj, src, cat):
    root = obj.get("question") if isinstance(obj, dict) else None
    if not root:
        return
    uid = _text_of(_first(root, "uid", "indexQuestion", default=""))
    if not uid:
        return
    titre = _text_of(_first(root, "textesReponses.texteReponse.texte", default=""))
    sujet = _text_of(_first(root, "indexationAnalytique.analyses.analyse", default=""))
    texte = _text_of(_first(root, "textesQuestion.texteQuestion.texte", default=""))
    date_pub = parse_iso(_first(root, "questionDate", default=None))
    # Chambre et auteur
    auteur = _text_of(_first(root, "auteur.identite.acteurRef", default=""))
    yield Item(
        source_id=src["id"],
        uid=uid,
        category=cat,
        chamber="AN",
        title=f"Question {uid} — {sujet or 'sans sujet'}",
        url=f"https://questions.assemblee-nationale.fr/q17/17-{uid}.htm",
        published_at=date_pub,
        summary=(texte or titre)[:500],
        raw={"auteur": auteur, "path": "assemblee:question"},
    )


def _normalize_agenda(obj, src, cat):
    root = obj.get("reunion") if isinstance(obj, dict) else None
    if not root:
        return
    uid = _text_of(_first(root, "uid", default=""))
    if not uid:
        return
    titre = _text_of(_first(root, "objet.libelleObjet", default="Réunion"))
    dt = parse_iso(_first(root, "timestampDebut", "timestampDebutReunion", default=None))
    yield Item(
        source_id=src["id"],
        uid=uid,
        category=cat,
        chamber="AN",
        title=f"Agenda — {titre}",
        url=f"https://www.assemblee-nationale.fr/dyn/17/reunions/{uid}",
        published_at=dt,
        summary=(_text_of(_first(root, "objet.themes", default="")))[:500],
        raw={"path": "assemblee:reunion"},
    )
