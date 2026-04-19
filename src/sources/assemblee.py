"""Connecteur Assemblée nationale — open data (zip JSON quotidiens)."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Iterable

from ..models import Item
from ._common import fetch_bytes, parse_iso, unzip_members, unzip_members_since

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


def _all_text(node) -> str:
    """Collecte récursive de toutes les chaînes textuelles d'un nœud JSON.

    Filet de sécurité 'shotgun' : les dumps AN ont des structures
    variables, nos paths ciblés ratent parfois le contenu pertinent.
    Concaténer tout le texte garantit que le matcher mots-clés voit
    le contenu, même quand la structure change.
    """
    bits: list[str] = []

    def _walk(n):
        if n is None:
            return
        if isinstance(n, str):
            s = n.strip()
            if s and len(s) > 1:
                bits.append(s)
        elif isinstance(n, dict):
            for v in n.values():
                _walk(v)
        elif isinstance(n, list):
            for v in n:
                _walk(v)

    _walk(node)
    return " ".join(bits)


def fetch_source(src: dict) -> list[Item]:
    """Récupère et normalise un dataset AN.

    Supporte un filtre `since_days` (config source ou env var `AN_SINCE_DAYS`)
    appliqué sur `ZipInfo.date_time` : pour les dumps massifs (ex. Amendements
    avec 104k JSON), on évite de décompresser et normaliser les entrées trop
    anciennes. Une veille quotidienne n'a pas besoin de re-ingérer des
    amendements de 2023 à chaque run.
    """
    fmt = src.get("format", "json_zip")
    if fmt != "json_zip":
        log.warning("Format %s non supporté pour %s", fmt, src["id"])
        return []
    data = fetch_bytes(src["url"])
    items: list[Item] = []
    file_count = 0

    # Fenêtre date optionnelle : src["since_days"] > env AN_SINCE_DAYS > None
    since_days_raw = src.get("since_days") or os.environ.get("AN_SINCE_DAYS")
    since: datetime | None = None
    if since_days_raw:
        try:
            since = datetime.utcnow() - timedelta(days=int(since_days_raw))
            log.info(
                "%s : filtre date >= %s (fenêtre %s jours)",
                src["id"], since.date().isoformat(), since_days_raw,
            )
        except (ValueError, TypeError):
            log.warning("%s : since_days invalide (%r), pas de filtre",
                        src["id"], since_days_raw)

    # 1er filtre (cheap) sur ZipInfo.date_time. Peut être sans effet si le
    # dump AN régénère les mtimes à chaque build — auquel cas on filtrera
    # sur la date réelle extraite du JSON plus bas.
    if since is not None:
        iterator = (
            (name, payload)
            for name, _dt, payload in unzip_members_since(data, since=since)
        )
    else:
        iterator = unzip_members(data)

    filtered_by_content = 0
    for name, payload in iterator:
        if not name.endswith(".json"):
            continue
        file_count += 1
        try:
            obj = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception as e:
            log.debug("JSON KO %s: %s", name, e)
            continue
        for item in _normalize(src, name, obj):
            # 2e filtre (reliable) sur la date extraite du JSON : garantit
            # que la fenêtre s'applique même si ZipInfo.date_time est
            # uniforme (cas des dumps régénérés quotidiennement).
            if since is not None and item.published_at is not None:
                if item.published_at < since:
                    filtered_by_content += 1
                    continue
            items.append(item)
    if since is not None and filtered_by_content:
        log.info(
            "%s : %d items retirés par filtre date (published_at < %s)",
            src["id"], filtered_by_content, since.date().isoformat(),
        )
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
    num = _text_of(uid)

    # Auteur : soit nom+prenom, soit acteurRef (identifiant PA….)
    auteur_nom = _text_of(_first(root, "signataires.auteur.nom",
                                   "auteurs.auteur.identite.nom", default=""))
    auteur_prenom = _text_of(_first(root, "signataires.auteur.prenom",
                                     "auteurs.auteur.identite.prenom", default=""))
    auteur_ref = _text_of(_first(root, "signataires.auteur.acteurRef",
                                  "corps.contenuAuteur.auteurs.auteur.acteurRef",
                                  "auteurs.auteur.acteurRef", default=""))
    auteur_groupe = _text_of(_first(root, "signataires.groupePolitiqueRef",
                                     "auteurs.auteur.groupePolitiqueRef", default=""))
    auteur_label = " ".join(x for x in [auteur_prenom, auteur_nom] if x).strip() or auteur_ref or "Auteur inconnu"

    # Dispositif + exposé sommaire (matériel pertinent pour matching mots-clés)
    dispo = _text_of(_first(root, "corps.dispositif", default=""))
    expose = _text_of(_first(root, "corps.exposeSommaire", "exposeSommaire", default=""))

    # Statut : sort en séance / en commission / "Irrecevable"
    sort = _text_of(_first(
        root,
        "cycleDeVie.sort.sortEnSeance",
        "cycleDeVie.etatDesTraitements.etat",
        "cycleDeVie.sort.libelle",
        default=""
    ))
    etat = _text_of(_first(root, "etat", "cycleDeVie.etat", default=""))
    statut = sort or etat or ""

    # Contexte dossier (article, division)
    article = _text_of(_first(root, "pointeurFragmentTexte.division.articleDesignation",
                               "pointeurFragmentTexte.article.numeroCorrection",
                               default=""))
    dossier_titre = _text_of(_first(root, "identifiant.numeroLong",
                                     "examinentInfo.loi.intitule",
                                     "dossierRef",
                                     default=""))

    # Summary enrichi : on concatène tout le texte utile pour que le
    # matcher mots-clés puisse attaquer le contenu entier (pas juste
    # le dispositif qui est souvent purement technique).
    summary_parts = [
        f"Auteur : {auteur_label}" if auteur_label else "",
        f"Statut : {statut}" if statut else "",
        f"Article : {article}" if article else "",
        expose,
        dispo,
    ]
    structured = " — ".join(p for p in summary_parts if p).strip()
    # Shotgun : quand les paths ciblés ne couvrent pas la structure
    # réelle du JSON (fréquent sur dumps AN), on ajoute tout le texte
    # du nœud pour que le matcher mots-clés trouve les occurrences.
    shotgun = _all_text(root)
    summary = (structured + " — " + shotgun if structured else shotgun)[:2000]

    # Titre compact et informatif
    title_bits = [f"Amendement n°{num}"]
    if statut:
        title_bits.append(f"[{statut}]")
    title_bits.append(f"— {auteur_label}")
    if article:
        title_bits.append(f"· art. {article}")
    title = " ".join(title_bits)[:220]

    yield Item(
        source_id=src["id"],
        uid=str(num),
        category=cat,
        chamber="AN",
        title=title,
        url=f"https://www.assemblee-nationale.fr/dyn/17/amendements/{num}",
        published_at=parse_iso(_first(root, "cycleDeVie.dateDepot",
                                       "cycleDeVie.dateSaisie", default=None)),
        summary=summary,
        raw={"path": "assemblee:amendement", "auteur_ref": auteur_ref,
             "groupe": auteur_groupe, "dossier": dossier_titre, "statut": statut},
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
    texte = _text_of(_first(root, "textesQuestion.texteQuestion.texte",
                             "texte", default=""))
    reponse = _text_of(_first(root, "textesReponses.texteReponse.texte",
                               "reponse", default=""))
    date_pub = parse_iso(_first(root, "questionDate", "dateQuestion",
                                 "dateDepot", default=None))

    # Auteur — on essaie nom/prénom explicites avant de tomber sur acteurRef.
    auteur_nom = _text_of(_first(root,
                                  "auteur.identite.nom",
                                  "auteur.identite.nomFamille",
                                  default=""))
    auteur_prenom = _text_of(_first(root,
                                     "auteur.identite.prenom",
                                     default=""))
    auteur_civilite = _text_of(_first(root, "auteur.identite.civ", default=""))
    auteur_ref = _text_of(_first(root, "auteur.identite.acteurRef",
                                  "auteur.acteurRef", default=""))
    auteur_groupe = _text_of(_first(root, "auteur.groupe.abrege",
                                     "auteur.groupePolitiqueRef",
                                     default=""))
    auteur_label = " ".join(x for x in [auteur_civilite, auteur_prenom, auteur_nom] if x).strip()
    if not auteur_label:
        auteur_label = auteur_ref or "Auteur"

    ministere = _text_of(_first(root, "minInt.abrege",
                                 "ministereAttributaire.intitule",
                                 default=""))

    # Construction du titre :
    # "Question écrite n°9711 — Mme Hervieu (Groupe) → Min. Sports : <sujet>"
    sujet_court = (rubrique or tete_analyse or analyse).strip()
    if not sujet_court:
        sujet_court = _first_sentence(texte, max_len=100)
    sujet_court = sujet_court or "Question"
    m_uid = _Q_UID_RE.search(uid)
    numero_court = m_uid.group(3) if m_uid else uid
    qtype_label = {
        "QE": "Question écrite",
        "QG": "Question au gouvernement",
        "QOSD": "Question orale",
        "QST": "Question orale",
    }.get((m_uid.group(2).upper() if m_uid else ""), "Question")

    title_bits = [f"{qtype_label} n°{numero_court}", f"— {auteur_label}"]
    if auteur_groupe:
        title_bits.append(f"({auteur_groupe})")
    if ministere:
        title_bits.append(f"→ {ministere}")
    title_bits.append(f": {sujet_court}")
    title = " ".join(title_bits)[:220]

    # Summary enrichi pour matching : on inclut auteur + ministère + rubrique
    # + texte + réponse. Le matcher attaque title+summary.
    summary_parts = [
        f"{auteur_label}" + (f" ({auteur_groupe})" if auteur_groupe else ""),
        f"Destinataire : {ministere}" if ministere else "",
        f"Rubrique : {rubrique}" if rubrique else "",
        f"Analyse : {analyse}" if analyse else "",
        texte,
        reponse,
    ]
    summary = " — ".join(p for p in summary_parts if p).strip()[:2000]

    yield Item(
        source_id=src["id"],
        uid=uid,
        category=cat,
        chamber="AN",
        title=title,
        url=_question_url(uid),
        published_at=date_pub,
        summary=summary,
        raw={"auteur_ref": auteur_ref, "auteur": auteur_label,
             "groupe": auteur_groupe, "ministere": ministere,
             "path": "assemblee:question"},
    )


def _normalize_agenda(obj, src, cat):
    root = obj.get("reunion") if isinstance(obj, dict) else None
    if not root:
        return
    uid = _text_of(_first(root, "uid", default=""))
    if not uid:
        return
    titre = _text_of(_first(root, "objet.libelleObjet",
                              "titreReunion", default="Réunion"))
    dt = parse_iso(_first(root, "timestampDebut",
                           "timestampDebutReunion",
                           "dateReunion", default=None))

    # Organe (commission, délégation…)
    organe = _text_of(_first(root, "organeReuniRef",
                               "organeRef",
                               "typeReunion",
                               default=""))
    lieu = _text_of(_first(root, "lieu.libelleCourt",
                            "lieu.libelle", default=""))

    # Ordre du jour / thèmes — souvent structurés en liste.
    # On aplati tout ce qui ressemble à un thème/libellé pour nourrir le matcher.
    odj_parts: list[str] = []
    themes = _first(root, "objet.themes", default=None)
    if isinstance(themes, (dict, list)):
        for p, v in _flatten(themes):
            if isinstance(v, str) and len(v) > 3:
                odj_parts.append(v)
    elif isinstance(themes, str):
        odj_parts.append(themes)

    points = _first(root, "pointsOrdreDuJour", "ordreDuJour", default=None)
    if isinstance(points, (dict, list)):
        for p, v in _flatten(points):
            if isinstance(v, str) and len(v) > 3 and ("libelle" in p.lower() or "objet" in p.lower() or "texte" in p.lower()):
                odj_parts.append(v)

    odj_text = " · ".join(odj_parts)[:2000]

    title_bits = [f"Agenda — {titre}"]
    if organe:
        title_bits.append(f"({organe})")
    title = " ".join(title_bits)[:220]

    structured = " — ".join(p for p in [
        f"Organe : {organe}" if organe else "",
        f"Lieu : {lieu}" if lieu else "",
        odj_text,
    ] if p)
    # Shotgun : filet de sécurité au cas où la structure du JSON
    # réunion ne colle pas à nos paths ciblés.
    shotgun = _all_text(root)
    summary = (structured + " — " + shotgun if structured else shotgun)[:2000]

    yield Item(
        source_id=src["id"],
        uid=uid,
        category=cat,
        chamber="AN",
        title=title,
        url=f"https://www.assemblee-nationale.fr/dyn/17/reunions/{uid}",
        published_at=dt,
        summary=summary,
        raw={"path": "assemblee:reunion", "organe": organe, "lieu": lieu},
    )
