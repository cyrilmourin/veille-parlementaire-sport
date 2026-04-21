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
from .. import amo_loader
from ._common import (
    extract_cr_theme,
    fetch_bytes,
    fetch_bytes_heavy,
    parse_iso,
    unzip_members,
    unzip_members_since,
)

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


def _strip_html_text(node) -> str:
    """Extrait le texte brut d'un nœud XHTML/XML parsé en JSON.

    Les éléments `corps.dispositif` et `corps.exposeSommaire` des amendements
    AN sont typés `TexteNonVide_Type` (XHTML). Le JSON dumper les rend sous
    forme d'arbre : `{"#text": "...", "p": [{"#text": "..."}, …]}`. Le
    ancien `_text_of` tombait sur `json.dumps()` et perdait le texte réel
    dans du markup — donc aucun mot-clé sport ne ressortait.

    Cette fonction walke récursivement l'arbre, ignore les attributs
    XML (clés `@xxx`) et concatène tous les `#text` + strings feuilles.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, list):
        return " ".join(_strip_html_text(it) for it in node if it).strip()
    if isinstance(node, dict):
        parts: list[str] = []
        # 1) contenu textuel direct du nœud
        if "#text" in node:
            parts.append(str(node["#text"]).strip())
        # 2) enfants (hors attributs XML `@xxx` et hors #text déjà lu)
        for k, v in node.items():
            if k == "#text" or (isinstance(k, str) and k.startswith("@")):
                continue
            t = _strip_html_text(v)
            if t:
                parts.append(t)
        return " ".join(p for p in parts if p).strip()
    return str(node).strip()


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


# Patterns de bruit techniques à retirer du shotgun agenda avant affichage.
# Gardés hors fonction pour compilation unique.
_AGENDA_NOISE_PATTERNS = [
    re.compile(r"\bPA\d{5,7}\b(?:\s+(?:absent|pr[ée]sent|excus[ée]))?"),
    re.compile(r"\bPO\d{5,7}\b"),
    re.compile(r"\b(?:RUANR|SLAN|PRANR|SEANR|TAANR)\w+\b"),  # UIDs réunions/salles/séances AN
    re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?\b"),
    re.compile(r"https?://\S+"),
    re.compile(r"\b\w+_type\b"),  # xsi:type markers (reunionCommission_type, etc.)
    re.compile(r"\b(?:true|false)\b", re.IGNORECASE),
]


def _clean_agenda_shotgun(text: str) -> str:
    """Filtre le shotgun agenda pour ne garder que le contenu sémantique.

    Retire les listes de présence (PAxxxxxx absent…), les UIDs techniques,
    timestamps, URIs et marqueurs de schéma. Garde les titres d'ODJ,
    noms de personnes entendues et thèmes — utiles pour l'extrait phrase
    et pour le matching mots-clés.
    """
    if not text:
        return ""
    for pat in _AGENDA_NOISE_PATTERNS:
        text = pat.sub(" ", text)
    # Collapse espaces + retire tokens d'une lettre orphelins
    text = re.sub(r"\s+", " ", text).strip()
    return text


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

# cr_ref AN : identifiant canonique d'un compte rendu de séance.
# Exemple : CRSANR5L17S2026O1N039 → /dyn/17/comptes-rendus/seance/CRSANR5L17S2026O1N039
# Chercher dans le nom de fichier OU dans le contenu (balise xml <idCR>).
_CR_REF_RE = re.compile(r"CRSAN[A-Z0-9]{5,30}", re.IGNORECASE)

# Syceron : en-tête du texte stripé contient un timestamp compacté (
# <timeStampDebut>20250709150000000</timeStampDebut> → AAAAMMJJHHMMSSsss).
# On matche 8 chiffres date + 9 chiffres heure. Plus fiable que ZipInfo
# (le zip Syceron est recompressé à chaque publication → dates toutes ~today).
_SYCERON_TS_RE = re.compile(r"\b(20\d{2})(\d{2})(\d{2})\d{9}\b")
# Thème : après « Présidence de <civ> <prenom> <nom> », la première ligne
# contient l'objet de la séance, délimité par « 0 » (séparateur Syceron).
_SYCERON_THEME_RE = re.compile(
    r"Présidence\s+de\s+(?:M\.|Mme|Mlle)\s+\S+\s+\S+\s+(.+?)\s+0\s",
    re.IGNORECASE | re.DOTALL,
)


def _extract_syceron_meta(text: str) -> tuple[datetime | None, str]:
    """Depuis le texte stripé d'un CR Syceron, renvoie (datetime séance, thème).

    La date vient du timeStampDebut (champ technique, toujours présent).
    Le thème vient de la ligne « Présidence de <nom> <OBJET> 0 … » (très
    régulier sur tout le corpus 2024+).
    """
    sample = text[:4000]
    dt_out: datetime | None = None
    m_d = _SYCERON_TS_RE.search(sample)
    if m_d:
        try:
            dt_out = datetime(int(m_d.group(1)), int(m_d.group(2)), int(m_d.group(3)))
        except ValueError:
            dt_out = None
    theme = ""
    m_t = _SYCERON_THEME_RE.search(sample)
    if m_t:
        theme = _WS_RE.sub(" ", m_t.group(1)).strip(" .,;:—-")
        if len(theme) > 130:
            theme = theme[:130].rsplit(" ", 1)[0] + "…"
    return dt_out, theme


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
    # Syceron AN ~ XML zip 80+ Mo : retry lourd + read 120s.
    data = fetch_bytes_heavy(src["url"])

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

        # Date séance : priorité aux métadonnées Syceron (timeStampDebut),
        # puis date YYYYMMDD dans le nom de fichier (autres dumps xml_zip
        # éventuels), puis ZipInfo en dernier recours.
        # NB: ZipInfo.date_time vaut la date de recompression du dump
        # Syceron (~aujourd'hui), pas la séance — donc inutilisable seul.
        ts_dt, syceron_theme = _extract_syceron_meta(text)
        published_at = ts_dt or dt
        if not ts_dt:
            m = _DATE_IN_NAME_RE.search(name)
            if m:
                try:
                    published_at = datetime(
                        int(m.group(1)), int(m.group(2)), int(m.group(3)),
                    )
                except ValueError:
                    pass

        # UID stable basé sur (source_id, nom_fichier)
        uid = hashlib.sha1(f"{sid}:{name}".encode()).hexdigest()[:16]

        # cr_ref : identifiant canonique du CR (ex. CRSANR5L17S2026O1N039).
        # On le cherche d'abord dans le nom de fichier, puis en fallback dans
        # le texte brut (balise propriétaire <idCR>CRSAN…</idCR>).
        base = os.path.basename(name).rsplit(".", 1)[0]
        m_cr = _CR_REF_RE.search(base) or _CR_REF_RE.search(raw[:5000])
        cr_ref = m_cr.group(0).upper() if m_cr else ""

        # URL : si on a un cr_ref, on pointe vers la page CR dédiée
        # (/dyn/17/comptes-rendus/seance/{cr_ref}) ; sinon fallback liste
        # des séances du mandat (toujours 200). L'ancienne URL
        # /dyn/17/seances est en 404.
        if cr_ref:
            url = f"https://www.assemblee-nationale.fr/dyn/17/comptes-rendus/seance/{cr_ref}"
        else:
            url = "https://www.assemblee-nationale.fr/dyn/17/comptes-rendus"

        # Titre : on veut évoquer le thème du débat. Ordre de priorité :
        #   1) thème Syceron (« Présidence de … <OBJET> 0 … ») — fiable
        #   2) thème générique extract_cr_theme (motifs « projet de loi… »)
        #   3) fallback date + mention CR intégral
        theme = syceron_theme or extract_cr_theme(text)
        # Indicateur : la date vient-elle bien du timestamp et pas du Zip ?
        date_is_from_seance = ts_dt is not None
        if date_is_from_seance and theme:
            title = f"Séance AN du {published_at:%d/%m/%Y} — {theme}"[:220]
        elif date_is_from_seance:
            title = f"Séance AN du {published_at:%d/%m/%Y} — Compte rendu intégral"[:220]
        elif theme:
            title = f"Séance AN — {theme}"[:220]
        elif cr_ref:
            title = f"Compte rendu AN — séance {cr_ref}"[:220]
        else:
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
            url=url,
            published_at=published_at,
            summary=summary,
            raw={
                "path": "assemblee:syceron",
                "fichier": name,
                "taille": len(payload),
                "cr_ref": cr_ref,
                # Exposé au template comptes_rendus/list.html pour le badge
                # de type (AN = intégral par défaut).
                "report_type": "integral",
                "report_label": "Compte rendu intégral",
                "theme": theme,
                # Date officielle de séance (depuis timeStampDebut du XML).
                # Consommée par _fix_cr_row : permet au rebuild du site
                # d'écrire le titre correct même pour les items déjà en DB.
                "seance_date_iso": (
                    ts_dt.date().isoformat() if ts_dt else ""
                ),
            },
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
    # Dumps JSON AN agrégés (amendements, questions, agenda, dossiers) :
    # 50-200 Mo. Retry lourd + read 120s pour ne pas timeout sur les gros.
    data = fetch_bytes_heavy(src["url"])
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

    # R11b — Flush du cache `texteLegislatifRef → dossier_title` après la
    # passe dossiers. Le fichier écrit sera lu par `_normalize_amendement`
    # via `amo_loader.resolve_texte_dossier`. On purge ensuite l'accumulateur
    # pour que les runs suivants (tests, multi-fetch) repartent propres.
    if src["id"] == "an_dossiers_legislatifs" and _TEXTE_TO_DOSSIER_ACCUM:
        try:
            amo_loader.write_texte_dossier_cache(_TEXTE_TO_DOSSIER_ACCUM)
        except Exception as e:
            log.warning("Flush cache texte→dossier KO : %s", e)
        _TEXTE_TO_DOSSIER_ACCUM.clear()

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
    # Numéro court d'amendement (ex : "233") — validé sur JSON unitaire AN
    # `/dyn/opendata/<uid>.json`, avril 2026. La clé réelle du JSON AN est
    # `identification` (ex: {numeroLong, numeroOrdreDepot, numeroRect,
    # prefixeOrganeExamen}), pas `identifiant` (legacy 404 — l'ancien path
    # n'existait plus, donc `uid` remontait le UID technique "AMANR5L17…"
    # et produisait des titres illisibles type "Amendement n°AMANR5L17…").
    num = _text_of(_first(root, "identification.numeroLong",
                           "identification.numeroOrdreDepot",
                           "identifiant.numero",  # fallback historique
                           default=""))
    uid_tech = _text_of(_first(root, "uid", default=""))
    if not num and not uid_tech:
        return
    # Si seul l'UID technique est présent (edge-case), on l'utilise ; sinon
    # on préfère le numéro court pour l'affichage.
    num = num or uid_tech

    # Auteur : acteurRef (identifiant PA…) — path réel confirmé :
    # signataires.auteur.acteurRef. Les paths `.nom` / `.prenom` / `.identite.*`
    # N'EXISTENT PAS dans le JSON AN — tous les noms sont résolus via AMO.
    auteur_ref = _text_of(_first(root, "signataires.auteur.acteurRef",
                                  default=""))
    auteur_groupe = _text_of(_first(root, "signataires.auteur.groupePolitiqueRef",
                                     default=""))
    auteur_label = ""
    # On résout systématiquement via le cache AMO (data/amo_resolved.json) :
    # civ + prenom + nom compilé depuis AMO_Acteurs.json.
    if auteur_ref:
        resolved = amo_loader.resolve_acteur(auteur_ref)
        if resolved:
            auteur_label = resolved
        if not auteur_groupe:
            auteur_groupe = amo_loader.resolve_groupe(auteur_ref) or ""
    if not auteur_label:
        auteur_label = f"Député {auteur_ref}" if auteur_ref else "Auteur inconnu"
    # Résout aussi le groupe si on n'a qu'un POxxx
    if auteur_groupe and auteur_groupe.startswith("PO"):
        groupe_lib = amo_loader.resolve_organe(auteur_groupe, prefer_long=False)
        if groupe_lib:
            auteur_groupe = groupe_lib

    # Dispositif + exposé sommaire (matériel pertinent pour matching mots-clés).
    # Paths réels validés sur JSON unitaire AN : `corps.contenuAuteur.dispositif`
    # et `corps.contenuAuteur.exposeSommaire` (pas `corps.dispositif` — ce
    # chemin ne résolvait jamais, donc dispositif/exposé étaient VIDES et
    # le matcher mots-clés n'avait rien à analyser → 0 match sur 5683 records).
    # Ces champs sont typés `TexteNonVide_Type` (XHTML) — _strip_html_text
    # extrait le texte brut des arbres {"#text": "...", "p": [...]}.
    dispo = _strip_html_text(_first(root,
                                      "corps.contenuAuteur.dispositif",
                                      "corps.dispositif",  # fallback legacy
                                      default=""))
    expose = _strip_html_text(_first(root,
                                       "corps.contenuAuteur.exposeSommaire",
                                       "corps.exposeSommaire",  # fallback legacy
                                       default=""))

    # Statut : sort en séance / en commission / "Irrecevable".
    # Sur beaucoup d'amendements en cours de traitement, `cycleDeVie.sort`
    # est un dict VIDE — la valeur utile est sur `etatDesTraitements.etat.libelle`
    # (ex : "En traitement", "Tombe"). On privilégie `sort.libelle` (valeur
    # finale après séance) puis `etatDesTraitements.etat.libelle` en fallback.
    statut = _strip_html_text(_first(
        root,
        "cycleDeVie.sort.libelle",
        "cycleDeVie.sort.sortEnSeance",
        "cycleDeVie.etatDesTraitements.etat.libelle",
        "cycleDeVie.etatDesTraitements.etat",
        "etat", "cycleDeVie.etat",
        default=""
    )) or ""

    # Contexte dossier (article, division)
    article = _text_of(_first(root, "pointeurFragmentTexte.division.articleDesignation",
                               "pointeurFragmentTexte.article.numeroCorrection",
                               default=""))
    # Référence au texte législatif parent (ex : "PIONANR5L17BTC2335").
    # Le titre humain du dossier est résolu via le cache an_texte_to_dossier
    # (construit en pré-pass par _normalize_dosleg, voir R11b) — essentiel
    # pour que les mots-clés du titre du dossier parent (ex : "JO 2024",
    # "sport", "clubs sportifs") ressortent dans le haystack du matcher.
    texte_ref = _text_of(_first(root, "texteLegislatifRef", default=""))
    dossier_titre = ""
    if texte_ref:
        dossier_titre = amo_loader.resolve_texte_dossier(texte_ref) or ""

    # Summary ciblé : on va DIRECTEMENT au contenu utile pour le matching
    # (dispositif + exposé sommaire) en les mettant en tête. Le shotgun
    # `_all_text(root)` a été retiré : il visitait le JSON dans l'ordre
    # d'apparition, donc la liste des co-signataires (PA795228 PA793262…)
    # et leurs noms résolus (Mme Hamdane, M. Bernalicis…) consommaient les
    # ~1500 premiers caractères et coupaient avant `corps.dispositif`.
    # Résultat : aucun mot-clé sport ne ressortait même sur des amendements
    # manifestement thématiques. En ciblant `corps.dispositif` et
    # `corps.exposeSommaire` — typés TexteNonVide_Type / XHTML par le XSD
    # officiel AN — le matcher voit enfin le vrai contenu.
    #
    # Ordre : exposé (plus riche, prose explicative) en premier pour
    # maximiser la chance de match dans les 2000 premiers caractères ;
    # dispositif (souvent plus technique) ensuite ; métadonnées
    # auteur/statut/article en queue (utile à l'affichage, non au match).
    # Ordre : (1) dossier_titre EN TÊTE — le titre du dossier parent contient
    # souvent les mots-clés thématiques (ex : "sécurité des JO 2024",
    # "mineurs réseaux sociaux") que l'amendement ne répète pas mais sur
    # lesquels Follaw matche. (2) exposé (prose, riche). (3) dispositif
    # (technique). (4) métadonnées auteur/statut/article en queue.
    summary_parts = [
        f"Dossier : {dossier_titre}" if dossier_titre else "",
        expose,
        dispo,
        f"Auteur : {auteur_label}" if auteur_label else "",
        f"Statut : {statut}" if statut else "",
        f"Article : {article}" if article else "",
    ]
    summary = " — ".join(p for p in summary_parts if p).strip()[:2000]

    # Titre compact et informatif — on inclut le libellé court du dossier
    # parent pour que le matcher mots-clés (qui regarde aussi `title`) ait
    # accès au sujet du dossier depuis le titre lui-même, et pour que
    # l'utilisateur voie le contexte dans la liste des amendements.
    # R13-G (2026-04-21) : "Amdt" au lieu de "Amendement" — titre plus court
    # et lisible sur la page d'accueil (la chambre est déjà affichée via le
    # tag .chamber, et le contexte catégorie est évident sur /items/amendements/).
    title_bits = [f"Amdt n°{num}"]
    if statut:
        title_bits.append(f"[{statut}]")
    title_bits.append(f"— {auteur_label}")
    if auteur_groupe and not auteur_groupe.startswith("PO"):
        title_bits.append(f"({auteur_groupe})")
    if article:
        title_bits.append(f"· art. {article}")
    if dossier_titre:
        # Tronqué pour ne pas exploser title[:220]
        title_bits.append(f"· sur « {dossier_titre[:80]} »")
    title = " ".join(title_bits)[:220]

    # URL : on préfère le UID technique (unique) au numéro court (unique
    # seulement par dossier). L'URL publique AN accepte les deux mais seul
    # le UID technique garantit un hit direct sur l'amendement.
    uid_for_url = uid_tech or num

    yield Item(
        source_id=src["id"],
        uid=str(uid_tech or num),
        category=cat,
        chamber="AN",
        title=title,
        url=f"https://www.assemblee-nationale.fr/dyn/17/amendements/{uid_for_url}",
        published_at=parse_iso(_first(root, "cycleDeVie.dateDepot",
                                       "cycleDeVie.dateSaisie", default=None)),
        summary=summary,
        raw={"path": "assemblee:amendement", "auteur_ref": auteur_ref,
             "groupe": auteur_groupe, "dossier": dossier_titre,
             "texte_ref": texte_ref, "statut": statut, "numero": num},
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

# Accumulateur du cache `texteLegislatifRef → dossier_title`, rempli par
# `_normalize_dosleg` au fil de l'itération sur le dump `Dossiers_Legislatifs.json.zip`,
# puis flushé par `fetch_source` à la fin de la passe dossiers. Utilisé par
# `_normalize_amendement` via `amo_loader.resolve_texte_dossier` — essentiel
# pour que le titre du dossier parent (ex : "Sécurité des JO 2024") figure
# dans le haystack matching des amendements (R11b).
_TEXTE_TO_DOSSIER_ACCUM: dict[str, str] = {}

# Pattern d'identifiant de texte législatif AN (préfixes validés via JSON
# unitaire `/dyn/opendata/<uid>.json`, avril 2026) :
#   PION* (Proposition d'Initiative Origine Non-adoptée)
#   PRJL* (Projet de loi)
#   PPL*  (Proposition de loi)
#   TA*   (Texte Adopté)
# Format complet : <prefix><chambre><chrono>, ex "PIONANR5L17BTC2335".
_TEXTE_REF_RE = re.compile(r"^(?:PION|PRJL|PPL|TA)[A-Z0-9]{8,}$")


def _harvest_texte_refs(node, title: str, accum: dict[str, str]) -> None:
    """Walk récursif : collecte tous les `texteLegislatifRef`-like qu'on voit
    passer dans l'arbre du dossier et les mappe vers `title`.

    Les textes législatifs AN sont référencés dans les actes via plusieurs
    clés selon le codeActe (`refTexteAssocie`, `texteAssocie`, leaf strings
    directes dans les actes de dépôt). Plutôt que de s'appuyer sur un schéma
    XSD précis — susceptible de varier entre actes —, on parcourt l'arbre
    et on harvest toute chaîne matchant `_TEXTE_REF_RE` (PION*, PRJL*, PPL*,
    TA*). Faux positifs très improbables : les autres UIDs AN commencent
    par AM, DLR, PA, PO, TI, RU, SE, etc.
    """
    if isinstance(node, str):
        if _TEXTE_REF_RE.match(node):
            # Premier mapping gagne : on privilégie la 1ère occurrence (texte
            # initial) plutôt que les mises à jour ultérieures d'un même
            # texte qui pourraient se référer au dossier enfant.
            accum.setdefault(node, title)
        return
    if isinstance(node, dict):
        for v in node.values():
            _harvest_texte_refs(v, title, accum)
    elif isinstance(node, list):
        for v in node:
            _harvest_texte_refs(v, title, accum)


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

    # R11b — Harvest des références de textes législatifs du dossier courant
    # vers l'accumulateur global. On les mappe au titre du dossier pour
    # enrichir le haystack de matching des amendements (qui référencent ces
    # textes via `texteLegislatifRef`). Appelé AVANT le filtre date/statut
    # pour que même les dossiers écartés de l'affichage alimentent le cache
    # — un amendement peut se référer à un texte d'un dossier promulgué
    # ancien et son titre reste utile au matching.
    if titre:
        _harvest_texte_refs(root, titre, _TEXTE_TO_DOSSIER_ACCUM)

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
    # Timeline complète des actes utiles — sert à la maquette "façon AN"
    # sur /items/dossiers_legislatifs/<slug>/ (affichage chronologique
    # des étapes procédurales avec leurs dates).
    actes_timeline: list[dict] = []
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
            libelle_acte = str(acte.get("libelleActe") or "")[:180]
            actes_timeline.append({
                "date": dt.date().isoformat(),
                "code": code,
                "libelle": libelle_acte,
                "institution": mapping.get("institution", ""),
                "stage": mapping.get("stage", ""),
                "step": mapping.get("step", ""),
                "is_promulgation": bool(mapping.get("is_promulgation")),
            })
            if last_date is None or dt > last_date:
                last_date = dt
                last_mapping = mapping
                last_code = code
                last_libelle = libelle_acte[:120]
    # Tri chronologique ascendant (dépôt → promulgation, comme la page AN).
    actes_timeline.sort(key=lambda a: a["date"])

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
            # Timeline pour la maquette AN-like — borner à 40 étapes pour
            # garder le JSON raisonnable (certains dossiers ont 70+ actes).
            "actes_timeline": actes_timeline[-40:],
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
    if not auteur_label and auteur_ref:
        # Résolution via cache AMO : PAxxx → "Mme Marie Dupont"
        resolved = amo_loader.resolve_acteur(auteur_ref)
        if resolved:
            auteur_label = resolved
            if not auteur_groupe:
                auteur_groupe = amo_loader.resolve_groupe(auteur_ref)
    if not auteur_label:
        auteur_label = f"Député {auteur_ref}" if auteur_ref else "Auteur"
    # Résout le groupe si c'est un POxxx (groupePolitiqueRef brut)
    if auteur_groupe and auteur_groupe.startswith("PO"):
        groupe_lib = amo_loader.resolve_organe(auteur_groupe, prefer_long=False)
        if groupe_lib:
            auteur_groupe = groupe_lib

    # Ministère : XSD → `minInt` (TexteAbregeable_type, abrege+developpe).
    ministere = _text_of(_first(root,
                                 "minInt.abrege",
                                 "minInt.developpe",
                                 "ministereAttributaire.intitule",
                                 default=""))

    # Construction du titre :
    # "Question écrite · 12/04/2026 — Mme Hervieu (LFI-NFP) : sport santé"
    # Choix demandé par l'utilisateur : nature + date + auteur (+groupe) +
    # sujet, SANS ministère (l'info ministère reste dans le summary pour le
    # matching et la consultation détaillée).
    # R13-G (2026-04-21) : Cyril veut l'analyse en premier — plus spécifique
    # que la rubrique ("sports : nautiques") ou la tête d'analyse. L'analyse
    # donne "Réforme de l'organisation du sport à l'école" là où la rubrique
    # dirait juste "sports". Ordre : analyse > teteAnalyse > rubrique > texte.
    sujet_court = (analyse or tete_analyse or rubrique).strip()
    if not sujet_court:
        sujet_court = _first_sentence(texte, max_len=100)
    sujet_court = sujet_court or "Question"
    m_uid = _Q_UID_RE.search(uid)
    qtype_label = {
        "QE": "Question écrite",
        "QG": "Question au gouvernement",
        "QOSD": "Question orale",
        "QST": "Question orale",
        "QM": "Question au ministre",
    }.get((m_uid.group(2).upper() if m_uid else ""), "Question")

    date_label = ""
    if date_pub:
        try:
            date_label = date_pub.strftime("%d/%m/%Y")
        except Exception:
            date_label = ""

    title_bits = [qtype_label]
    if date_label:
        title_bits.append(f"· {date_label}")
    title_bits.append(f"— {auteur_label}")
    if auteur_groupe:
        title_bits.append(f"({auteur_groupe})")
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

    # URL fiche député AN — format moderne dyn/ (redirect automatique
    # vers l'ancienne fiche si archivée). Seulement si acteurRef = PAxxx.
    auteur_url = ""
    if auteur_ref and auteur_ref.startswith("PA") and auteur_ref[2:].isdigit():
        auteur_url = f"https://www.assemblee-nationale.fr/dyn/deputes/{auteur_ref}"

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
             "auteur_url": auteur_url,
             # R13-G : stockés pour fixup in-memory côté site_export si on
             # modifie la règle de priorité (analyse > rubrique vs inverse).
             "analyse": analyse, "tete_analyse": tete_analyse,
             "rubrique": rubrique,
             "path": "assemblee:question"},
    )


# --- Agenda AN : helpers pour extraire un titre lisible depuis la structure
# JSON. Source : XSD AN 0.9.8 (/Schemas_Entites/VieAN/Schemas_AgendaParlementaire.xsd).
# Clés en camelCase avec S majuscule : `timeStampDebut`, `timeStampFin`,
# `libelleLong` / `libelleCourt` pour `lieu`, `@xsi:type` pour distinguer
# seance_type / reunionCommission_type / reunionBase_type.

# Préfixes d'identifiants AN à écarter comme "titre" (ce sont des codes,
# pas des libellés). POxxxx = organe, PAxxxx = acteur, RUANRxxx = réunion,
# SLANxxx = salle, CRSANxxx = compte rendu, DLR*L17Nxxx = dossier lég.,
# PTxxxx = point ODJ, CTAxxxx = code thème, podj*_type = sous-type XSD ODJ.
_AGENDA_ID_RE = re.compile(
    r"^\s*("
    r"PO[A-Z0-9]+|PA[A-Z0-9]+|RU[A-Z0-9]+|SL[A-Z0-9]+|CR[A-Z0-9]+|"
    r"DLR[A-Z0-9]+|PT[A-Z0-9]+|CTA[A-Z0-9]+|ODJ[A-Z]*|"
    r"podj\w*_type|\w+_type|"
    r"\d{2,}|[A-Z]{2,}\d+"
    r")\s*$",
)

# Chaînes de bruit : statuts, états de participation, booléens, adjectifs
# ordinaux de session, valeurs xsi:type, codes de rôle.
_AGENDA_NOISE = {
    "confirmé", "confirme", "reporté", "reporte", "annulé", "annule",
    "ordinaire", "extraordinaire", "première", "premiere", "deuxième",
    "deuxieme", "troisième", "troisieme", "quatrième", "quatrieme",
    "présent", "present", "absent", "excusé", "excuse",
    "true", "false", "null", "none",
    "ouverturepresse", "ouverture presse",
    "assemblée nationale", "assemblee nationale",
    "oui", "non",
    # Chaînes de namespace / schéma qui apparaissent dans le shotgun
    "http://schemas.assemblee-nationale.fr/referentiel",
    "http://www.w3.org/2001/xmlschema-instance",
}

# Clés JSON prioritaires pour le titre d'un point ODJ ou d'une réunion.
# L'ordre reflète la préférence (plus haut = meilleur candidat).
_AGENDA_TITLE_KEYS = [
    "titreODJ", "titreOrdreDuJour", "libelleOrdreDuJour",
    "libelleObjet", "titreReunion", "intitule",
    "objet", "libelle", "libelleLong",
]


def _is_agenda_title_candidate(s) -> bool:
    """Heuristique : true si `s` ressemble à un libellé lisible d'ODJ."""
    if not isinstance(s, str):
        return False
    t = s.strip()
    if len(t) < 15 or len(t) > 400:
        return False
    low = t.lower()
    if low in _AGENDA_NOISE:
        return False
    if _AGENDA_ID_RE.match(t):
        return False
    # Pas une date ISO pure
    if re.match(r"^\d{4}-\d{2}-\d{2}(T|$)", t):
        return False
    # Doit contenir au moins un espace (vrai texte, pas un code collé)
    if " " not in t:
        return False
    # Doit contenir au moins une lettre minuscule (les codes sont MAJ)
    if not any(c.islower() for c in t):
        return False
    return True


def _collect_agenda_titles(root) -> list[str]:
    """Collecte les libellés lisibles dans l'arbre JSON d'une réunion.

    Retourne une liste dédupliquée, ordonnée par priorité de clé :
    `titreODJ` > `libelleObjet` > `objet` > `libelle` > autres.
    """
    prioritized: dict[int, list[str]] = {}
    seen: set[str] = set()
    stack = [(root, None)]
    while stack:
        cur, parent_key = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(v, str):
                    if _is_agenda_title_candidate(v):
                        t = v.strip()
                        if t in seen:
                            continue
                        seen.add(t)
                        try:
                            prio = _AGENDA_TITLE_KEYS.index(k)
                        except ValueError:
                            prio = len(_AGENDA_TITLE_KEYS) + 10
                        prioritized.setdefault(prio, []).append(t)
                elif isinstance(v, (dict, list)):
                    stack.append((v, k))
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    stack.append((v, parent_key))
    result: list[str] = []
    for prio in sorted(prioritized):
        result.extend(prioritized[prio])
    return result


def _agenda_url(uid: str, xsi_type: str, dt, cr_ref: str = "",
                 organe_ref: str = "") -> str:
    """Construit une URL publique AN stable pour un item d'agenda.

    Priorité (du + spécifique au + générique) :
    1. Séance avec idCR → page du compte rendu de séance (lien le + utile).
    2. Réunion de commission (organe_ref connu) → page agenda de la commission
       filtrée par jour — le portail AN accepte `#commission-{organe}/jour-{d}`.
    3. Date connue → ancre jour dans l'agenda global.
    4. Dernier recours → agenda global.

    Avant ce patch, toute réunion sans idCR retombait direct sur le cas 4 —
    tous les items d'agenda pointaient alors vers la MÊME URL générique,
    donnant l'impression d'items statiques ("rien ne change, liens morts").
    """
    # 1. Séance avec compte rendu : CRSANR5L17S2026O1N039 → page CR séance
    #    URL canonique : /dyn/17/comptes-rendus/seance/{cr_ref}
    if "seance" in xsi_type and cr_ref:
        return f"https://www.assemblee-nationale.fr/dyn/17/comptes-rendus/seance/{cr_ref}"
    # 2. Réunion rattachée à un organe connu (commission, délégation…) :
    #    on peut cibler l'agenda de CET organe au lieu de l'agenda global.
    #    Plus ciblé = distinct par réunion = clic utile côté lecteur.
    if organe_ref and dt is not None:
        return (
            "https://www.assemblee-nationale.fr/dyn/agendas-parlementaires/"
            f"agenda-commissions#{organe_ref}/jour-{dt.date().isoformat()}"
        )
    if organe_ref:
        return (
            "https://www.assemblee-nationale.fr/dyn/agendas-parlementaires/"
            f"agenda-commissions#{organe_ref}"
        )
    # 3. Date connue sans organe : ancre jour dans l'agenda global.
    if dt is not None:
        return (
            "https://www.assemblee-nationale.fr/dyn/agendas-parlementaires/agenda-an"
            f"#jour-{dt.date().isoformat()}"
        )
    # 4. Dernier recours : agenda global.
    return "https://www.assemblee-nationale.fr/dyn/agendas-parlementaires/agenda-an"


def _normalize_agenda(obj, src, cat):
    root = obj.get("reunion") if isinstance(obj, dict) else None
    if not root:
        return
    uid = _text_of(_first(root, "uid", default=""))
    if not uid:
        return

    # --- Type de réunion : attribut XML xsi:type → clé JSON variable
    # selon convertisseur (`@xsi:type`, `xsi:type`, `xsiType`).
    xsi_type = _text_of(_first(
        root, "@xsi:type", "xsi:type", "xsiType", "type", default=""
    )).lower()
    # Fallback : retrouver *_type dans le shotgun si aucune clé directe.
    if not xsi_type:
        m = re.search(r"\b(seance_type|reunion[a-z]*_type|podj[a-z]*_type)\b",
                      _all_text(root), re.IGNORECASE)
        if m:
            xsi_type = m.group(1).lower()

    is_seance = "seance_type" in xsi_type
    is_commission = "commission" in xsi_type

    # --- DATE : le XSD AN définit `timeStampDebut` (S majuscule).
    # On essaie les variantes + fallback deep-find + SeanceID.DateSeance.
    dt_raw = _first(
        root,
        "timeStampDebut", "timestampDebut",
        "timeStampDebutReunion", "timestampDebutReunion",
        "dateReunion", default=None,
    )
    if dt_raw in (None, ""):
        dt_raw = _deep_find(root, "timeStampDebut", "timestampDebut",
                            "DateSeance", "dateSeance")
    dt = parse_iso(_text_of(dt_raw)) if dt_raw else None

    # --- LIEU : lieuAN_type = {code, libelleCourt, libelleLong}.
    lieu = _text_of(_first(
        root, "lieu.libelleLong", "lieu.libelleCourt",
        default="",
    ))
    if not lieu:
        lieu = _text_of(_deep_find(root, "libelleLong") or "")
    # Rejeter les codes purs type 'SLANPBS6351' passés par erreur
    if lieu and _AGENDA_ID_RE.match(lieu):
        lieu = ""

    # --- ORGANE (IdOrgane_type, ex. PO838901) : gardé en raw + résolu
    organe_ref = _text_of(_first(root, "organeReuniRef", "organeRef",
                                   default=""))
    if not organe_ref:
        organe_ref = _text_of(_deep_find(root, "organeReuniRef",
                                          "organeRef") or "")
    organe_label = amo_loader.resolve_organe(organe_ref) if organe_ref else ""

    # --- COMPTE RENDU DE SÉANCE (référence externe pour l'URL)
    cr_ref = ""
    if is_seance:
        cr_ref = _text_of(_deep_find(root, "compteRenduRef", "idCompteRendu",
                                      "idCR") or "")

    # --- TITRE : libellé d'ODJ ou d'audition, filtré du bruit.
    titles = _collect_agenda_titles(root)
    main_title = titles[0] if titles else ""

    if is_seance:
        quant = _text_of(_deep_find(root, "quantieme") or "")
        num_jo = _text_of(_deep_find(root, "numSeanceJO", "idJO") or "")
        prefix = "Séance"
        if quant:
            prefix = f"{quant.capitalize()} séance"
        if num_jo:
            prefix += f" n°{num_jo}"
        title = f"{prefix} — {main_title}" if main_title else prefix
    elif is_commission:
        # Privilégie le libellé résolu de la commission quand on l'a
        commission_label = organe_label or (f"({organe_ref})" if organe_ref else "")
        if main_title:
            if main_title.lower().startswith("audition"):
                title = main_title
            elif organe_label:
                title = f"{organe_label} — {main_title}"
            else:
                title = f"Commission — {main_title}"
        else:
            # R13-H (2026-04-21) : ne plus exposer le code PO brut en titre
            # quand le cache AMO ne résout pas l'organe (ex. commissions
            # créées après la dernière maj du dump /17/). On préfère un
            # libellé générique que `_fix_agenda_row` enrichira ensuite
            # avec la date de séance.
            title = (f"Réunion — {organe_label}" if organe_label
                     else "Réunion de commission")
    else:
        if main_title:
            title = main_title
        elif organe_label:
            title = f"Réunion — {organe_label}"
        else:
            # R13-H : idem, pas de `Réunion ({organe_ref})` avec code brut.
            title = "Réunion"

    title = title[:220]

    # --- URL stable
    url = _agenda_url(uid, xsi_type, dt, cr_ref, organe_ref=organe_ref)

    # --- SUMMARY : structuré + shotgun NETTOYÉ pour alimenter le matcher.
    # On filtre du shotgun :
    #   - les listes de présence (PAxxxxxx absent/présent → des dizaines)
    #   - les UID techniques (PAxxxx, POxxxx, RUANR…, SLAN…)
    #   - les timestamps ISO et URIs schema
    #   - les booléens isolés ("false true true")
    #   - les marqueurs xsi:type (`*_type`)
    # Pour ne garder que le contenu sémantique (titres ODJ, auditions,
    # personnes entendues, thèmes), exploitable comme extrait phrase.
    organe_display = (f"{organe_label} ({organe_ref})" if organe_label and organe_ref
                       else organe_label or organe_ref)
    structured = " — ".join(p for p in [
        f"Organe : {organe_display}" if organe_display else "",
        f"Lieu : {lieu}" if lieu else "",
        " · ".join(titles[:5]) if titles else "",
    ] if p)
    shotgun_clean = _clean_agenda_shotgun(_all_text(root))
    summary = (structured + " — " + shotgun_clean if structured else shotgun_clean)[:2000]

    # --- GUARD : on ne garde pas une réunion sans aucune info utile.
    # Cas observé : l'XML contient juste l'UID + le rattachement organe,
    # sans titre ni ODJ — produit un item "Réunion" vide qui pollue le site.
    # R13-H : on tolère organe_ref seul (cache AMO incomplet) — `_fix_agenda_row`
    # enrichira le titre avec la date de séance au moment de l'export, évitant
    # que le changement de fallback ci-dessus (pas de code brut) ne masque
    # des items qui étaient auparavant affichés sous "Réunion (POxxx)".
    if (title in ("Réunion", "Réunion de commission")
            and not titles and not organe_label and not organe_ref):
        log.debug("Agenda %s : skip réunion sans titre ni organe", uid)
        return

    yield Item(
        source_id=src["id"],
        uid=uid,
        category=cat,
        chamber="AN",
        title=title,
        url=url,
        published_at=dt,
        summary=summary,
        raw={
            "path": "assemblee:reunion",
            "organe": organe_ref,
            "organe_label": organe_label,
            "lieu": lieu,
            "xsi_type": xsi_type,
        },
    )
