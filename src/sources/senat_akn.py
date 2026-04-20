"""Connecteur Sénat — dossiers législatifs via flux Akoma Ntoso.

Le flux CSV (dosleg.zip) du Sénat ne remonte qu'un titre (souvent en
minuscule / tronqué), pas de date, pas de statut de procédure. Pour
afficher des dossiers législatifs correctement datés et qualifiés
(stade de la navette), on utilise le flux XML Akoma Ntoso exposé par
la DSI Sénat :

  - https://www.senat.fr/akomantoso/depots.xml      : textes déposés
  - https://www.senat.fr/akomantoso/adoptions.xml   : textes adoptés
  - https://www.senat.fr/akomantoso/{TYPE}{SESS}-{N}.akn.xml

Chaque document unitaire contient :
  <bill name="pjl|ppl|ppr|plf|plfss|pjlo|pplo|tas|td">
    <meta>
      <identification>
        <FRBRWork>
          <FRBRalias name="intitule-court" value="..."/>
          <FRBRalias name="url-senat"      value="..."/>
          <FRBRalias name="url-AN"         value="..."/>
        </FRBRWork>
      </identification>
      <workflow>
        <step date="..." by="#senat|#assemblee-nationale|..."
              refersTo="#lecture_1|#cmp|..." outcome="..."/>
      </workflow>
    </meta>
  </bill>

Doc officielle : PDF "Akoma Ntoso — Sénat" (DSI, mars 2021), fondée
sur le standard OASIS LegalDocML 3.0.

Validé en live (avril 2026) sur depots.xml + exemples pjl25-561.akn.xml
et tas25-091.akn.xml — voir mémoire Cyril.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

from ..models import Item
from ._common import fetch_bytes

log = logging.getLogger(__name__)


# Namespace Akoma Ntoso 3.0 — obligatoire sur le <bill> et enfants.
_AKN_NS = {"akn": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}


# --- Mappings ontologie → libellé FR -------------------------------------
# refersTo (eId TLCProcess) → stade de la procédure.
# Les eId exacts viennent du PDF slide 30 : lecture_1..lecture_7,
# lecture_4=CMP, lecture_5=nouvelle, lecture_6=définitive.
_STAGE_BY_REFERS = {
    "lecture_1": "1ère lecture",
    "lecture_2": "2ème lecture",
    "lecture_3": "3ème lecture",
    "lecture_7": "4ème lecture",          # exotique mais présent dans l'ontologie
    "lecture_4": "CMP",
    "cmp": "CMP",                         # alias textuel
    "lecture_5": "nouvelle lecture",
    "nouvelle": "nouvelle lecture",
    "lecture_6": "lecture définitive",
    "definitive": "lecture définitive",
    "promulgation": "promulgation",
}

# `by` = eId de l'organisation qui a produit l'étape (défini dans
# <references><TLCOrganization>). Valeurs observées en live sur le flux
# Sénat (avril 2026) : senat, assemblee-nationale, commission-senat,
# commission-assemblee-nationale, gouvernement, president-republique.
_INSTITUTION_BY = {
    "senat": "Sénat",
    "assemblee-nationale": "AN",
    "gouvernement": "Gouvernement",
    "president-republique": "Promulgation",
    "presidence-republique": "Promulgation",
    "conseil-constitutionnel": "Conseil const.",
    "conseil-etat": "Conseil d'État",
}


def _resolve_institution(by: str) -> tuple[str, bool]:
    """Résout l'attribut `by` d'un <step>. Renvoie (institution, is_commission).
    Le préfixe "commission-" (observé en live : commission-senat,
    commission-assemblee-nationale) est géré : on reconnaît la chambre et
    on marque l'étape comme passage en commission."""
    key = (by or "").strip()
    if not key:
        return "", False
    if key.startswith("commission-"):
        stem = key[len("commission-"):]
        return _INSTITUTION_BY.get(stem, ""), True
    return _INSTITUTION_BY.get(key, ""), False


def _step_from_outcome(outcome: str) -> str:
    """Déduit une sous-étape (dépôt/commission/hémicycle/…) depuis le
    texte libre FR du champ `outcome` du <step>.
    Exemples observés en live (avril 2026) : "déposé au Sénat",
    "de la commission", "adopté par le Sénat", "transmis à l'Assemblée
    nationale", "de la commission (AN)", "rejeté par l'Assemblée nationale",
    "renvoyé en commission", "promulguée (Loi n° …)"."""
    s = (outcome or "").lower()
    if "promulg" in s:
        return "promulgation"
    if "transmi" in s:
        return "transmission"
    # "de la commission", "renvoyé en commission", "adopté en commission" → commission
    if "commission" in s:
        return "commission"
    if "hémicycl" in s or "séance" in s:
        return "hémicycle"
    if "adopt" in s or "rejet" in s:
        return "hémicycle"
    if "déposé" in s or "dépôt" in s or "présenté" in s:
        return "dépôt"
    return ""


def _format_status(institution: str, stage: str, step: str) -> str:
    """Concatène les 3 composantes en "Inst · stade · étape"."""
    parts = [p for p in (institution, stage, step) if p]
    return " · ".join(parts)


# Format observé dans <lastModifiedDateTime>: "2019-12-05T17:46:20" (ISO, sans tz).
# <lastModified> "Thu Dec 05 17:46:20 CET 2019" est locale-dépendant → on évite.
def _parse_last_modified(dt_text: str | None) -> datetime | None:
    if not dt_text:
        return None
    try:
        return datetime.fromisoformat(dt_text.strip())
    except Exception:
        return None


# Libellés FR des types de texte (attribut @name du <bill>).
_TYPE_LABELS = {
    "pjl": "Projet de loi",
    "ppl": "Proposition de loi",
    "ppr": "Proposition de résolution",
    "plf": "Projet de loi de finances",
    "plfss": "Projet de loi de financement SS",
    "pjlo": "Projet de loi organique",
    "pplo": "Proposition de loi organique",
    "tas": "Texte adopté",
    "td": "Texte définitif",
}


def _type_label(tp: str) -> str:
    return _TYPE_LABELS.get((tp or "").lower(), (tp or "").upper())


# URL .akn.xml : /akomantoso/{TYPE}{SESS}-{NUM}.akn.xml
# Les rectifs (rec, recbis, …) viennent après le numéro.
_AKN_URL_RE = re.compile(
    r"/akomantoso/(?P<type>[a-z]+)(?P<session>\d+)-(?P<num>\d+[a-z_]*)"
    r"\.akn\.xml$",
    re.IGNORECASE,
)


def _find(parent, path):
    return parent.find(path, _AKN_NS) if parent is not None else None


def _findall(parent, path):
    return parent.findall(path, _AKN_NS) if parent is not None else []


def parse_bill(xml_bytes: bytes, url: str) -> dict | None:
    """Parse un .akn.xml unitaire et renvoie un dict normalisé :
      {type, session, num, uid, titre, stage_last, step_last,
       institution_last, date_last, has_promulgation,
       url_senat, url_an, signet, steps}.
    Renvoie None si le XML n'est pas exploitable."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("parse AKN %s : XML KO (%s)", url, e)
        return None

    bill = _find(root, "./akn:bill")
    if bill is None:
        # Certaines manifestations utilisent <doc> au lieu de <bill> (ex. td/tas
        # sous forme de texte définitif, selon versions). On reste laxiste.
        bill = _find(root, "./akn:doc") or root
    btype = (bill.get("name") or "").lower()

    # -- FRBRWork : titre, alias, dossier_key
    frbr_work = _find(bill, "./akn:meta/akn:identification/akn:FRBRWork")
    aliases: dict[str, str] = {}
    for a in _findall(frbr_work, "./akn:FRBRalias"):
        key = (a.get("name") or "").strip()
        val = (a.get("value") or "").strip()
        if key:
            aliases[key] = val
    intitule_court = aliases.get("intitule-court") or aliases.get("intituleCourt") or ""
    url_senat = aliases.get("url-senat") or aliases.get("urlSenat") or ""
    url_an = aliases.get("url-AN") or aliases.get("urlAn") or ""
    signet = aliases.get("signet-dossier-legislatif-senat") or ""
    frbr_this = _find(frbr_work, "./akn:FRBRthis")
    frbr_this_val = (frbr_this.get("value") if frbr_this is not None else "") or ""
    m_this = re.match(r"/akn/fr/[^/]+/([^/]+)/main", frbr_this_val)
    dossier_key = m_this.group(1) if m_this else ""

    # -- FRBRExpression : session + n° de dépôt
    frbr_expr = _find(bill, "./akn:meta/akn:identification/akn:FRBRExpression")
    expr_uri = ""
    if frbr_expr is not None:
        ft = _find(frbr_expr, "./akn:FRBRuri")
        expr_uri = (ft.get("value") if ft is not None else "") or ""
    m_expr = re.match(r"/akn/fr/([^/]+)/([^/]+)/([^/]+?)(?:/fr@.*)?$", expr_uri)
    session_exp, num_exp = "", ""
    if m_expr:
        session_exp = m_expr.group(2)
        num_exp = m_expr.group(3)

    # Fallback depuis l'URL .akn.xml si FRBRExpression absent
    m_url = _AKN_URL_RE.search(url)
    session_url = m_url.group("session") if m_url else ""
    num_url = m_url.group("num") if m_url else ""

    # -- workflow <step>
    steps = []
    wf = _find(bill, "./akn:meta/akn:workflow")
    for st in _findall(wf, "./akn:step"):
        d = st.get("date") or ""
        by = (st.get("by") or "").lstrip("#")
        refers = (st.get("refersTo") or "").lstrip("#")
        outcome = st.get("outcome") or ""
        try:
            dt = datetime.strptime(d, "%Y-%m-%d") if d else None
        except ValueError:
            dt = None
        steps.append({
            "date": dt,
            "by": by,
            "refersTo": refers,
            "outcome": outcome,
        })

    # Étapes datées, triées croissant (la dernière = statut courant).
    steps_dated = sorted(
        [s for s in steps if s["date"] is not None],
        key=lambda s: s["date"],
    )
    has_promulgation = any(
        "promulg" in (s["outcome"] or "").lower() for s in steps_dated
    ) or any((s["by"] or "").startswith("president") for s in steps_dated)

    last = steps_dated[-1] if steps_dated else {}
    stage_last = _STAGE_BY_REFERS.get(last.get("refersTo") or "", "")
    institution_last, is_commission = _resolve_institution(last.get("by") or "")
    step_last = _step_from_outcome(last.get("outcome") or "")
    # Si by est "commission-*", on force step="commission" même si l'outcome
    # ne contient pas le mot (cas "de la commission (AN)" qui matche déjà,
    # mais aussi "projet rédigé en commission").
    if is_commission and not step_last:
        step_last = "commission"
    date_last = last.get("date")

    # Promulgation : l'étape n'a pas toujours de refersTo ontologie.
    if has_promulgation and not stage_last:
        stage_last = "promulgation"
        step_last = step_last or "JORF"
        institution_last = institution_last or "Promulgation"

    return {
        "type": btype,
        "session": session_exp or session_url,
        "num": num_exp or num_url,
        "dossier_key": dossier_key,
        "uid": signet or dossier_key or f"{btype}{session_url}-{num_url}",
        "titre": intitule_court,
        "url_senat": url_senat,
        "url_an": url_an,
        "signet": signet,
        "date_last": date_last,
        "stage_last": stage_last,
        "step_last": step_last,
        "institution_last": institution_last,
        "has_promulgation": has_promulgation,
        "steps": steps_dated,
        "frbr_expression_uri": expr_uri,
    }


# Fenêtres d'inclusion — alignées sur assemblee.py (veille active).
_DOSLEG_MAX_AGE_ACTIVE_DAYS = 365
_DOSLEG_MAX_AGE_PROMULGATED_DAYS = 548

# Nombre max de fetch unitaires .akn.xml par run (garde-fou : 1 requête
# HTTP par texte). L'index depots.xml fait ~750 entrées remontant à 2023 ;
# on se limite aux plus récentes. since_days (config) filtre ensuite
# par lastModifiedDateTime.
_MAX_FETCH_PER_RUN = 300


def fetch_akn_index(src: dict) -> list[Item]:
    """Télécharge depots.xml / adoptions.xml et ingère chaque .akn.xml
    récent référencé. Renvoie une liste d'Item (catégorie
    dossiers_legislatifs, chambre Sénat)."""
    sid = src["id"]
    url_idx = src["url"]
    # Filtre temporel au niveau de l'index. Par défaut on remonte 90 jours.
    since_days = int(src.get("since_days") or 90)

    try:
        idx_bytes = fetch_bytes(url_idx)
    except Exception as e:
        log.exception("Sénat %s : fetch index KO %s", sid, e)
        return []

    try:
        idx_root = ET.fromstring(idx_bytes)
    except ET.ParseError as e:
        log.warning("Sénat %s : parse index KO (%s)", sid, e)
        return []

    # L'index depots.xml / adoptions.xml n'a pas de namespace : on cherche
    # les <text> à plat. On supporte malgré tout un namespace au cas où la
    # DSI en ajouterait un jour.
    text_elems = idx_root.findall("./text") or idx_root.findall(".//text")
    entries: list[tuple[str, datetime | None]] = []
    for te in text_elems:
        u_el = te.find("url")
        dt_el = te.find("lastModifiedDateTime")
        u = (u_el.text or "").strip() if u_el is not None and u_el.text else ""
        dt = _parse_last_modified(dt_el.text if dt_el is not None else None)
        if u:
            entries.append((u, dt))

    # Dédup URL, tri décroissant par lastModifiedDateTime
    seen = set()
    uniq: list[tuple[str, datetime | None]] = []
    for u, dt in entries:
        if u in seen:
            continue
        seen.add(u)
        uniq.append((u, dt))
    uniq.sort(key=lambda x: x[1] or datetime(1970, 1, 1), reverse=True)

    # Filtre fenêtre depuis since_days
    cutoff = datetime.now() - timedelta(days=since_days)
    uniq = [(u, dt) for (u, dt) in uniq if (dt is None or dt >= cutoff)]
    uniq = uniq[:_MAX_FETCH_PER_RUN]
    log.info(
        "Sénat %s : %d textes à fetch unitaire (since=%dj, cap=%d)",
        sid, len(uniq), since_days, _MAX_FETCH_PER_RUN,
    )

    # Agrégation par dossier : même loi déposée en plusieurs versions (tas,
    # pjl rectifié, td…) partage le même workflow — on garde l'entrée la
    # plus à jour pour présenter UN statut de dossier.
    by_uid: dict[str, Item] = {}
    now = datetime.now()
    fetched_ok = 0
    for u, dt_idx in uniq:
        try:
            xml = fetch_bytes(u)
        except Exception as e:
            log.warning("Sénat %s : fetch .akn.xml KO %s (%s)", sid, u, e)
            continue
        data = parse_bill(xml, u)
        if not data:
            continue
        fetched_ok += 1

        titre = (data.get("titre") or "").strip()
        if not titre:
            tl = _type_label(data.get("type", ""))
            num = data.get("num", "")
            session = data.get("session", "")
            titre = " ".join(p for p in [
                tl,
                f"n°{num}" if num else "",
                f"({session})" if session else "",
            ] if p).strip()
        if not titre:
            continue

        date_last = data.get("date_last") or dt_idx
        if date_last is None:
            continue
        age_days = (now - date_last).days
        max_age_d = (
            _DOSLEG_MAX_AGE_PROMULGATED_DAYS
            if data.get("has_promulgation")
            else _DOSLEG_MAX_AGE_ACTIVE_DAYS
        )
        if age_days > max_age_d:
            continue

        status_label = _format_status(
            data.get("institution_last") or "Sénat",
            data.get("stage_last") or "",
            data.get("step_last") or "",
        )

        page_url = data.get("url_senat") or data.get("url_an") or u
        uid = data.get("uid") or hashlib.sha1(u.encode()).hexdigest()[:16]
        summary = " ".join(p for p in [
            _type_label(data.get("type", "")),
            f"n°{data['num']}" if data.get("num") else "",
            f"({data['session']})" if data.get("session") else "",
        ] if p)

        item = Item(
            source_id=sid,
            uid=uid,
            category="dossiers_legislatifs",
            chamber="Senat",
            title=titre[:220],
            url=page_url,
            published_at=date_last,
            summary=summary[:500],
            raw={
                "path": "senat:akn",
                "type": data.get("type", ""),
                "session": data.get("session", ""),
                "num": data.get("num", ""),
                "status_label": status_label,
                "institution": data.get("institution_last", ""),
                "stage": data.get("stage_last", ""),
                "step": data.get("step_last", ""),
                "is_promulgated": bool(data.get("has_promulgation")),
                "akn_url": u,
                "signet": data.get("signet", ""),
            },
        )
        # Dédup dossier : on remplace si on trouve plus récent.
        prev = by_uid.get(uid)
        if prev is None or (
            prev.published_at
            and item.published_at
            and item.published_at > prev.published_at
        ):
            by_uid[uid] = item

    log.info(
        "Sénat %s : %d items (fetch ok=%d, dossiers uniques=%d)",
        sid, len(by_uid), fetched_ok, len(by_uid),
    )
    return list(by_uid.values())
