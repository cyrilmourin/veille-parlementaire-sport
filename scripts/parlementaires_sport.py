"""R43-A (2026-05-16) — Script manuel : top des parlementaires actifs sur le sport.

Cyril 2026-05-16 a validé :
- Périmètre élargi (filtre keyword sport sur contenu OU texte parent)
- Cumul OK (un même parlementaire peut être crédité sur plusieurs dossiers)
- Mode autonome (pas de signal qualité, on compte les volumes — taux
  d'adoption affiché à côté du score pour transparence)
- Mise à jour trimestrielle (1er du mois tous les 3 mois ou ad hoc)

Pondération validée :
  QE / QOSD                                  1 pt
  QAG (Question au Gouvernement)             5 pts
  Amendement déposé                          2 pts
  Amendement adopté                          5 pts
  Rapporteur principal d'un texte sport     15 pts
  Rapporteur spécifique du budget sport     15 pts
  Rapporteur pour avis / co-rapporteur      10 pts
  1er signataire d'une PPL sport déposée    15 pts
  Signataire d'une PPL sport (non premier)  10 pts
  1er signataire d'un texte sport adopté    25 pts
  1er signataire ou signataire résolution    5 pts
  Auteur rapport parlementaire sport        15 pts

Usage:
    python scripts/parlementaires_sport.py [--top 20] [--no-fetch]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Permet d'exécuter le script depuis n'importe où sans installer le package
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.keywords import KeywordMatcher  # noqa: E402

log = logging.getLogger("parlementaires_sport")


# ---------------------------------------------------------------------------
# Constantes — dumps AN open data XVII
# ---------------------------------------------------------------------------

AN_DUMPS = {
    "AMO10": "https://data.assemblee-nationale.fr/static/openData/repository/17/amo/deputes_actifs_mandats_actifs_organes/AMO10_deputes_actifs_mandats_actifs_organes.json.zip",
    "QE":    "https://data.assemblee-nationale.fr/static/openData/repository/17/questions/questions_ecrites/Questions_ecrites.json.zip",
    "QAG":   "https://data.assemblee-nationale.fr/static/openData/repository/17/questions/questions_gouvernement/Questions_gouvernement.json.zip",
    "QOSD":  "https://data.assemblee-nationale.fr/static/openData/repository/17/questions/questions_orales_sans_debat/Questions_orales_sans_debat.json.zip",
    "AMDT":  "https://data.assemblee-nationale.fr/static/openData/repository/17/loi/amendements_div_legis/Amendements.json.zip",
    "DOSLEG": "https://data.assemblee-nationale.fr/static/openData/repository/17/loi/dossiers_legislatifs/Dossiers_Legislatifs.json.zip",
}

CACHE_DIR = ROOT / "data" / "parlementaires_cache"

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

SCORE = {
    "qe": 1, "qosd": 1, "qag": 5,
    "amdt_depose": 2, "amdt_adopte": 5,    # adopte = bonus en plus du depose
    "rapporteur_principal": 15,
    "rapporteur_avis_co": 10,
    "ppl_premier_signataire": 15,
    "ppl_signataire": 10,
    "texte_adopte_premier_signataire": 25,  # bonus en plus du « 1er signataire PPL »
    "resolution_signataire": 5,
    "rapport_parlementaire_auteur": 15,
}

# ---------------------------------------------------------------------------
# Modèles
# ---------------------------------------------------------------------------

@dataclass
class Acteur:
    acteur_ref: str
    chambre: str   # "AN" / "Senat"
    civ: str = ""
    prenom: str = ""
    nom: str = ""
    groupe_abrege: str = ""
    groupe_long: str = ""
    circonscription: str = ""
    photo_url: str = ""
    fiche_url: str = ""

    @property
    def label_court(self) -> str:
        return f"{self.civ} {self.prenom} {self.nom}".strip()


@dataclass
class CompteurActeur:
    acteur_ref: str = ""
    chambre: str = ""
    # Compteurs détaillés par type d'activité
    qe: int = 0
    qosd: int = 0
    qag: int = 0
    # R43-A bis (2026-05-17) — `amdt_depose` = en tant qu'AUTEUR PRINCIPAL.
    # La cosignature en bloc (groupes politiques signant collectivement les
    # amdts de leurs collègues) gonflait artificiellement le score : sur
    # 1005 amdt sport identifiés, le top 20 était composé à 85% du groupe
    # LFI-NFP via cosignatures massives sur PJL JO 2030 (674 amdt). On
    # garde la cosignature en stat séparée (`amdt_cosigne`) mais sans
    # points — l'effort éditorial / la responsabilité d'auteur reste le
    # bon signal. Cyril : « si trop large, propose d'y revenir ».
    amdt_depose: int = 0    # en tant qu'auteur principal
    amdt_adopte: int = 0    # idem, et adopté
    amdt_cosigne: int = 0   # cosignataire — tracé pour transparence, 0 pt
    rapporteur_principal: int = 0
    rapporteur_avis_co: int = 0
    ppl_premier_signataire: int = 0
    ppl_signataire: int = 0
    texte_adopte_premier_signataire: int = 0
    resolution_signataire: int = 0
    rapport_parlementaire_auteur: int = 0
    # Activités tracées (pour affichage détaillé)
    activites: list[dict] = field(default_factory=list)

    def score(self) -> int:
        return (
            self.qe * SCORE["qe"]
            + self.qosd * SCORE["qosd"]
            + self.qag * SCORE["qag"]
            + self.amdt_depose * SCORE["amdt_depose"]
            + self.amdt_adopte * SCORE["amdt_adopte"]
            + self.rapporteur_principal * SCORE["rapporteur_principal"]
            + self.rapporteur_avis_co * SCORE["rapporteur_avis_co"]
            + self.ppl_premier_signataire * SCORE["ppl_premier_signataire"]
            + self.ppl_signataire * SCORE["ppl_signataire"]
            + self.texte_adopte_premier_signataire * SCORE["texte_adopte_premier_signataire"]
            + self.resolution_signataire * SCORE["resolution_signataire"]
            + self.rapport_parlementaire_auteur * SCORE["rapport_parlementaire_auteur"]
        )

    def taux_adoption_amdt(self) -> float | None:
        if self.amdt_depose <= 0:
            return None
        return round(100.0 * self.amdt_adopte / self.amdt_depose, 1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_cached(url: str, name: str, *, force: bool = False) -> Path:
    """Télécharge un dump et le met en cache local. Skip si déjà cached."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.zip"
    if path.exists() and not force:
        log.info("Cache HIT %s (%d MB)", name, path.stat().st_size / 1_000_000)
        return path
    log.info("Fetch %s …", name)
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    path.write_bytes(data)
    log.info("  → %d MB", len(data) / 1_000_000)
    return path


def _text_of(v) -> str:
    """Robust string extraction (gère les `{#text: "..."}` du XSD AN)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        # Forme { "#text": "...", "@xsi:type": "..." }
        for k in ("#text", "valeur", "libelle"):
            if k in v and isinstance(v[k], str):
                return v[k]
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    return ""


def _strip_html(txt: str) -> str:
    """Retire les balises HTML basiques (utile pour le matcher sport sur le
    texte intégral de QAG / réponses, qui sont stockés en HTML)."""
    if not txt:
        return ""
    return re.sub(r"<[^>]+>", " ", txt)


def _build_an_photo_url(acteur_ref: str, legislature: int = 17) -> str:
    """Cf. amo_loader.build_photo_url_an (recopié ici pour autonomie du
    script)."""
    if not acteur_ref or not acteur_ref.startswith("PA"):
        return ""
    digits = acteur_ref[2:]
    return f"https://www.assemblee-nationale.fr/dyn/static/tribun/{legislature}/photos/carre/{digits}.jpg"


def _build_an_fiche_url(acteur_ref: str, legislature: int = 17) -> str:
    if not acteur_ref or not acteur_ref.startswith("PA"):
        return ""
    return f"https://www.assemblee-nationale.fr/dyn/{legislature}/deputes/{acteur_ref}"


# ---------------------------------------------------------------------------
# 1. Référentiel acteurs AN
# ---------------------------------------------------------------------------

def load_acteurs_an(zip_path: Path) -> dict[str, Acteur]:
    """Parse AMO10 et retourne {acteurRef → Acteur} pour les députés actifs XVII."""
    registry: dict[str, Acteur] = {}
    organe_to_groupe: dict[str, tuple[str, str]] = {}
    with zipfile.ZipFile(zip_path) as z:
        # 1ère passe : groupes politiques (PO*) pour résoudre les groupeRef
        for name in z.namelist():
            if not name.startswith("json/organe/") or not name.endswith(".json"):
                continue
            try:
                d = json.loads(z.read(name))
            except Exception:
                continue
            o = d.get("organe", d) if isinstance(d, dict) else {}
            code = _text_of(o.get("codeType"))
            if code != "GP":  # groupe politique
                continue
            uid = _text_of(o.get("uid"))
            if not uid:
                continue
            organe_to_groupe[uid] = (
                _text_of(o.get("libelleAbrev")),
                _text_of(o.get("libelle")),
            )

        # 2e passe : acteurs (PA*)
        for name in z.namelist():
            if not name.startswith("json/acteur/") or not name.endswith(".json"):
                continue
            try:
                d = json.loads(z.read(name))
            except Exception:
                continue
            a = d.get("acteur", d) if isinstance(d, dict) else {}

            uid_node = a.get("uid")
            acteur_ref = _text_of(uid_node)
            if not acteur_ref or not acteur_ref.startswith("PA"):
                continue
            ec = a.get("etatCivil") or {}
            ident = ec.get("ident") or {}
            # Mandat actif → groupe + circonscription
            grp_abr = grp_long = circo = ""
            mandats = (a.get("mandats") or {}).get("mandat") or []
            if isinstance(mandats, dict):
                mandats = [mandats]
            for m in mandats:
                if not isinstance(m, dict):
                    continue
                # Cherche le mandat de député actif
                date_fin = _text_of(m.get("dateFin"))
                if date_fin:
                    continue  # mandat expiré
                # Groupe : organeRef de type GP
                organes = m.get("organes") or {}
                orgs = organes.get("organeRef") or []
                if isinstance(orgs, str):
                    orgs = [orgs]
                for oref in orgs:
                    if oref in organe_to_groupe:
                        grp_abr, grp_long = organe_to_groupe[oref]
                        break
                # Circonscription
                lieu = (m.get("election") or {}).get("lieu") or {}
                if isinstance(lieu, dict):
                    dep_label = _text_of(lieu.get("departement"))
                    num_circo = _text_of(lieu.get("numCirco"))
                    if dep_label:
                        circo = (
                            f"{dep_label} ({num_circo})" if num_circo else dep_label
                        )
            registry[acteur_ref] = Acteur(
                acteur_ref=acteur_ref,
                chambre="AN",
                civ=_text_of(ident.get("civ")),
                prenom=_text_of(ident.get("prenom")),
                nom=_text_of(ident.get("nom")),
                groupe_abrege=grp_abr,
                groupe_long=grp_long,
                circonscription=circo,
                photo_url=_build_an_photo_url(acteur_ref),
                fiche_url=_build_an_fiche_url(acteur_ref),
            )
    log.info("Acteurs AN chargés : %d", len(registry))
    return registry


# ---------------------------------------------------------------------------
# 2. Index dosleg sport (titre du dossier matche keyword sport)
# ---------------------------------------------------------------------------

def build_dosleg_sport_index(zip_path: Path, matcher: KeywordMatcher) -> set[str]:
    """Retourne {dossierRef} (DLR5L17NXXXX) dont le titre matche sport."""
    sport_doslegs: set[str] = set()
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.startswith("json/dossierParlementaire/"):
                continue
            try:
                d = json.loads(z.read(name))
            except Exception:
                continue
            dp = d.get("dossierParlementaire", d) if isinstance(d, dict) else {}
            uid = _text_of(dp.get("uid"))
            if not uid:
                continue
            titre_node = dp.get("titreDossier") or {}
            titre = _text_of(titre_node.get("titre"))
            matched, _fams = matcher.match(titre)
            if matched:
                sport_doslegs.add(uid)
    log.info("Dossiers législatifs sport indexés : %d", len(sport_doslegs))
    return sport_doslegs


# ---------------------------------------------------------------------------
# 3. Scanners
# ---------------------------------------------------------------------------

def _question_haystack(q: dict) -> str:
    """Concatène champs utiles pour matcher sport sur une question."""
    parts: list[str] = []
    idx = q.get("indexationAN") or {}
    parts.append(_text_of(idx.get("rubrique")))
    parts.append(_text_of(idx.get("teteAnalyse")))
    analyses = idx.get("analyses") or {}
    an = analyses.get("analyse") if isinstance(analyses, dict) else None
    if isinstance(an, str):
        parts.append(an)
    elif isinstance(an, list):
        parts.extend(_text_of(x) for x in an)
    # Texte de la question (HTML)
    tq = q.get("textesQuestion")
    if isinstance(tq, dict):
        parts.append(_strip_html(_text_of(tq.get("texte"))))
    # Texte de la réponse (HTML) — utile pour QAG dont le sujet sport est dans la réponse
    tr = q.get("textesReponse")
    if isinstance(tr, dict):
        tr_inner = tr.get("texteReponse") or {}
        if isinstance(tr_inner, dict):
            parts.append(_strip_html(_text_of(tr_inner.get("texte"))))
    return " ".join(p for p in parts if p)[:5000]


def scan_questions_an(
    zip_path: Path, matcher: KeywordMatcher, registry: dict[str, Acteur],
    counters: dict[str, CompteurActeur], q_kind: str,
) -> int:
    """q_kind ∈ {'qe','qag','qosd'}."""
    n_match = 0
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.endswith(".json"):
                continue
            try:
                d = json.loads(z.read(name))
            except Exception:
                continue
            q = d.get("question", d) if isinstance(d, dict) else {}
            auteur = q.get("auteur") or {}
            id_auteur = (auteur.get("identite") or {})
            acteur_ref = _text_of(id_auteur.get("acteurRef"))
            if not acteur_ref:
                continue
            hay = _question_haystack(q)
            matched, _ = matcher.match(hay)
            if not matched:
                continue
            n_match += 1
            c = counters[acteur_ref]
            c.acteur_ref = acteur_ref
            c.chambre = "AN"
            if q_kind == "qe":
                c.qe += 1
            elif q_kind == "qag":
                c.qag += 1
            elif q_kind == "qosd":
                c.qosd += 1
            # Trace
            numero = _text_of((q.get("identifiant") or {}).get("numero"))
            c.activites.append({
                "type": q_kind.upper(),
                "numero": numero,
                "titre": _text_of((q.get("indexationAN") or {}).get("analyses", {}).get("analyse")),
                "url": f"https://questions.assemblee-nationale.fr/q17/17-{numero}{'QE' if q_kind == 'qe' else 'QG' if q_kind == 'qag' else 'QOSD'}.htm",
            })
    log.info("Questions %s sport matchées : %d", q_kind.upper(), n_match)
    return n_match


def scan_amendements_an(
    zip_path: Path, matcher: KeywordMatcher, registry: dict[str, Acteur],
    counters: dict[str, CompteurActeur], dosleg_sport: set[str],
) -> int:
    """Parcourt le dump amendements AN XVII (270 MB, ~110k fichiers).

    R43-A bis (2026-05-17) — Structure réelle découverte :
    - Path du zip : `json/<DLR5L17NXXXX>/<TEXTE_REF>/AMANR...json`
      → le 1er segment après "json/" est le `dossierRef` parent. **Filtre
      élargi pur path-based** : on compte l'amendement si son dosleg
      parent est sport, sans relire le texte de l'amdt.
    - `signataires.auteur.acteurRef` : auteur principal (dict singleton)
    - `signataires.cosignataires.acteurRef` : liste cosignataires (ou
      singleton str)
    - `cycleDeVie.sort` : string directe ("Adopté", "Rejeté", "Tombé",
      "Retiré"). Pas un dict avec libelle.
    """
    n_match = 0
    n_adopte = 0
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.endswith(".json"):
                continue
            # Path-based dosleg filter (gros gain perf : on n'ouvre que ~3%
            # des fichiers du zip).
            parts = name.split("/")
            if len(parts) < 2:
                continue
            dossier_ref = parts[1]
            if dossier_ref not in dosleg_sport:
                continue
            try:
                d = json.loads(z.read(name))
            except Exception:
                continue
            amdt = d.get("amendement", d) if isinstance(d, dict) else {}
            signataires = amdt.get("signataires") or {}

            # Auteur principal
            auteur_node = signataires.get("auteur") or {}
            principal_ref = _text_of(auteur_node.get("acteurRef")) if isinstance(auteur_node, dict) else ""

            # Cosignataires (peut être list str, str unique, ou dict imbriqué)
            cosig_node = signataires.get("cosignataires") or {}
            cosig_refs: list[str] = []
            if isinstance(cosig_node, dict):
                refs = cosig_node.get("acteurRef") or []
                if isinstance(refs, str):
                    cosig_refs = [refs]
                elif isinstance(refs, list):
                    cosig_refs = [str(r) for r in refs if r]

            all_refs = [r for r in [principal_ref] + cosig_refs if r and r.startswith("PA")]
            if not all_refs:
                continue

            # Sort (Adopté ?)
            cdv = amdt.get("cycleDeVie") or {}
            sort_raw = cdv.get("sort")
            sort_label = _text_of(sort_raw) if not isinstance(sort_raw, dict) else _text_of(sort_raw.get("libelle"))
            is_adopte = "adopt" in (sort_label or "").lower()

            n_match += 1
            if is_adopte:
                n_adopte += 1
            # R43-A bis : seul l'auteur principal touche les points
            # `amdt_depose` (+ bonus si adopté). Les cosignataires sont
            # tracés en `amdt_cosigne` sans points.
            if principal_ref and principal_ref.startswith("PA"):
                c = counters[principal_ref]
                c.acteur_ref = principal_ref
                c.chambre = "AN"
                c.amdt_depose += 1
                if is_adopte:
                    c.amdt_adopte += 1
            for ref in cosig_refs:
                if not ref.startswith("PA"):
                    continue
                c = counters[ref]
                c.acteur_ref = ref
                c.chambre = "AN"
                c.amdt_cosigne += 1
    log.info(
        "Amendements AN sport (path-based via dosleg) : %d total, %d adoptés",
        n_match, n_adopte,
    )
    return n_match


def _document_auteurs(doc: dict) -> tuple[list[str], list[str], list[str]]:
    """Retourne (premier_signataire_acteur_refs, cosignataires_acteur_refs,
    rapporteurs_acteur_refs)."""
    premiers: list[str] = []
    cosig: list[str] = []
    rapporteurs: list[str] = []

    auteurs_node = doc.get("auteurs") or {}
    auteur_list = auteurs_node.get("auteur") or []
    if isinstance(auteur_list, dict):
        auteur_list = [auteur_list]
    for a in auteur_list:
        if not isinstance(a, dict):
            continue
        acteur = a.get("acteur") or {}
        ref = _text_of(acteur.get("acteurRef"))
        if not ref:
            continue
        qualite = (_text_of(acteur.get("qualite")) or "").lower()
        if "rapporteur" in qualite:
            rapporteurs.append(ref)
        else:
            premiers.append(ref)

    cosig_node = doc.get("coSignataires") or {}
    cs_list = cosig_node.get("coSignataire") or []
    if isinstance(cs_list, dict):
        cs_list = [cs_list]
    for c in cs_list:
        if not isinstance(c, dict):
            continue
        # Retrait de cosignature → ne pas compter
        if _text_of(c.get("dateRetraitCosignature")):
            continue
        ref = _text_of((c.get("acteur") or {}).get("acteurRef"))
        if ref:
            cosig.append(ref)
    return premiers, cosig, rapporteurs


def scan_documents_an(
    zip_path: Path, matcher: KeywordMatcher, registry: dict[str, Acteur],
    counters: dict[str, CompteurActeur], dosleg_sport: set[str],
) -> dict[str, int]:
    """Parcourt les documents PIONAN/PNRE/RAPPAN/AVIS/RINF. Compte selon
    le type + lien avec un dosleg sport.
    """
    stats: dict[str, int] = defaultdict(int)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.startswith("json/document/") or not name.endswith(".json"):
                continue
            try:
                d = json.loads(z.read(name))
            except Exception:
                continue
            doc = d.get("document", d) if isinstance(d, dict) else {}
            uid = _text_of(doc.get("uid"))
            if not uid:
                continue
            # Détection sport : (a) dossierRef ∈ dosleg_sport (élargi), (b) titre matche
            dossier_ref = _text_of(doc.get("dossierRef"))
            titre = _text_of((doc.get("titres") or {}).get("titrePrincipal"))
            in_sport_dosleg = dossier_ref in dosleg_sport
            titre_matche = bool(matcher.match(titre)[0])
            if not (in_sport_dosleg or titre_matche):
                continue

            type_doc = _text_of((doc.get("classification") or {}).get("type", {}).get("code"))
            # Adopté ? Présence d'un texte adopté côté dosleg parent — on utilise
            # `statutAdoption` du document.
            statut = _text_of((doc.get("classification") or {}).get("statutAdoption"))
            is_adopte = "adopt" in (statut or "").lower()

            premiers, cosig, rapporteurs = _document_auteurs(doc)

            if type_doc == "PION":  # Proposition de loi
                stats["PPL"] += 1
                for ref in premiers:
                    c = counters[ref]; c.acteur_ref = ref; c.chambre = "AN"
                    c.ppl_premier_signataire += 1
                    if is_adopte:
                        c.texte_adopte_premier_signataire += 1
                for ref in cosig:
                    c = counters[ref]; c.acteur_ref = ref; c.chambre = "AN"
                    c.ppl_signataire += 1
            elif type_doc == "PNRE":  # Proposition de résolution
                stats["RESOLUTION"] += 1
                for ref in premiers + cosig:
                    c = counters[ref]; c.acteur_ref = ref; c.chambre = "AN"
                    c.resolution_signataire += 1
            elif type_doc == "RAPP":  # Rapport de commission sur un texte
                stats["RAPP"] += 1
                for ref in rapporteurs:
                    c = counters[ref]; c.acteur_ref = ref; c.chambre = "AN"
                    c.rapporteur_principal += 1
            elif type_doc == "AVIS":  # Rapport pour avis
                stats["AVIS"] += 1
                for ref in rapporteurs:
                    c = counters[ref]; c.acteur_ref = ref; c.chambre = "AN"
                    c.rapporteur_avis_co += 1
            elif type_doc in ("RINF", "RION"):  # Rapport d'information
                stats["RINF"] += 1
                for ref in premiers + rapporteurs:
                    c = counters[ref]; c.acteur_ref = ref; c.chambre = "AN"
                    c.rapport_parlementaire_auteur += 1
            elif type_doc == "PRJL":  # PJL : on n'attribue rien (initiative gouv)
                # Sauf rapporteur : 15 pts
                stats["PRJL"] += 1
                for ref in rapporteurs:
                    c = counters[ref]; c.acteur_ref = ref; c.chambre = "AN"
                    c.rapporteur_principal += 1
    log.info(
        "Documents AN sport : PPL=%d, RESOLUTION=%d, RAPP=%d, AVIS=%d, RINF=%d, PRJL=%d",
        stats["PPL"], stats["RESOLUTION"], stats["RAPP"], stats["AVIS"],
        stats["RINF"], stats["PRJL"],
    )
    return dict(stats)


# ---------------------------------------------------------------------------
# 4. Sénat — depuis la DB veille existante
# ---------------------------------------------------------------------------

def scan_senat_from_db(
    matcher: KeywordMatcher,
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
) -> dict[str, int]:
    """Lit data/veille.sqlite3 pour récupérer les items Sénat sport déjà
    filtrés par le pipeline daily.

    Sources Sénat couvertes :
      - senat_questions_1an / senat_qg → QE / QAG / QOSD
      - senat_amendements_*           → amdt (déposé + adopté)
      - dossiers_legislatifs Sénat    → PPL / résolutions (auteur en clair)

    Limites : les CSV Sénat n'ont pas d'acteurRef stable → on indexe par
    `nom + prénom` normalisés. Photos via data/senat_slugs.json.
    """
    import sqlite3
    db_path = ROOT / "data" / "veille.sqlite3"
    if not db_path.exists():
        log.warning("DB veille.sqlite3 absente — Sénat skip")
        return {}

    # Photos sénateurs
    slugs_path = ROOT / "data" / "senat_slugs.json"
    senat_slugs: dict[str, str] = {}
    if slugs_path.exists():
        senat_slugs = json.loads(slugs_path.read_text())

    def _normalize_name(s: str) -> str:
        import unicodedata
        s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]+", "", s.lower())

    def _senat_id(prenom: str, nom: str) -> str:
        return "SENAT::" + _normalize_name(f"{prenom}{nom}")

    stats: dict[str, int] = defaultdict(int)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM items WHERE chamber='Senat' "
        "AND matched_keywords != '[]'"
    )
    for row in cur:
        cat = row["category"]
        try:
            raw = json.loads(row["raw"] or "{}")
        except Exception:
            raw = {}
        nom = (raw.get("nom") or raw.get("Nom") or "").strip()
        prenom = (raw.get("prenom") or raw.get("Prénom") or raw.get("Prenom") or "").strip()
        if not (nom and prenom):
            # Auteur en clair texte parfois
            auteur = raw.get("auteur") or ""
            m = re.match(r"^(M\.|Mme|MM\.|Mme\.)?\s*([^,]+)$", auteur)
            if m:
                full = m.group(2).strip()
                # Heuristique : « Prénom NOM » avec NOM en majuscule
                parts = full.split()
                if len(parts) >= 2:
                    prenom = parts[0]
                    nom = " ".join(p for p in parts[1:] if p.isupper() or p[:1].isupper())
        if not (nom and prenom):
            continue
        sid = _senat_id(prenom, nom)
        c = counters[sid]
        c.acteur_ref = sid
        c.chambre = "Senat"

        groupe = (raw.get("groupe") or raw.get("Groupe") or "").strip()
        if sid not in registry:
            registry[sid] = Acteur(
                acteur_ref=sid, chambre="Senat",
                civ=(raw.get("Civilité") or raw.get("civilite") or ""),
                prenom=prenom, nom=nom, groupe_abrege=groupe,
                photo_url=(senat_slugs.get(_normalize_name(f"{prenom}{nom}"), {}) or {}).get("photo_url", ""),
                fiche_url=(senat_slugs.get(_normalize_name(f"{prenom}{nom}"), {}) or {}).get("fiche_url", ""),
            )

        # Catégorisation
        if cat == "questions":
            nature = (raw.get("Nature") or raw.get("nature") or "").upper()
            if nature == "QG":
                c.qag += 1; stats["qag"] += 1
            elif nature == "QOSD":
                c.qosd += 1; stats["qosd"] += 1
            else:
                c.qe += 1; stats["qe"] += 1
        elif cat == "amendements":
            c.amdt_depose += 1; stats["amdt_depose"] += 1
            sort_label = (raw.get("sort") or raw.get("etat") or "").lower()
            if "adopt" in sort_label:
                c.amdt_adopte += 1; stats["amdt_adopte"] += 1
        elif cat == "dossiers_legislatifs":
            # Heuristique : titre commence par "Proposition de résolution" → résolution
            titre = (row["title"] or "").lower()
            if titre.startswith("proposition de résolution"):
                c.resolution_signataire += 1; stats["resolution_signataire"] += 1
            elif titre.startswith("proposition de loi"):
                c.ppl_premier_signataire += 1; stats["ppl_premier_signataire"] += 1
                statut = (raw.get("statut") or row["status_label"] or "").lower()
                if "promulg" in statut or "adopt" in statut:
                    c.texte_adopte_premier_signataire += 1
                    stats["texte_adopte_premier_signataire"] += 1
    conn.close()
    log.info("Sénat (DB veille) : %s", dict(stats))
    return dict(stats)


# ---------------------------------------------------------------------------
# 5. Output JSON + content Hugo
# ---------------------------------------------------------------------------

def render_outputs(
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    top_n: int = 20,
) -> dict:
    """Construit le payload JSON consommé par Hugo."""
    enriched = []
    for ref, c in counters.items():
        if c.score() <= 0:
            continue
        acteur = registry.get(ref)
        if not acteur:
            continue
        enriched.append({
            "acteur_ref": ref,
            "chambre": acteur.chambre,
            "civ": acteur.civ, "prenom": acteur.prenom, "nom": acteur.nom,
            "label": acteur.label_court,
            "groupe": acteur.groupe_abrege,
            "groupe_long": acteur.groupe_long,
            "circonscription": acteur.circonscription,
            "photo_url": acteur.photo_url,
            "fiche_url": acteur.fiche_url,
            "score": c.score(),
            "stats": {
                "qe": c.qe, "qosd": c.qosd, "qag": c.qag,
                "amdt_depose": c.amdt_depose, "amdt_adopte": c.amdt_adopte,
                "amdt_cosigne": c.amdt_cosigne,
                "rapporteur_principal": c.rapporteur_principal,
                "rapporteur_avis_co": c.rapporteur_avis_co,
                "ppl_premier_signataire": c.ppl_premier_signataire,
                "ppl_signataire": c.ppl_signataire,
                "texte_adopte_premier_signataire": c.texte_adopte_premier_signataire,
                "resolution_signataire": c.resolution_signataire,
                "rapport_parlementaire_auteur": c.rapport_parlementaire_auteur,
            },
            "taux_adoption_amdt": c.taux_adoption_amdt(),
        })
    enriched.sort(key=lambda x: (-x["score"], x["nom"], x["prenom"]))
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "legislature": "XVIIe",
        "scoring": SCORE,
        "top": enriched[:top_n],
        "total_parlementaires_actifs": len(enriched),
    }


def write_hugo_content(payload: dict) -> None:
    """Génère site/content/parlementaires-actifs-sport.md (page Hugo)."""
    out = ROOT / "site" / "content" / "parlementaires-actifs-sport.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = (
        "---\n"
        f"title: \"Parlementaires les plus actifs sur le sport — {payload['legislature']} législature\"\n"
        f"date: {payload['generated_at']}\n"
        "type: page\n"
        "layout: parlementaires-actifs-sport\n"
        "url: \"/parlementaires-actifs-sport/\"\n"
        "description: \"Classement des députés et sénateurs les plus actifs sur les sujets sport durant la XVIIe législature, sur la base d'un score composite agrégeant questions, amendements, rapports et propositions de loi.\"\n"
        "---\n"
    )
    out.write_text(frontmatter, encoding="utf-8")
    log.info("Écrit : %s", out)


def write_data_json(payload: dict) -> None:
    out = ROOT / "site" / "data" / "parlementaires_sport_xvii.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("Écrit : %s (%d KB)", out, out.stat().st_size // 1024)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Top parlementaires actifs sur le sport (XVIIe législature)")
    ap.add_argument("--top", type=int, default=20, help="Nombre d'items dans le top")
    ap.add_argument("--no-fetch", action="store_true",
                    help="N'utilise que le cache local (pas de download)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    matcher = KeywordMatcher(ROOT / "config" / "keywords.yml")
    log.info("Matcher chargé : %d familles, %d termes",
             len(matcher.families), len(matcher.index))

    # 1. Fetch (ou cache)
    t0 = time.time()
    paths = {}
    for name, url in AN_DUMPS.items():
        paths[name] = _fetch_cached(url, name)
    log.info("Fetch/cache terminé en %.1fs", time.time() - t0)

    # 2. Référentiel acteurs
    registry = load_acteurs_an(paths["AMO10"])

    # 3. Index dosleg sport
    dosleg_sport = build_dosleg_sport_index(paths["DOSLEG"], matcher)

    # 4. Scanners
    counters: dict[str, CompteurActeur] = defaultdict(CompteurActeur)
    scan_questions_an(paths["QE"], matcher, registry, counters, "qe")
    scan_questions_an(paths["QAG"], matcher, registry, counters, "qag")
    scan_questions_an(paths["QOSD"], matcher, registry, counters, "qosd")
    scan_documents_an(paths["DOSLEG"], matcher, registry, counters, dosleg_sport)
    scan_amendements_an(paths["AMDT"], matcher, registry, counters, dosleg_sport)

    # 5. Sénat depuis DB veille
    scan_senat_from_db(matcher, counters, registry)

    # 6. Output
    payload = render_outputs(counters, registry, top_n=args.top)
    log.info("Top %d parlementaires sur %d actifs",
             len(payload["top"]), payload["total_parlementaires_actifs"])
    write_data_json(payload)
    write_hugo_content(payload)

    # Affichage console pour validation manuelle
    log.info("== Top affiché ==")
    for i, p in enumerate(payload["top"], 1):
        log.info(
            "  #%2d  %-30s %5d pts  [%s] %s",
            i, f"{p['prenom']} {p['nom']}"[:30], p["score"],
            p["chambre"], p["groupe"] or "—",
        )


if __name__ == "__main__":
    main()
