"""Connecteur Assemblée nationale — open data (zip JSON quotidiens)."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Iterable

from ..models import Item
from ._common import fetch_bytes, parse_iso, unzip_members

log = logging.getLogger(__name__)

BASE_URL = "https://data.assemblee-nationale.fr"

# Parseur d'UID de question AN : "QANR5L17QE9340" → législature 17, type QE, num 9340
_Q_UID_RE = re.compile(r"L(\d+)(QE|QG|QOSD|QST)(\d+)", re.IGNORECASE)


def _first_sentence(text: str, max_len: int = 140) -> str:
    """Renvoie la 1re phrase du texte, tronquée à max_len."""
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", text).strip()
    # Coupe sur fin de phrase si trouvée dans la fenêtre
    m = re.search(r"[\.\!\?]\s", clean[:max_len])
    if m:
        return clean[: m.end()].strip()
    return clean[:max_len].rstrip() + ("…" if len(clean) > max_len else "")


def _question_url(uid: str) -> str:
    """URL canonique pour une question AN — format `<leg>-<num><type>.htm`."""
    if not uid:
        return "https://www.assemblee-nationale.fr/dyn/17/"
    m = _Q_UID_RE.search(uid)
    if m:
        leg, qtype, num = m.group(1), m.group(2).upper(), m.group(3)
        return f"https://questions.assemblee-nationale.fr/q{leg}/{leg}-{num}{qtype}.htm"
    # Fallback : recherche générique
    return f"https://www2.assemblee-nationale.fr/recherche/questions?q={uid}"


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
    file_count = 0
    for name, payload in unzip_members(data):
        if not name.endswith(".json"):
            continue
        file_count += 1
        try:
            obj = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception as e:
            log.debug("JSON KO %s: %s", name, e)
            continue
        items.extend(_normalize(src, name, obj))
    log.info("%s : %d items (sur %d fichiers JSON)", src["id"], len(items), file_count)
    return items


# Clés englobantes fréquentes dans les dumps agrégés de l'AN
# (ex. {"amendements": {"amendement": [...]}} ou {"export": {...}})
_WRAPPER_KEYS = {"amendement", "amendements", "dossier", "dossiers",
                 "dossierParlementaire", "dossiersLegislatifs",
                 "reunion", "reunions", "agenda",
                 "question", "questions", "questionsEcrites",
                 "questionsGouvernement", "export", "items", "records"}


def _iter_records(obj, target_singular: str):
    """Itère sur les 'records' d'un objet JSON AN — tolère les 2 formats :

    - un fichier = un item → racine = {target_singular: {...}} → yield l'objet
    - un fichier = agrégat → racine = {target_plural: [{target_singular: {...}}, …]}
      ou {wrapper: {target_singular: [{...}, {...}]}} → yield chaque item

    target_singular : 'amendement' | 'dossierParlementaire' | 'question' | 'reunion'
    """
    if obj is None:
        return
    # Cas 1 : l'objet est déjà l'item recherché (racine = {target: {…}})
    if isinstance(obj, dict) and target_singular in obj:
        inner = obj[target_singular]
        if isinstance(inner, list):
            for it in inner:
                if isinstance(it, dict):
                    yield it
        elif isinstance(inner, dict):
            yield inner
        return
    # Cas 2 : l'objet est un array d'items
    if isinstance(obj, list):
        for it in obj:
            yield from _iter_records(it, target_singular)
        return
    # Cas 3 : descente dans les wrappers connus
    if isinstance(obj, dict):
        # Préférence : clés plurielles ou wrappers
        for k, v in obj.items():
            if k in _WRAPPER_KEYS or k.lower().endswith("s"):
                yield from _iter_records(v, target_singular)


def _normalize(src: dict, name: str, obj) -> Iterable[Item]:
    """Dispatch par type de dataset."""
    sid = src["id"]
    cat = src["category"]

    if sid == "an_amendements":
        for rec in _iter_records(obj, "amendement"):
            yield from _normalize_amendement({"amendement": rec}, src, cat)
    elif sid == "an_dossiers_legislatifs":
        for rec in _iter_records(obj, "dossierParlementaire"):
            yield from _normalize_dosleg({"dossierParlementaire": rec}, src, cat)
    elif sid in ("an_questions_ecrites", "an_questions_gouvernement"):
        for rec in _iter_records(obj, "question"):
            yield from _normalize_question({"question": rec}, src, cat)
    elif sid == "an_agenda":
        for rec in _iter_records(obj, "reunion"):
            yield from _normalize_agenda({"reunion": rec}, src, cat)
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
    # Date = DERNIÈRE date d'acte législatif (= dernière étape de procédure),
    # pas la date de dépôt. On scanne toutes les dates du chrono et on prend le max.
    chrono = _first(root, "dossier.actesLegislatifs", default=None)
    dates_found: list[datetime] = []
    if isinstance(chrono, (dict, list)):
        for p, v in _flatten(chrono):
            if isinstance(v, str) and len(v) >= 10 and any(k in p.lower() for k in ("date", "timestamp")):
                dt = parse_iso(v[:10])
                if dt:
                    dates_found.append(dt)
    derniere_date = max(dates_found) if dates_found else None
    yield Item(
        source_id=src["id"],
        uid=uid,
        category=cat,
        chamber="AN",
        title=titre or f"Dossier {uid}",
        url=f"https://www.assemblee-nationale.fr/dyn/17/dossiers/{uid}",
        published_at=derniere_date,
        summary=_text_of(
            _first(root, "dossier.titreDossier.titreChemin", default="")
        )[:500],
        raw={"path": "assemblee:dossier", "nb_actes": len(dates_found)},
    )


def _normalize_question(obj, src, cat):
    root = obj.get("question") if isinstance(obj, dict) else None
    if not root:
        return
    uid = _text_of(_first(root, "uid", "indexQuestion", default=""))
    if not uid:
        return
    # Indicateurs de contenu :
    # - rubrique / tête d'analyse = thème court (ex. "sports : nautiques")
    # - texte question = corps de la question (1re phrase = bon résumé)
    rubrique = _text_of(_first(root, "rubrique", "indexationAnalytique.rubrique", default=""))
    tete_analyse = _text_of(_first(root, "teteAnalyse", "indexationAnalytique.teteAnalyse", default=""))
    analyse = _text_of(_first(root, "indexationAnalytique.analyses.analyse", default=""))
    texte = _text_of(_first(root, "textesQuestion.texteQuestion.texte", default=""))
    reponse = _text_of(_first(root, "textesReponses.texteReponse.texte", default=""))
    date_pub = parse_iso(_first(root, "questionDate", default=None))
    auteur = _text_of(_first(root, "auteur.identite.acteurRef", default=""))

    # Construction d'un titre explicite : on préfère rubrique > teteAnalyse > analyse > 1re phrase texte
    sujet_court = (rubrique or tete_analyse or analyse).strip()
    if not sujet_court:
        sujet_court = _first_sentence(texte, max_len=120)
    sujet_court = sujet_court or "Question écrite"
    # Identifiant lisible (numéro court si parsable)
    m_uid = _Q_UID_RE.search(uid)
    numero_court = m_uid.group(3) if m_uid else uid
    qtype_label = {
        "QE": "Question écrite",
        "QG": "Question au gouvernement",
        "QOSD": "Question orale",
        "QST": "Question orale",
    }.get((m_uid.group(2).upper() if m_uid else ""), "Question")

    yield Item(
        source_id=src["id"],
        uid=uid,
        category=cat,
        chamber="AN",
        title=f"{qtype_label} n°{numero_court} — {sujet_court}",
        url=_question_url(uid),
        published_at=date_pub,
        summary=(texte or reponse)[:500],
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
