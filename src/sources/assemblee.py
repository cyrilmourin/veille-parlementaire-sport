"""Connecteur Assemblée nationale — open data (zip JSON + XML comptes rendus)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

from ..models import Item
from ._common import fetch_bytes, parse_iso, unzip_members, unzip_members_since

log = logging.getLogger(__name__)

BASE_URL = "https://data.assemblee-nationale.fr"


def _utcnow_naive() -> datetime:
    """`now` naïf en UTC — les `published_at` stockés en DB sont naïfs."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

# Parseur d'UID de question AN : "QANR5L17QE9340" → législature 17, type QE, num 9340
# Types XSD officiels : QE (écrite) | QG (au gouvernement) | QOSD (orale sans débat) | QM (ministre)
# QST = ancien terme officieux, gardé pour rétrocompat des UID historiques.
_Q_UID_RE = re.compile(r"L(\d+)(QE|QG|QOSD|QST|QM)(\d+)", re.IGNORECASE)


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


def _deep_find(node, *key_names: str):
    """Walk un JSON (dicts et listes) et renvoie la première valeur dont la
    clé terminale correspond à `key_names` (match exact, premier en priorité).

    Utile pour les schémas XSD dont un élément a `maxOccurs="unbounded"` : le
    dumper JSON peut rendre soit un dict (1 occurrence) soit une liste (N),
    ce que `_first` à base de dot-path ne gère pas. Exemple : pour les
    questions AN, `textesQuestion.texteQuestion.infoJO.dateJO` peut être à
    deux niveaux de profondeur (dict) OU sous une liste (index 0)."""
    if node is None:
        return None
    # DFS itératif, ordre de visite = ordre d'apparition dans le JSON
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k in key_names:
                if k in cur and cur[k] not in (None, "", [], {}):
                    return cur[k]
            # on pousse les enfants en sens inverse pour préserver l'ordre
            for v in reversed(list(cur.values())):
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            for v in reversed(cur):
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DATE_IN_NAME_RE = re.compile(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})")


def _strip_xml(text: str) -> str:
    """Retire les tags XML/HTML et normalise les espaces."""
    no_tags = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", no_tags).strip()


def _decode(payload: bytes) -> str:
    """Décode un payload texte (XML/HTML) en essayant utf-8 puis cp1252."""
    for enc in ("utf-8", "cp1252", "iso-8859-1"):
        try:
            return payload.decode(enc)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _fetch_xml_zip(src: dict) -> list[Item]:
    """Handler XML zip — pour les dumps type Syceron Brut (comptes rendus AN).

    Principe : un fichier XML = une séance (ou un fragment). On ne parse pas
    la structure XML (propriétaire, changeante) — on strippe les tags et on
    laisse le matcher mots-clés attaquer tout le texte. UID hash-based stable
    par nom de fichier.
    """
    sid = src["id"]
    cat = src["category"]
    data = fetch_bytes(src["url"])

    # Fenêtre date : src["since_days"] > env AN_SINCE_DAYS > 30 (défaut)
    since_days_raw = src.get("since_days") or os.environ.get("AN_SINCE_DAYS") or 30
    try:
        since = _utcnow_naive() - timedelta(days=int(since_days_raw))
        log.info(
            "%s : filtre date >= %s (fenêtre %s jours)",
            sid, since.date().isoformat(), since_days_raw,
        )
    except (ValueError, TypeError):
        since = _utcnow_naive() - timedelta(days=30)
        log.warning("%s : since_days invalide (%r), défaut 30j", sid, since_days_raw)

    items: list[Item] = []
    file_count = 0
    for name, dt, payload in unzip_members_since(data, since=since):
        if not name.lower().endswith((".xml", ".html", ".htm", ".txt")):
            continue
        file_count += 1
        try:
            raw = _decode(payload)
        except Exception as e:
            log.debug("decode KO %s: %s", name, e)
            continue
        text = _strip_xml(raw)
        if not text:
            continue

        # Date : préfère la date extraite du nom de fichier (ex. seance_20260315.xml)
        # sinon ZipInfo.date_time.
        published_at = dt
        m = _DATE_IN_NAME_RE.search(name)
        if m:
            try:
                published_at = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass

        # UID stable basé sur (source_id, nom_fichier)
        uid = hashlib.sha1(f"{sid}:{name}".encode()).hexdigest()[:16]

        # Titre : on extrait la base du nom du fichier (sans chemin/extension)
        base = os.path.basename(name).rsplit(".", 1)[0]
        title = f"Compte rendu AN — {base}"[:220]

        # Résumé : tronqué à 2000 caractères. Le matcher mots-clés verra
        # donc la première partie du CR, suffisant pour détecter une
        # mention « sport ».
        summary = text[:2000]

        items.append(Item(
            source_id=sid,
            uid=uid,
            category=cat,
            chamber="AN",
            title=title,
            url=f"https://www.assemblee-nationale.fr/dyn/17/seances",
            published_at=published_at,
            summary=summary,
            raw={"path": "assemblee:syceron", "fichier": name,
                 "taille": len(payload)},
        ))
    log.info("%s : %d items (sur %d fichiers XML/HTML)", sid, len(items), file_count)
    return items


def fetch_source(src: dict) -> list[Item]:
    """Récupère et normalise un dataset AN.

    Supporte un filtre `since_days` (config source ou env var `AN_SINCE_DAYS`)
    appliqué sur `ZipInfo.date_time` : pour les dumps massifs (ex. Amendements
    avec 104k JSON), on évite de décompresser et normaliser les entrées trop
    anciennes. Une veille quotidienne n'a pas besoin de re-ingérer des
    amendements de 2023 à chaque run.

    Formats supportés :
    - json_zip : dumps JSON agrégés (amendements, questions, agenda, dossiers…)
    - xml_zip  : dumps XML (Syceron comptes rendus) — strippe les tags avant matcher
    """
    fmt = src.get("format", "json_zip")
    if fmt == "xml_zip":
        return _fetch_xml_zip(src)
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
            since = _utcnow_naive() - timedelta(days=int(since_days_raw))
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
    elif sid in ("an_questions_ecrites", "an_questions_gouvernement",
                 "an_questions_orales_sans_debat"):
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


# ---------------------------------------------------------------------------
# Mapping codeActe → (institution, stage, step, flags) — dossiers législatifs
#
# Source de référence : parser `anpy` de Regards Citoyens (collectif derrière
# nosdeputes.fr), fichier `anpy/dossier_from_opendata.py`. C'est le référentiel
# de facto de l'écosystème open source FR, basé sur les XSD AN officiels.
#   https://github.com/regardscitoyens/anpy/blob/master/anpy/dossier_from_opendata.py
#
# Règles clés :
#   - préfixe `AN*`  → Assemblée nationale, préfixe `SN*` → Sénat
#   - substring `1-` → 1re lecture, `2-` / `3-` → 2e / 3e, `NLEC-` → nouvelle
#     lecture, `ANLDEF-` → l. définitive, `CMP-` → CMP, `ANLUNI-` → l. unique
#   - suffixe `-DEPOT` → dépôt, `-COM*` → commission, `-DEBATS*` → hémicycle
#   - type `Promulgation_Type` → étape de promulgation au JO
#   - type `ConclusionEtapeCC_Type` → saisine Conseil constitutionnel
# Codes ignorés (redondants / non procéduraux) : AVIS-RAPPORT, CMP-DEPOT,
# DPTLETTRECT, types EtudeImpact/DepotAvisConseilEtat/ProcedureAccelere,
# préfixes AN20-/AN21- (contrôle parlementaire hors navette) et AN-APPLI-
# (rapport d'application post-promulgation).
# ---------------------------------------------------------------------------

_DOSLEG_IGNORED_XSI = {
    "EtudeImpact_Type",
    "DepotAvisConseilEtat_Type",
    "ProcedureAccelere_Type",
}

_DOSLEG_IGNORED_PREFIX = ("AN20-", "AN21-", "AN-APPLI-")
_DOSLEG_IGNORED_SUBSTR = ("AVIS-RAPPORT", "-DPTLETTRECT")


def _iter_actes(node):
    """Parcours récursif de l'arbre `actesLegislatifs` : yield chaque dict
    d'acte portant (typiquement) codeActe / libelleActe / dateActe /
    @xsi:type / uid. Supporte les deux enveloppes du dump AN :
    - {"acteLegislatif": [...] | {...}}
    - liste directe d'actes à la racine."""
    if isinstance(node, dict):
        if "acteLegislatif" in node:
            child = node["acteLegislatif"]
            if isinstance(child, list):
                for c in child:
                    yield from _iter_actes(c)
            elif isinstance(child, dict):
                yield from _iter_actes(child)
        else:
            # feuille : yield puis descente dans les enfants éventuels
            if any(k in node for k in ("codeActe", "libelleActe", "dateActe", "@xsi:type")):
                yield node
            enfants = node.get("actesLegislatifs")
            if enfants is not None:
                yield from _iter_actes(enfants)
    elif isinstance(node, list):
        for n in node:
            yield from _iter_actes(n)


def _map_code_acte(code: str, xsi_type: str) -> dict:
    """Traduit un codeActe (+ @xsi:type) en structure procédurale.

    Renvoie un dict avec :
      - ignored (bool)        : l'acte ne doit pas servir de statut
      - is_promulgation (bool)
      - is_cc (bool)          : Conseil constitutionnel
      - institution (str)     : "AN" | "Senat" | "CMP" | "Gouvernement" | "ConseilConst" | ""
      - stage (str)           : "1ère lecture", "nouv. lect.", "CMP", "promulgation"…
      - step (str)            : "dépôt", "commission", "hémicycle", ""
    """
    code = code or ""
    xsi = xsi_type or ""

    out = {"ignored": False, "is_promulgation": False, "is_cc": False,
           "institution": "", "stage": "", "step": ""}

    # Types non-étapes à ignorer pour le calcul du statut
    if xsi in _DOSLEG_IGNORED_XSI:
        out["ignored"] = True
        return out
    if any(s in code for s in _DOSLEG_IGNORED_SUBSTR):
        out["ignored"] = True
        return out
    if any(code.startswith(p) for p in _DOSLEG_IGNORED_PREFIX):
        out["ignored"] = True
        return out
    if code == "CMP-DEPOT":  # redondant avec le cycle CMP
        out["ignored"] = True
        return out

    # Promulgation / Conseil constitutionnel (identifiés par @xsi:type)
    if xsi == "Promulgation_Type" or code.startswith("PROM-"):
        out["is_promulgation"] = True
        out["institution"] = "Gouvernement"
        out["stage"] = "promulgation"
        return out
    if xsi == "ConclusionEtapeCC_Type":
        out["is_cc"] = True
        out["institution"] = "ConseilConst"
        out["stage"] = "Conseil constitutionnel"
        return out

    # Institution
    if code.startswith("AN"):
        out["institution"] = "AN"
    elif code.startswith("SN"):
        out["institution"] = "Senat"
    elif code.startswith("CMP"):
        out["institution"] = "CMP"

    # Phase de navette (stage) — ordre important : NLEC et ANLDEF avant "1-"
    if "ANLDEF-" in code:
        out["stage"] = "lecture définitive"
    elif "NLEC-" in code:
        out["stage"] = "nouvelle lecture"
    elif "ANLUNI-" in code:
        out["stage"] = "lecture unique"
    elif "CMP-" in code:
        out["stage"] = "CMP"
    elif "1-" in code:
        out["stage"] = "1ère lecture"
    elif "2-" in code:
        out["stage"] = "2ème lecture"
    elif "3-" in code:
        out["stage"] = "3ème lecture"

    # Sous-étape (step)
    if "-DEPOT" in code:
        out["step"] = "dépôt"
    elif "-COM" in code:
        out["step"] = "commission"
    elif "-DEBATS" in code:
        out["step"] = "hémicycle"

    return out


def _format_status(mapping: dict) -> str:
    """Rend un statut lisible court à partir du mapping : `AN · 1ère lecture · commission`."""
    inst = mapping.get("institution") or ""
    stage = mapping.get("stage") or ""
    step = mapping.get("step") or ""
    parts = [p for p in (inst, stage, step) if p]
    return " · ".join(parts)


# Fenêtres d'inclusion — alignées sur la mémoire projet
# (veille_parl_procedure_context.md) : on affiche seulement les dossiers
# "actifs" ou "promulgués récemment".
_DOSLEG_MAX_AGE_ACTIVE_DAYS = 365      # non-promulgués : < 12 mois
_DOSLEG_MAX_AGE_PROMULGATED_DAYS = 548  # promulgués : < 18 mois


def _normalize_dosleg(obj, src, cat):
    # Structure AN (vérifiée via scripts/diag_dosleg.py, avril 2026) :
    # la racine du fichier est {"dossierParlementaire": {...}} et le contenu
    # est DIRECTEMENT à plat (pas de sous-clé "dossier"). Les clés typiques
    # sont : uid, legislature, titreDossier.{titre,titreChemin},
    # procedureParlementaire, initiateur, actesLegislatifs. Les anciens
    # fallbacks "dossier.*" sont conservés par sécurité pour les vieux
    # fichiers ou d'éventuelles variantes.
    root = obj.get("dossierParlementaire") if isinstance(obj, dict) else None
    if not root:
        return
    uid = _text_of(_first(root, "uid", "dossier.uid", default=""))
    if not uid:
        return
    titre = _text_of(
        _first(root, "titreDossier.titre", "dossier.titreDossier.titre", default="")
    )

    # Parcours de l'arbre actesLegislatifs : on extrait, pour chaque acte,
    # (dateActe, codeActe, @xsi:type) et on applique _map_code_acte pour
    # savoir si l'acte compte comme étape procédurale. Les actes "ignorés"
    # (avis rapporteur, études d'impact, contrôle parlementaire…) ne servent
    # ni au tri date ni au statut.
    chrono = _first(root, "actesLegislatifs", "dossier.actesLegislatifs",
                     default=None)
    last_mapping: dict = {}
    last_date: datetime | None = None
    last_code: str = ""
    last_libelle: str = ""
    has_promulgation = False
    nb_actes_utiles = 0
    nb_actes_total = 0
    if isinstance(chrono, (dict, list)):
        for acte in _iter_actes(chrono):
            nb_actes_total += 1
            raw_date = acte.get("dateActe")
            if not isinstance(raw_date, str) or len(raw_date) < 10:
                continue
            dt = parse_iso(raw_date[:10])
            if not dt:
                continue
            code = str(acte.get("codeActe") or "")
            xsi = str(acte.get("@xsi:type") or "")
            mapping = _map_code_acte(code, xsi)
            if mapping["is_promulgation"]:
                has_promulgation = True
            if mapping["ignored"]:
                continue
            nb_actes_utiles += 1
            if last_date is None or dt > last_date:
                last_date = dt
                last_mapping = mapping
                last_code = code
                last_libelle = str(acte.get("libelleActe") or "")[:120]

    # Si on n'a aucun acte utile, le dossier n'a pas de "statut" exploitable
    # (typiquement : AN20-/AN21- purs, ou dossiers vides). On l'écarte.
    if last_date is None:
        return

    # Règle d'inclusion : on filtre par fraîcheur. Un dossier promulgué
    # récemment (< 18 mois) ou un dossier en navette active (< 12 mois
    # depuis le dernier acte utile) entrent dans la veille. Les autres
    # (promulgués il y a longtemps, dossiers dormants, textes caducs)
    # sont écartés — cf. ticket Cyril "un dossier de 1990 remontait".
    today = _utcnow_naive()
    age_days = (today - last_date).days
    max_age = _DOSLEG_MAX_AGE_PROMULGATED_DAYS if has_promulgation else _DOSLEG_MAX_AGE_ACTIVE_DAYS
    if age_days > max_age:
        return

    status_label = _format_status(last_mapping)

    yield Item(
        source_id=src["id"],
        uid=uid,
        category=cat,
        chamber="AN",
        title=titre or f"Dossier {uid}",
        url=f"https://www.assemblee-nationale.fr/dyn/17/dossiers/{uid}",
        published_at=last_date,
        summary=_text_of(
            _first(root, "titreDossier.titreChemin",
                    "dossier.titreDossier.titreChemin", default="")
        )[:500],
        raw={
            "path": "assemblee:dossier",
            "nb_actes": nb_actes_total,
            "nb_actes_utiles": nb_actes_utiles,
            "status_label": status_label,
            "code_acte": last_code,
            "libelle_acte": last_libelle,
            "institution": last_mapping.get("institution", ""),
            "stage": last_mapping.get("stage", ""),
            "step": last_mapping.get("step", ""),
            "is_promulgated": has_promulgation,
        },
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
    #
    # XSD officiel Question_Type (voir references/an_schemas/.../Schemas_Questions.html) :
    #   question.indexationAN.rubrique / .teteAnalyse / .analyses.analyse
    # Les anciens chemins `indexationAnalytique.*` sont conservés en fallback
    # par sécurité (variation historique, mais absents du XSD actuel).
    rubrique = _text_of(_first(root,
                                "indexationAN.rubrique",
                                "indexationAnalytique.rubrique",
                                "rubrique",
                                default=""))
    tete_analyse = _text_of(_first(root,
                                    "indexationAN.teteAnalyse",
                                    "indexationAnalytique.teteAnalyse",
                                    "teteAnalyse",
                                    default=""))
    analyse = _text_of(_first(root,
                               "indexationAN.analyses.analyse",
                               "indexationAnalytique.analyses.analyse",
                               default=""))
    # Textes : chercher dans la structure textesQuestion/texteQuestion (liste
    # ou dict selon le dumper) en utilisant _deep_find qui traverse les deux.
    texte_node = _deep_find(root.get("textesQuestion"), "texte") \
                 or _first(root, "texte", default="")
    texte = _text_of(texte_node)
    reponse_node = _deep_find(root.get("textesReponse") or root.get("textesReponses"), "texte") \
                   or _first(root, "reponse", default="")
    reponse = _text_of(reponse_node)

    # Date publication : XSD place la date dans InfoJO (texteQuestion.infoJO)
    # via un champ généralement nommé `dateJO` / `dateParution` / `date`.
    # On tape d'abord ces champs via _deep_find (traverse la liste textesQuestion).
    date_raw = _deep_find(root.get("textesQuestion"),
                           "dateJO", "dateParution", "dateJORF", "date") \
               or _first(root,
                          "cloture.dateCloture",
                          "questionDate", "dateQuestion", "dateDepot",
                          default=None)
    date_pub = parse_iso(_text_of(date_raw) if date_raw else None)

    # Auteur — XSD : question.auteur n'expose QUE acteurRef + mandatRef (+ groupe).
    # Les champs nom/prénom/civ existent dans le dump AMO Acteurs, pas ici.
    # On garde les anciens chemins en fallback (si un jour la source les inclut),
    # et on formate l'acteurRef proprement quand on tombe dessus.
    auteur_nom = _text_of(_first(root,
                                  "auteur.identite.nom",
                                  "auteur.identite.nomFamille",
                                  default=""))
    auteur_prenom = _text_of(_first(root,
                                     "auteur.identite.prenom",
                                     default=""))
    auteur_civilite = _text_of(_first(root, "auteur.identite.civ", default=""))
    auteur_ref = _text_of(_first(root,
                                  "auteur.identite.acteurRef",
                                  "auteur.acteurRef",
                                  default=""))
    # Groupe : XSD dit `auteur.groupe` est un Groupe_type (abrege/developpe).
    auteur_groupe = _text_of(_first(root,
                                     "auteur.groupe.abrege",
                                     "auteur.groupe.developpe",
                                     "auteur.groupePolitiqueRef",
                                     default=""))
    auteur_label = " ".join(x for x in [auteur_civilite, auteur_prenom, auteur_nom] if x).strip()
    if not auteur_label:
        # Pas de nom dans le JSON (normal vu le XSD) — on affiche l'acteurRef
        # préfixé "Député" pour que ce soit lisible. Remplacé par le vrai nom
        # quand le loader AMO sera en place (task #6).
        auteur_label = f"Député {auteur_ref}" if auteur_ref else "Auteur"

    # Ministère : XSD → `minInt` (TexteAbregeable_type, abrege+developpe).
    ministere = _text_of(_first(root,
                                 "minInt.abrege",
                                 "minInt.developpe",
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
        "QM": "Question au ministre",
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

    # Titre : on garde l'objet de la réunion, lisible tel quel
    # (style Follaw). Date et lieu sont exposés dans la méta (pas dans
    # le titre) et rendus comme texte simple sans lien hypertexte.
    title = (titre or "Réunion")[:220]

    structured = " — ".join(p for p in [
        f"Organe : {organe}" if organe else "",
        f"Lieu : {lieu}" if lieu else "",
        odj_text,
    ] if p)
    # Shotgun : filet de sécurité au cas où la structure du JSON
    # réunion ne colle pas à nos paths ciblés.
    shotgun = _all_text(root)
    summary = (structured + " — " + shotgun if structured else shotgun)[:2000]

    # URL : il n'existe pas d'URL publique stable par UID de réunion
    # (testé : /dyn/17/reunions/{uid} 404, idem /reunion/{uid}, /agenda/{uid}).
    # On laisse une URL vide — à l'affichage, le titre est rendu en clair
    # (pas de lien cliquable) pour rester aligné sur Follaw.
    yield Item(
        source_id=src["id"],
        uid=uid,
        category=cat,
        chamber="AN",
        title=title,
        url="",
        published_at=dt,
        summary=summary,
        raw={"path": "assemblee:reunion", "organe": organe, "lieu": lieu},
    )
