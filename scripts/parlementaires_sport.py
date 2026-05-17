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

# R43-A bis (2026-05-17) — Sources Sénat. Le Sénat ne fonctionne pas par
# législature mais par renouvellement triennal partiel ; pour cohérence
# temporelle avec l'AN, on filtre toutes les activités Sénat à partir du
# début de la XVIIe législature AN (1er juillet 2024, élections
# anticipées 2024).
SENAT_DUMPS = {
    "AKN_DEPOTS":    "https://www.senat.fr/akomantoso/depots.xml",
    "AKN_ADOPTIONS": "https://www.senat.fr/akomantoso/adoptions.xml",
    "Q1AN":          "https://data.senat.fr/data/questions/questions-depuis-un-an.csv",
    "QG_SENAT":      "https://data.senat.fr/data/questions/qg.csv",
}

LEGISLATURE_START = "2024-07-01"  # Début XVIIe législature AN

CACHE_DIR = ROOT / "data" / "parlementaires_cache"

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# R43-B (2026-05-17) — Nouvelle pondération validée par Cyril.
# Choix structurants :
# - amdt_depose réduit (0.5 vs 2) : un rapporteur dépose mécaniquement
#   beaucoup, pas besoin de double-pondérer
# - bonus texte_adopte_premier_signataire réduit (10 vs 25) ET appliqué
#   uniquement si texte PROMULGUÉ (pas juste adopté en commission/séance)
# - ajout 2 critères d'appartenance : commission Culture (5), groupe
#   d'étude/mission d'info/commission d'enquête sport (5)
# Tous les pts sont des int sauf amdt_depose (0.5) → on travaille en
# float et arrondit à l'entier au rendu pour rester lisible.
SCORE: dict[str, float] = {
    "membre_commission_culture": 5,
    "membre_groupe_etude_sport": 5,
    "qe": 2, "qosd": 2, "qag": 5,
    "amdt_depose": 0.5, "amdt_adopte": 2,
    "rapporteur_principal": 10,
    "rapporteur_avis_co": 5,
    "ppl_premier_signataire": 15,
    "ppl_signataire": 5,
    "texte_adopte_premier_signataire": 10,  # bonus, applicable SEULEMENT si
                                             # texte promulgué (loi parue au JO)
    "resolution_signataire": 3,
    "rapport_parlementaire_auteur": 10,     # auteur OU co-auteur
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
    organes_refs: list[str] = field(default_factory=list)  # mandats actifs

    @property
    def label_court(self) -> str:
        return f"{self.civ} {self.prenom} {self.nom}".strip()


@dataclass
class CompteurActeur:
    acteur_ref: str = ""
    chambre: str = ""
    # R43-B (2026-05-17) : critères d'appartenance (binaire, 0 ou 1)
    membre_commission_culture: int = 0
    membre_groupe_etude_sport: int = 0
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

    def score(self) -> float:
        return (
            self.membre_commission_culture * SCORE["membre_commission_culture"]
            + self.membre_groupe_etude_sport * SCORE["membre_groupe_etude_sport"]
            + self.qe * SCORE["qe"]
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

def _fetch_via_curl(url: str, out_path: Path, *, timeout: int = 180) -> bool:
    """Délègue à curl (système) pour contourner les problèmes de truststore
    urllib qui bloque certains certs.gouv.fr."""
    import subprocess
    try:
        result = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout),
             "-A", "Mozilla/5.0",
             url, "-o", str(out_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log.warning("curl fetch fail (rc=%d) for %s : %s",
                        result.returncode, url, result.stderr[:200])
            return False
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception as e:
        log.warning("curl exception %s : %s", url, e)
        return False


def _fetch_cached(url: str, name: str, *, force: bool = False, ext: str = ".zip") -> Path:
    """Télécharge un dump et le met en cache local. Skip si déjà cached."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}{ext}"
    if path.exists() and not force:
        log.info("Cache HIT %s (%d KB)", name, path.stat().st_size // 1024)
        return path
    log.info("Fetch %s …", name)
    if not _fetch_via_curl(url, path):
        raise RuntimeError(f"Fetch failed for {name}")
    log.info("  → %d KB", path.stat().st_size // 1024)
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
            # Mandat actif → groupe + circonscription + tous les organes
            grp_abr = grp_long = circo = ""
            all_organes: list[str] = []  # R43-B : nécessaire pour
                                          # détecter la commission Culture
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
                # Tous les organeRef du mandat (groupe + commissions + délégations)
                organes = m.get("organes") or {}
                orgs = organes.get("organeRef") or []
                if isinstance(orgs, str):
                    orgs = [orgs]
                for oref in orgs:
                    if oref and oref not in all_organes:
                        all_organes.append(oref)
                    if oref in organe_to_groupe and not grp_abr:
                        grp_abr, grp_long = organe_to_groupe[oref]
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
                organes_refs=all_organes,
            )
    log.info("Acteurs AN chargés : %d", len(registry))
    return registry


# ---------------------------------------------------------------------------
# 2. Index dosleg sport (titre du dossier matche keyword sport)
# ---------------------------------------------------------------------------

def _detect_promulgated(dp: dict) -> bool:
    """R43-B (2026-05-17) — Détecte si un dossierParlementaire AN porte
    un acte « Promulgation_Type » dans `actesLegislatifs`.

    Cyril : « n'est adopté qu'un texte promulgué » → critère strict.
    """
    raw_str = json.dumps(dp, ensure_ascii=False)
    return (
        "Promulgation_Type" in raw_str
        or '"Promulgation de la loi"' in raw_str
        or '"Promulgation d\'une loi"' in raw_str
    )


def build_dosleg_sport_index(zip_path: Path, matcher: KeywordMatcher) -> dict[str, bool]:
    """R43-B (2026-05-17) — Retourne `{dossierRef: is_promulgated}` pour
    les dossiers sport.
    """
    sport_doslegs: dict[str, bool] = {}
    n_promulg = 0
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
                is_prom = _detect_promulgated(dp)
                sport_doslegs[uid] = is_prom
                if is_prom:
                    n_promulg += 1
    log.info(
        "Dossiers législatifs sport indexés : %d (dont %d promulgués)",
        len(sport_doslegs), n_promulg,
    )
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

    R43-A bis (2026-05-17) — Structure réelle :
    - Path : `json/<DLR5L17NXXXX>/<TEXTE_REF>/AMANR...json`
    - `signataires.auteur.acteurRef` : auteur principal (dict singleton)
    - `signataires.cosignataires.acteurRef` : liste cosignataires
    - `cycleDeVie.sort` : « Adopté » / « Rejeté » / « Tombé » / « Retiré »
    - `corps.contenuAuteur.dispositif` + `exposeSommaire` : contenu

    R43-K (2026-05-17) — Cyril : « tous les amendements sports, y
    compris sur d'autres textes ». 2 passes :
    1. PATH-BASED (scope élargi) : dosleg parent matche sport → on
       compte d'office tous ses amdt.
    2. CONTENT-BASED (filtre strict) : amdt sur dossier non-sport →
       on lit dispositif + exposeSommaire, on matche sport. Compte
       si match.
    Coût : ~110k fichiers JSON lus en ~30-40s.
    """
    n_path = 0
    n_content = 0
    n_adopte = 0
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.endswith(".json"):
                continue
            parts = name.split("/")
            if len(parts) < 2:
                continue
            dossier_ref = parts[1]
            via_path = dossier_ref in dosleg_sport
            try:
                d = json.loads(z.read(name))
            except Exception:
                continue
            amdt = d.get("amendement", d) if isinstance(d, dict) else {}

            # R43-K : si pas sur dosleg sport, match content-based
            via_content = False
            if not via_path:
                corps = amdt.get("corps") or {}
                ca = corps.get("contenuAuteur") if isinstance(corps, dict) else None
                hay_parts = []
                if isinstance(ca, dict):
                    hay_parts.append(_strip_html(_text_of(ca.get("dispositif"))))
                    hay_parts.append(_strip_html(_text_of(ca.get("exposeSommaire"))))
                hay = " ".join(p for p in hay_parts if p)[:5000]
                if hay and matcher.match(hay)[0]:
                    via_content = True
                else:
                    continue

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

            if via_path:
                n_path += 1
            else:
                n_content += 1
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
        "Amendements AN sport : %d via dosleg + %d via contenu = %d total (%d adoptés)",
        n_path, n_content, n_path + n_content, n_adopte,
    )
    return n_path + n_content


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
    counters: dict[str, CompteurActeur], dosleg_sport: dict[str, bool],
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
            # R43-B (2026-05-17) — « adopté » strict = promulgué (loi
            # parue au JO). Cyril : « n'est adopté qu'un texte
            # promulgué ». On lit le flag depuis le dossier parent
            # (capté par `_detect_promulgated` dans `build_dosleg_sport_index`).
            is_promulgated = dosleg_sport.get(dossier_ref, False)

            premiers, cosig, rapporteurs = _document_auteurs(doc)

            if type_doc == "PION":  # Proposition de loi
                stats["PPL"] += 1
                for ref in premiers:
                    c = counters[ref]; c.acteur_ref = ref; c.chambre = "AN"
                    c.ppl_premier_signataire += 1
                    if is_promulgated:
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

def _normalize_name(s: str) -> str:
    """Normalise un nom (sans accents, sans casse, sans espaces) pour
    indexation."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _senat_id(prenom: str, nom: str) -> str:
    """R43-D bis (2026-05-17) — Sid basé sur le NOM seul (sans prénom).
    Évite la duplication d'acteur quand certaines sources n'exposent
    pas le prénom (CSV amdt Sénat). Le risque d'homonymes côté Sénat
    est marginal (~0-2 cas sur 348 sénateurs) et peut être traité via
    override config si besoin.
    """
    return "SENAT::" + _normalize_name(nom)


def _load_senat_slugs() -> dict:
    """Charge data/senat_slugs.json et l'indexe par nom normalisé.

    Format source : {"entries": [{slug, nom_usuel, prenom_usuel, key,
    photo_url, fiche_url}, ...]}. On construit un index par
    `_normalize_name(prenom + nom)`.
    """
    slugs_path = ROOT / "data" / "senat_slugs.json"
    if not slugs_path.exists():
        return {}
    raw = json.loads(slugs_path.read_text())
    indexed: dict[str, dict] = {}
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if entries:
        for e in entries:
            if not isinstance(e, dict):
                continue
            nom = e.get("nom_usuel") or ""
            prenom = e.get("prenom_usuel") or ""
            key = _normalize_name(f"{prenom}{nom}")
            if key:
                indexed[key] = e
            # Index aussi sur nom seul (fallback désambiguïsation imparfaite)
            indexed.setdefault(_normalize_name(nom), e)
    return indexed


def _senat_meta_from_slugs(prenom: str, nom: str, senat_slugs: dict) -> tuple[str, str]:
    """Retourne (photo_url, fiche_url)."""
    key = _normalize_name(f"{prenom}{nom}")
    entry = senat_slugs.get(key) or senat_slugs.get(_normalize_name(nom))
    if isinstance(entry, dict):
        return entry.get("photo_url", ""), entry.get("fiche_url", "")
    return "", ""


def _titlecase_nom(nom: str) -> str:
    """Normalise un NOM en casse mixte (« LAFON » → « Lafon », « LE GAC »
    → « Le Gac »). Préserve les NOM-NOM (« FIRMIN-LE BODO » →
    « Firmin-Le Bodo »)."""
    if not nom:
        return ""
    # Si déjà casse mixte, on ne touche pas
    if any(c.islower() for c in nom):
        return nom
    return " ".join(
        "-".join(part.capitalize() for part in word.split("-"))
        for word in nom.split()
    )


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.warning("YAML load %s : %s", path, e)
        return {}


def apply_senat_backfill_2024(
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    senat_slugs: dict,
) -> dict[str, int]:
    """R43-E (2026-05-17) — Backfill manuel des questions Sénat sport
    juillet 2024 → avril 2025. Lit `config/sport_backfill_senat_2024.yml`
    et ajoute les compteurs `qe / qosd / qag` aux acteurs concernés.

    Le fichier est saisi manuellement (le scraping web Sénat des
    questions par sénateur retourne du HTML dynamique non parseable
    sans session). Vide par défaut → no-op.
    """
    cfg = _load_yaml(ROOT / "config" / "sport_backfill_senat_2024.yml")
    items = cfg.get("items") or []
    stats: dict[str, int] = defaultdict(int)
    for entry in items:
        if not isinstance(entry, dict):
            continue
        sen = entry.get("senateur") or {}
        prenom = (sen.get("prenom") or "").strip()
        nom = (sen.get("nom") or "").strip()
        if not nom:
            continue
        sid = _ensure_senat_acteur(prenom, nom, registry, senat_slugs)
        c = counters[sid]
        c.acteur_ref = sid
        c.chambre = "Senat"
        qe = int(entry.get("qe") or 0)
        qosd = int(entry.get("qosd") or 0)
        qag = int(entry.get("qag") or 0)
        c.qe += qe
        c.qosd += qosd
        c.qag += qag
        stats["qe"] += qe
        stats["qosd"] += qosd
        stats["qag"] += qag
    if stats:
        log.info("Backfill Sénat 2024 (questions) : %s", dict(stats))
    return dict(stats)


def scan_manual_reports(
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    senat_slugs: dict,
) -> int:
    """R43-B (2026-05-17) — Lit config/sport_reports_manual.yml et
    crédite les auteurs/co-auteurs en `rapport_parlementaire_auteur`.

    Permet de capter les rapports d'information Sénat (invisibles dans
    le flux AKN) — typiquement le rapport « Football-business : stop ou
    encore ? » de Lafon (président) + Savin (rapporteur).
    """
    cfg = _load_yaml(ROOT / "config" / "sport_reports_manual.yml")
    items = cfg.get("items") or []
    n = 0
    for entry in items:
        chamber = (entry.get("chamber") or "").strip()
        date_str = (entry.get("date") or "").strip()
        if date_str and date_str < LEGISLATURE_START:
            continue
        for a in entry.get("auteurs") or []:
            if not isinstance(a, dict):
                continue
            prenom = (a.get("prenom") or "").strip()
            nom = (a.get("nom") or "").strip()
            if not (prenom or nom):
                continue
            if chamber == "Senat":
                sid = _ensure_senat_acteur(prenom, nom, registry, senat_slugs)
            else:
                # AN : on tente match par acteurRef si présent, sinon
                # on saute (les rapports AN passent déjà via le dump).
                ref = (a.get("acteur_ref") or "").strip()
                if ref and ref in registry:
                    sid = ref
                else:
                    continue
            c = counters[sid]
            c.acteur_ref = sid
            c.chambre = chamber if chamber else c.chambre
            c.rapport_parlementaire_auteur += 1
            n += 1
    log.info("Rapports manuels sport : %d crédits", n)
    return n


def apply_membership_credits(
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    senat_slugs: dict,
    an_commission_culture_organe: str = "PO420120",
) -> dict[str, int]:
    """R43-B (2026-05-17) — Applique les crédits d'appartenance :
    - Membre commission Culture (AN ou Sénat)
    - Membre groupe d'étude sport / mission info sport (Sénat
      uniquement, cf. Cyril)

    Sources : YAML manuel + détection AN via mandats AMO10.
    """
    cfg = _load_yaml(ROOT / "config" / "sport_membership_manual.yml")
    stats: dict[str, int] = defaultdict(int)

    # 1. Commission Culture Sénat (YAML)
    for m in cfg.get("commission_culture_senat") or []:
        if not isinstance(m, dict):
            continue
        prenom = (m.get("prenom") or "").strip()
        nom = (m.get("nom") or "").strip()
        sid = _ensure_senat_acteur(prenom, nom, registry, senat_slugs)
        counters[sid].acteur_ref = sid
        counters[sid].chambre = "Senat"
        counters[sid].membre_commission_culture = 1
        stats["commission_culture_senat"] += 1

    # 2. Groupe d'étude sport Sénat (YAML)
    for m in cfg.get("groupe_etude_sport_senat") or []:
        if not isinstance(m, dict):
            continue
        prenom = (m.get("prenom") or "").strip()
        nom = (m.get("nom") or "").strip()
        sid = _ensure_senat_acteur(prenom, nom, registry, senat_slugs)
        counters[sid].acteur_ref = sid
        counters[sid].chambre = "Senat"
        counters[sid].membre_groupe_etude_sport = 1
        stats["groupe_etude_sport_senat"] += 1

    # 3. Commission Culture AN (détection automatique via mandats AMO10)
    # `an_commission_culture_organe` = PO420120 par défaut. Tout député
    # dont l'un des organeRef actifs est cette commission est crédité.
    for ref, acteur in registry.items():
        if acteur.chambre != "AN":
            continue
        if an_commission_culture_organe in (acteur.organes_refs or []):
            counters[ref].acteur_ref = ref
            counters[ref].chambre = "AN"
            counters[ref].membre_commission_culture = 1
            stats["commission_culture_an"] += 1

    # 4. Overrides manuels AN (cas non détectés)
    for m in cfg.get("commission_culture_an") or []:
        if not isinstance(m, dict):
            continue
        ref = (m.get("acteur_ref") or "").strip()
        if ref and ref in registry:
            counters[ref].acteur_ref = ref
            counters[ref].chambre = "AN"
            counters[ref].membre_commission_culture = 1

    log.info("Appartenances sport : %s", dict(stats))
    return dict(stats)


def _ensure_senat_acteur(
    prenom: str, nom: str, registry: dict[str, Acteur],
    senat_slugs: dict, civ: str = "", groupe: str = "",
) -> str:
    """Inscrit ou met à jour le sénateur dans le registre. Retourne son ID.

    R43-D (2026-05-17) — Dédup robuste : si `prenom` est vide (cas CSV
    amendements Sénat où seul le NOM est exposé), on cherche d'abord
    dans le registry existant un sénateur ayant le même NOM normalisé.
    Si trouvé, on retourne SON id existant (évite la duplication
    « M. SAVIN » à côté de « Michel SAVIN »).
    """
    # Dédup par NOM seul si prénom absent
    nom_norm = _normalize_name(nom) if nom else ""
    if not prenom and nom:
        for ref, acteur in registry.items():
            if acteur.chambre != "Senat":
                continue
            if _normalize_name(acteur.nom) == nom_norm:
                return ref
    sid = _senat_id(prenom, nom)
    # Normalisation du NOM en casse mixte pour l'affichage
    nom_display = _titlecase_nom(nom)
    prenom_display = prenom.strip() if prenom else ""
    # Dédup symétrique : si on insère avec prénom mais qu'un acteur SANS
    # prénom (créé via CSV amdt qui n'expose que le NOM) a déjà été
    # enregistré avec le même NOM, on ENRICHIT l'acteur existant avec le
    # prénom plutôt que de créer un doublon. Le sid de l'acteur existant
    # est conservé pour ne pas casser les compteurs déjà incrémentés.
    if prenom and nom and sid not in registry:
        for ref, acteur in registry.items():
            if acteur.chambre != "Senat":
                continue
            if not acteur.prenom and _normalize_name(acteur.nom) == nom_norm:
                acteur.prenom = prenom_display
                if civ and not acteur.civ:
                    acteur.civ = civ
                if groupe and not acteur.groupe_abrege:
                    acteur.groupe_abrege = groupe
                # Enrichit aussi photo/fiche depuis slugs si non posés
                if not acteur.photo_url:
                    photo, fiche = _senat_meta_from_slugs(
                        prenom, nom, senat_slugs,
                    )
                    if photo:
                        acteur.photo_url = photo
                    if fiche:
                        acteur.fiche_url = fiche
                return ref
    if sid not in registry:
        photo, fiche = _senat_meta_from_slugs(prenom, nom, senat_slugs)
        # R43-D bis : si prénom absent, tenter de le récupérer depuis
        # senat_slugs.json (qui expose `prenom_usuel`). Évite les noms
        # bruts type « Kern » / « Folliot » dans le top final.
        if not prenom_display:
            slug_entry = senat_slugs.get(nom_norm)
            if isinstance(slug_entry, dict):
                p_usuel = (slug_entry.get("prenom_usuel") or "").strip()
                if p_usuel:
                    prenom_display = p_usuel
        registry[sid] = Acteur(
            acteur_ref=sid, chambre="Senat",
            civ=civ, prenom=prenom_display, nom=nom_display,
            groupe_abrege=groupe, photo_url=photo, fiche_url=fiche,
        )
    else:
        # Compléter les champs manquants
        a = registry[sid]
        if not a.groupe_abrege and groupe:
            a.groupe_abrege = groupe
        if not a.civ and civ:
            a.civ = civ
        if not a.prenom and prenom_display:
            a.prenom = prenom_display
        # Préfère le NOM en casse mixte si nouveau plus propre
        if a.nom.isupper() and not nom_display.isupper():
            a.nom = nom_display
    return sid


# ---------------------------------------------------------------------------
# 4a. Sénat — questions via CSV
# ---------------------------------------------------------------------------

def scan_senat_questions_csv(
    csv_path: Path, matcher: KeywordMatcher,
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    senat_slugs: dict,
    since: str = LEGISLATURE_START,
) -> int:
    """Parse `questions-depuis-un-an.csv` ou `qg.csv` Sénat.

    Filtre : date publication JO ≥ `since`. Match sport sur titre + thème.
    """
    import csv as _csv
    n = 0
    # CSV Sénat encodé ISO-8859-1 / Latin-1 historiquement
    with open(csv_path, encoding="latin-1") as f:
        reader = _csv.DictReader(f, delimiter=";")
        for r in reader:
            date_jo = (r.get("Date de publication JO") or "")[:10]
            if not date_jo or date_jo < since:
                continue
            hay_parts = [
                r.get("Titre") or "",
                r.get("Thème(s)") or "",
                r.get("Thème QC") or "",
                r.get("Ministère de réponse") or "",
            ]
            matched, _ = matcher.match(" ".join(hay_parts))
            if not matched:
                continue
            nom = (r.get("Nom") or "").strip()
            prenom = (r.get("Prénom") or "").strip()
            if not (nom and prenom):
                continue
            nature = (r.get("Nature") or "QE").upper()
            sid = _ensure_senat_acteur(
                prenom, nom, registry, senat_slugs,
                civ=(r.get("Civilité") or ""),
                groupe=(r.get("Groupe") or ""),
            )
            c = counters[sid]
            c.acteur_ref = sid
            c.chambre = "Senat"
            if nature == "QG":
                c.qag += 1
            elif nature == "QOSD":
                c.qosd += 1
            else:
                c.qe += 1
            n += 1
    log.info("Questions Sénat sport (≥ %s, %s) : %d",
             since, csv_path.name, n)
    return n


# ---------------------------------------------------------------------------
# 4b. Sénat — PPL / PPR / PJL via Akoma Ntoso
# ---------------------------------------------------------------------------

# Regex de découpe d'un showAs Sénat type :
#   "Par Mme Agnès FIRMIN-LE BODO, MM. Philippe BONNECARRERE, Alain DAVID, ...,
#    Mmes Sandrine LE FEUR, Liliane TANGUY, ..., et M. Vincent CAURE,
#    Députés"
# Pattern : <civilité> <prénom-éventuel> <NOM_EN_MAJUSCULES>
# La civilité distribue sur les noms qui suivent jusqu'au prochain titre.
_SENAT_SIGNATAIRE_RE = re.compile(
    r"(?:^|,|\s+et\s+)\s*"
    r"(?P<civ>Mme|MM\.|M\.|Mmes)\s+"
    r"(?P<rest>[^,]+?)"
    r"(?=,|\s+et\s+|\s+(?:Mme|MM\.|M\.|Mmes)\s+|$)",
    re.IGNORECASE,
)


def _parse_senat_signataires(show_as: str) -> list[tuple[str, str, str]]:
    """Extrait [(civ, prenom, NOM)] depuis un showAs AKN Sénat.

    Heuristique : civilité (M./Mme/MM./Mmes) suivie d'un ou plusieurs
    noms séparés par virgules. Le NOM est en MAJUSCULES (convention
    Sénat AKN), le prénom le précède en casse mixte. Si le bloc commence
    sans prénom (cas « MM. BONNECARRERE, DAVID, ... »), le prénom est
    vide.
    """
    if not show_as:
        return []
    # Strip "Par " initial et terminaison "Députés"/"Sénateurs"
    txt = re.sub(r"^Par\s+", "", show_as.strip())
    txt = re.sub(r",\s*(D[ée]put[ée]s|S[ée]nateurs).*$", "", txt)

    out: list[tuple[str, str, str]] = []
    current_civ = ""
    # Tokenize par virgules ou " et "
    parts = re.split(r"\s*(?:,|\s+et\s+)\s*", txt)
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Détecte une civilité en tête
        m_civ = re.match(r"^(Mmes|Mme|MM\.|M\.)\s+(.+)$", p)
        if m_civ:
            current_civ = m_civ.group(1)
            rest = m_civ.group(2)
        else:
            rest = p
        # Tente de découper Prénom NOM
        # NOM = séquence de mots en majuscules (avec accents possibles, tirets)
        m_name = re.match(
            r"^(?P<prenom>[A-ZÉÈÊËÀÂÄÎÏÔÖÙÛÜÇŒ][a-zéèêëàâäîïôöùûüçœ\-']+(?:\s+[A-ZÉÈÊËÀÂÄÎÏÔÖÙÛÜÇŒ][a-zéèêëàâäîïôöùûüçœ\-']+)*)?\s*"
            r"(?P<nom>[A-ZÉÈÊËÀÂÄÎÏÔÖÙÛÜÇŒ\-']+(?:\s+[A-ZÉÈÊËÀÂÄÎÏÔÖÙÛÜÇŒ\-']+)*)$",
            rest,
        )
        if m_name:
            prenom = (m_name.group("prenom") or "").strip()
            nom = m_name.group("nom").strip()
            out.append((current_civ, prenom, nom))
    return out


def _fetch_senat_amdt_csv_urls_from_dl(slug: str) -> list[str]:
    """R43-D / R43-E (2026-05-17) — Extrait les URLs CSV d'amendements
    (commission + séance + CMP) depuis la page dossier législatif Sénat.

    Stratégie : parser tous les liens vers `accueil.html` d'amendements
    et dériver l'URL CSV correspondante. Le `accueil.html` indique le
    contexte (commission/2024-2025/630/ vs /2024-2025/734/ pour séance,
    /2025-2026/307/ pour CMP). À partir du chemin on construit l'URL
    CSV (`jeu_complet_commission_<session>_<num>.csv` côté commission,
    `jeu_complet_<session>_<num>.csv` côté séance/CMP).
    """
    if not slug:
        return []
    local = CACHE_DIR / "senat_dossiers_dl" / f"{slug}.html"
    if not local.exists():
        return []
    try:
        h = local.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    urls: set[str] = set()

    # Cas 1 : lien jeu_complet direct (existant dans la page)
    for m in re.finditer(r'href="([^"]*jeu_complet[^"]*\.csv)"', h):
        u = m.group(1)
        if not u.startswith("http"):
            if u.startswith("/"):
                u = "https://www.senat.fr" + u
            else:
                continue
        urls.add(u)

    # Cas 2 : R43-I (2026-05-17) — dériver depuis TOUT lien
    # `/amendements/...` (la page DL ne pose pas toujours `accueil.html`
    # pour la commission, parfois juste `liste_adoptes_ordre_discussion.html`
    # ou `liste_alpha.html`). On capte donc tout chemin du type
    # `/amendements/[commissions/]<session>/<num>/<anything>` et on
    # déduplique sur (session, num, is_commission).
    seen_keys: set[tuple[str, str, bool]] = set()
    for m in re.finditer(
        r'href="/amendements/(commissions/)?(\d{4}-\d{4})/(\d+)/[^"]+"',
        h,
    ):
        is_commission = bool(m.group(1))
        session = m.group(2)
        num = m.group(3)
        key = (session, num, is_commission)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if is_commission:
            csv_path = (
                f"/amendements/commissions/{session}/{num}/"
                f"jeu_complet_commission_{session}_{num}.csv"
            )
        else:
            csv_path = (
                f"/amendements/{session}/{num}/"
                f"jeu_complet_{session}_{num}.csv"
            )
        urls.add(f"https://www.senat.fr{csv_path}")

    return sorted(urls)


# R43-J (2026-05-17) — Slugs Sénat des PLF à scanner pour les amdt
# crédits sport. À enrichir d'année en année (pjlf2025, pjlf2024,
# pjlf2027…). Le PLF n'est pas un « dossier sport » au sens du titre,
# mais une partie de ses amdt concernent les crédits Mission Sport.
SENAT_PLF_SLUGS = ["pjlf2026"]


def scan_senat_all_pjl_amdt(
    akn_index_path: Path | None,
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    senat_slugs: dict,
    since: str = LEGISLATURE_START,
    exclude_slugs: set[str] | None = None,
) -> dict[str, int]:
    """R43-K (2026-05-17) — Scanne TOUS les PJL Sénat depuis 2024-07
    pour capter les amdt sport-relevant, même sur textes non-sport.

    Cyril : « tous les amendements sports, y compris sur d'autres
    textes ». Couvre par exemple un amdt sport déposé sur la PJL
    Sécurité civile, qui ne serait pas capté par le scope élargi
    dosleg (le dosleg sécurité civile ne matche pas sport).

    Approche : lister les PJL via AKN, fetch leur page DL Sénat, puis
    scanner les CSV amdt avec filtre strict (`_PLF_SPORT_RE`). Coût :
    ~173 PJL × 2-3 CSV moyens = ~400 fetches. Cache local idempotent.
    """
    if not akn_index_path or not akn_index_path.exists():
        return {}
    from xml.etree import ElementTree as ET
    try:
        tree = ET.parse(akn_index_path)
    except Exception:
        return {}
    root = tree.getroot()
    pjl_slugs: set[str] = set()
    for txt_node in root.findall("text"):
        url_node = txt_node.find("url")
        date_node = txt_node.find("lastModifiedDateTime")
        if url_node is None or date_node is None:
            continue
        if (date_node.text or "")[:10] < since:
            continue
        fname = (url_node.text or "").split("/")[-1]
        # On garde uniquement les PJL (= projets de loi). Les PPL/PPR
        # sport sont déjà couvertes via scope élargi.
        if not fname.startswith("pjl"):
            continue
        # Récupère le signet dl-senat depuis le fichier AKN
        local_akn = CACHE_DIR / "senat_akn_files" / fname
        if not local_akn.exists():
            continue
        try:
            akn_tree = ET.parse(local_akn)
        except Exception:
            continue
        NS = "{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}"
        dl_slug = ""
        for fra in akn_tree.iter(f"{NS}FRBRalias"):
            if fra.get("name") == "signet-dossier-legislatif-senat":
                dl_slug = fra.get("value", "")
                break
        if dl_slug:
            pjl_slugs.add(dl_slug)

    # Exclure les slugs déjà couverts (PLF, dosleg sport scope élargi)
    skip = set(SENAT_PLF_SLUGS)
    if exclude_slugs:
        skip |= exclude_slugs
    pjl_slugs -= skip
    if not pjl_slugs:
        return {}

    total_stats: dict[str, int] = defaultdict(int)
    n_scanned = 0
    for slug in sorted(pjl_slugs):
        before = dict(total_stats)
        s = scan_senat_plf_amdt(slug, counters, registry, senat_slugs)
        for k, v in s.items():
            total_stats[k] += v
        n_scanned += 1
    log.info(
        "Sénat : %d PJL scannés (content-based sport) : %s",
        n_scanned, dict(total_stats),
    )
    return dict(total_stats)

# Regex sport ÉLARGIE pour le contenu PLF (Subdivision + Dispositif +
# Objet). Plus permissive que le KeywordMatcher (qui exige des
# expressions composées), car le langage budgétaire est codé (« mission
# Sport », « programme 219 », etc.).
_PLF_SPORT_RE = re.compile(
    r"\b(sport(?:if|ive)?s?|olympique|paralympique|jop?\b|jeunesse"
    r"|f[ée]d[ée]r(?:ation|al).{0,30}sport|associations?\s+sport|club\s+sport"
    r"|ans[\s.,]|cnosf|cpsf|inj[eé]p|insep|crep[s]?|sport[\s-]sant[ée]"
    r"|sportifs?\s+(?:de\s+)?haut\s+niveau|mission\s+sport"
    r"|programme\s+(?:219|350|163)\b)",
    re.IGNORECASE,
)


def scan_senat_plf_amdt(
    plf_slug: str,
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    senat_slugs: dict,
) -> dict[str, int]:
    """R43-J (2026-05-17) — Scan les amdt PLF avec filtre STRICT sport.

    Cyril : « je doute également que tous les amendements déposés au
    PLF 2026 sur les crédits sports aient été comptabilisés ».

    Le PLF n'est pas un dossier sport globalement (titre = « Budget
    2026 »), donc le scope élargi (= compter tous les amdt du dosleg)
    serait trompeur. On filtre amdt par amdt sur Subdivision +
    Dispositif + Objet via `_PLF_SPORT_RE`.

    Volume observé PLF 2026 séance Sénat : 5156 amdt total, ~560
    sport-relevant. Dédupliqué par auteur via `_ensure_senat_acteur`.
    """
    import csv as _csv, io as _io
    # R43-K : certains amdt Sénat ont un dispositif > 131k chars
    # (default csv field limit) → bump explicit.
    try:
        _csv.field_size_limit(10 * 1024 * 1024)  # 10 MB
    except OverflowError:
        pass
    # R43-J : pré-fetch la page DL Sénat (le PLF n'est pas traité par
    # `scan_senat_akn` donc sa page DL n'est pas en cache local).
    dl_html_path = CACHE_DIR / "senat_dossiers_dl" / f"{plf_slug}.html"
    dl_html_path.parent.mkdir(parents=True, exist_ok=True)
    if not dl_html_path.exists():
        _fetch_via_curl(
            f"https://www.senat.fr/dossier-legislatif/{plf_slug}.html",
            dl_html_path, timeout=30,
        )
    urls = _fetch_senat_amdt_csv_urls_from_dl(plf_slug)
    if not urls:
        log.info("Sénat PLF %s : aucun CSV trouvé", plf_slug)
        return {}
    stats: dict[str, int] = defaultdict(int)
    n_total = 0
    n_sport = 0
    for url in urls:
        fname = url.rstrip("/").rsplit("/", 1)[-1]
        local = CACHE_DIR / "senat_amdt_csv" / f"{plf_slug}__{fname}"
        local.parent.mkdir(parents=True, exist_ok=True)
        if not local.exists():
            if not _fetch_via_curl(url, local, timeout=60):
                continue
        try:
            raw = local.read_text(encoding="latin-1", errors="ignore")
        except Exception:
            continue
        if raw.startswith("sep="):
            raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        reader = _csv.DictReader(_io.StringIO(raw), delimiter="\t")
        for r in reader:
            n_total += 1
            # Filtre sport sur Subdivision + Dispositif + Objet
            def _col(*candidates: str) -> str:
                for c in candidates:
                    for k in r.keys():
                        if k is None:
                            continue
                        if str(k).strip() == c:
                            return (r.get(k) or "").strip()
                return ""

            subdiv = _col("Subdivision")
            dispo = _col("Dispositif")
            objet = _col("Objet")
            haystack = f"{subdiv} {dispo} {objet}"
            if not _PLF_SPORT_RE.search(haystack):
                continue
            n_sport += 1
            auteur = _col("Auteur")
            sort = _col("Sort")
            if not auteur:
                continue
            # Format auteur (idem que CSV PPL)
            m_auth = re.match(
                r"^(M\.|MM\.|Mme|Mmes)\s+(.+?)(?:,\s*(?:rapporteur|s[ée]nateur|d[ée]put[ée])e?.*)?$",
                auteur,
            )
            if not m_auth:
                continue
            full = m_auth.group(2).strip()
            tokens = full.split()
            nom_parts: list[str] = []
            prenom_parts: list[str] = []
            for t in tokens:
                alpha = [c for c in t if c.isalpha()]
                if alpha and all(c.isupper() for c in alpha):
                    nom_parts.append(t)
                else:
                    if nom_parts:
                        nom_parts.append(t)
                    else:
                        prenom_parts.append(t)
            if not nom_parts:
                continue
            nom = " ".join(nom_parts)
            prenom = " ".join(prenom_parts).strip()
            if not any(c.isupper() for c in nom):
                continue
            sid = _ensure_senat_acteur(prenom, nom, registry, senat_slugs)
            c = counters[sid]
            c.acteur_ref = sid
            c.chambre = "Senat"
            c.amdt_depose += 1
            stats["amdt_depose"] += 1
            if "adopt" in sort.lower():
                c.amdt_adopte += 1
                stats["amdt_adopte"] += 1
    log.info(
        "Sénat PLF %s : %d amdt scannés, %d sport-relevant (%s)",
        plf_slug, n_total, n_sport, dict(stats),
    )
    return dict(stats)


def scan_senat_amdt_csv_for_dl(
    dl_slug: str,
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    senat_slugs: dict,
) -> dict[str, int]:
    """R43-D (2026-05-17) — Scrape et parse les CSV d'amendements Sénat
    pour un dossier législatif sport donné.

    Couvre les amdt commission + séance. Format CSV particulier (1ère
    ligne `sep=\\n`, puis TAB-separated, encodage latin-1).

    Cyril 2026-05-17 : « Savin n'a pas encore tous ses amendements
    comptés sur la PPL Sport pro ». Diagnostic : la DB veille ne couvre
    que ~6 mois ; les amdt commission Sénat PPL 456 (mai 2025) sont
    HORS périmètre. Ce scraping CSV par texte les ramène en intégralité.
    """
    import csv as _csv, io as _io
    try:
        _csv.field_size_limit(10 * 1024 * 1024)
    except OverflowError:
        pass
    urls = _fetch_senat_amdt_csv_urls_from_dl(dl_slug)
    stats: dict[str, int] = defaultdict(int)
    if not urls:
        # Fallback : on tente de construire les URLs standard à partir
        # du slug (ex. ppl24-456 → session=2024-2025, num=456).
        m = re.match(r"ppl(\d{2})-(\d+)", dl_slug)
        if m:
            yy = int(m.group(1))
            num = m.group(2)
            sess = f"20{yy}-20{yy + 1:02d}"
            urls = [
                f"https://www.senat.fr/amendements/commissions/{sess}/{num}/jeu_complet_commission_{sess}_{num}.csv",
                f"https://www.senat.fr/amendements/{sess}/{num}/jeu_complet_{sess}_{num}.csv",
            ]

    for url in urls:
        # Cache local
        fname = url.rstrip("/").rsplit("/", 1)[-1]
        local = CACHE_DIR / "senat_amdt_csv" / f"{dl_slug}__{fname}"
        local.parent.mkdir(parents=True, exist_ok=True)
        if not local.exists():
            if not _fetch_via_curl(url, local, timeout=30):
                continue
        try:
            raw = local.read_text(encoding="latin-1", errors="ignore")
        except Exception:
            continue
        # Format Sénat : 1re ligne "sep=\n", puis TAB-separated
        if raw.startswith("sep="):
            raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        if not raw.strip():
            continue
        reader = _csv.DictReader(_io.StringIO(raw), delimiter="\t")
        for r in reader:
            # Les noms de colonnes Sénat ont des espaces traînants
            # (« Auteur », « Sort », etc.)
            def _col(*candidates: str) -> str:
                for c in candidates:
                    for k in r.keys():
                        if k is None:
                            continue
                        if str(k).strip() == c:
                            val = r.get(k)
                            return (val or "").strip()
                return ""

            auteur = _col("Auteur")
            sort = _col("Sort")
            if not auteur:
                continue
            # Format auteur Sénat (CSV par texte) :
            #   « M. SAVIN, rapporteur »
            #   « MM. Jean-Michel ARNAUD, sénateur, Christophe PROENÇA, député »
            #   « Mme OLLIVIER »
            # Le NOM est typiquement en MAJUSCULES, parfois précédé d'un
            # prénom en casse mixte. Le suffixe « , rapporteur / sénateur /
            # député » est ignoré.
            m_auth = re.match(
                r"^(M\.|MM\.|Mme|Mmes)\s+(.+?)(?:,\s*(?:rapporteur|s[ée]nateur|d[ée]put[ée])e?.*)?$",
                auteur,
            )
            if not m_auth:
                continue
            full = m_auth.group(2).strip()
            # R43-I (2026-05-17) — Découpe Prénom / NOM. Le NOM est
            # composé de mots en MAJUSCULES (peuvent contenir tirets et
            # accents). Le prénom le précède en casse mixte.
            tokens = full.split()
            nom_parts: list[str] = []
            prenom_parts: list[str] = []
            for t in tokens:
                alpha = [c for c in t if c.isalpha()]
                if alpha and all(c.isupper() for c in alpha):
                    nom_parts.append(t)
                else:
                    if nom_parts:
                        # Particule type "DE", "LE" intercalée → nom
                        nom_parts.append(t)
                    else:
                        prenom_parts.append(t)
            if not nom_parts:
                continue
            nom = " ".join(nom_parts)
            prenom = " ".join(prenom_parts).strip()
            if not any(c.isupper() for c in nom):
                continue
            sid = _ensure_senat_acteur(prenom, nom, registry, senat_slugs)
            c = counters[sid]
            c.acteur_ref = sid
            c.chambre = "Senat"
            c.amdt_depose += 1
            stats["amdt_depose"] += 1
            if "adopt" in sort.lower():
                c.amdt_adopte += 1
                stats["amdt_adopte"] += 1
    if stats:
        log.info("Sénat amdt CSV %s : %s", dl_slug, dict(stats))
    return dict(stats)


def _fetch_senat_groupe_from_fiche(slug: str) -> tuple[str, str]:
    """R43-F (2026-05-17) — Scrape la fiche sénateur pour extraire le
    groupe politique (abrégé + nom complet).

    Le AKN Sénat ne porte pas le groupe et les CSV (questions) ne le
    contiennent que pour les sénateurs ayant posé une question récente.
    Pour Lafon / Savin / Malhuret / Arnaud (tous actifs sport mais pas
    sur les Q récentes), la fiche est la seule source.

    Pattern observé : `<img src="/assets/images/partagees/groupes/<ABR>.webp"
    alt="Groupe <Nom complet> au Sénat">`.

    Retourne `("", "")` si pas de slug ou échec.
    """
    if not slug:
        return "", ""
    local = CACHE_DIR / "senat_fiches" / f"{slug}.html"
    local.parent.mkdir(parents=True, exist_ok=True)
    if not local.exists():
        url = f"https://www.senat.fr/senateur/{slug}.html"
        if not _fetch_via_curl(url, local, timeout=20):
            return "", ""
    try:
        h = local.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "", ""
    # Pattern : src="/assets/images/partagees/groupes/<ABR>.webp" alt="Groupe <Nom> au Sénat"
    m = re.search(
        r'src="/assets/images/partagees/groupes/(?P<abr>[A-Z][A-Z0-9\-]+)\.webp"'
        r'[^>]*alt="Groupe\s+(?P<nom>[^"]+?)\s+au\s+S[ée]nat"',
        h,
    )
    # R43-G : map abrégé URL Sénat → abrégé d'affichage usuel.
    # Le Sénat garde « UMP » dans le slug image pour Les Républicains
    # (héritage historique), c'est trompeur côté lecteur.
    _ABR_MAP = {"UMP": "LR"}
    if m:
        abr = _ABR_MAP.get(m.group("abr"), m.group("abr"))
        return abr, m.group("nom")
    # Fallback : juste le nom dans le alt (sans l'abrégé fiable)
    m2 = re.search(r'alt="Groupe\s+([^"]+?)\s+au\s+S[ée]nat"', h)
    if m2:
        return "", m2.group(1)
    return "", ""


def _enrich_senat_groupes(
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    senat_slugs: dict,
    top_n: int = 50,
) -> None:
    """R43-F (2026-05-17) — Pour chaque sénateur dans le top N actuel
    sans groupe politique posé, scrape sa fiche pour le récupérer.

    Limité au top N pour ne pas saturer en fetches sur 348 sénateurs
    quand seuls quelques-uns sont visibles côté UI. Cache local.
    """
    # Tri par score desc → top N
    ranked = sorted(
        [(ref, c.score()) for ref, c in counters.items()
         if registry.get(ref) and registry[ref].chambre == "Senat"
         and c.score() > 0],
        key=lambda x: -x[1],
    )[:top_n]
    enriched = 0
    for ref, _score in ranked:
        acteur = registry[ref]
        if acteur.groupe_abrege:
            continue
        nom_norm = _normalize_name(acteur.nom)
        slug_entry = senat_slugs.get(nom_norm)
        if not isinstance(slug_entry, dict):
            # fallback : essai avec prenom+nom
            slug_entry = senat_slugs.get(
                _normalize_name(f"{acteur.prenom}{acteur.nom}")
            )
        if not isinstance(slug_entry, dict):
            continue
        slug = slug_entry.get("slug") or ""
        if not slug:
            continue
        abr, nom_long = _fetch_senat_groupe_from_fiche(slug)
        if abr:
            acteur.groupe_abrege = abr
            acteur.groupe_long = nom_long
            enriched += 1
        elif nom_long:
            acteur.groupe_long = nom_long
            enriched += 1
    if enriched:
        log.info("Enrichissement groupes Sénat : %d sénateurs", enriched)


def _fetch_senat_rapporteurs_from_dl(slug: str) -> list[tuple[str, str]]:
    """R43-A ter (2026-05-17) — Scrape la page dossier législatif Sénat
    pour extraire les rapporteurs (sénateurs auteurs des rapports `r24-*`).

    Le AKN Sénat (`depots.xml`) liste les **textes déposés** (PPL/PPR/PJL)
    mais PAS les rapports. Les rapporteurs sont donc invisibles depuis
    le flux AKN. Pour les récupérer, on fetch la page HTML du dossier
    législatif (forme stable depuis des années) et on parse les blocs
    `<a href='/rap/...'>Rapport</a> ... de M. <a href="/senateur/<slug>">
    Prénom NOM</a>`.

    Cyril 2026-05-17 : « pourtant il s'agit de Savin et Lafon, surtout
    le premier ». Cas concret PPL Sport pro (ppl24-456) : Lafon est
    1er signataire (capté via AKN) mais Savin est rapporteur — invisible
    sans ce scraping.

    Retourne `[(prenom, nom)]` (peut être vide si pas de rapport).
    """
    if not slug:
        return []
    url = f"https://www.senat.fr/dossier-legislatif/{slug}.html"
    local = CACHE_DIR / "senat_dossiers_dl" / f"{slug}.html"
    local.parent.mkdir(parents=True, exist_ok=True)
    if not local.exists():
        if not _fetch_via_curl(url, local, timeout=20):
            return []
    try:
        h = local.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    pattern = re.compile(
        r'<a\s+href=["\']/rap/[^"\']+["\'][^>]*>\s*Rapport\s*</a>'
        r'.{0,400}?<a\s+href="/senateur/[^"]+"\s*>([^<]+)</a>',
        re.S,
    )
    for m in pattern.finditer(h):
        label = m.group(1).strip()
        # Format type : "Prénom NOM" ou "Prénom-Composé NOM"
        # Le NOM est en MAJUSCULES (convention Sénat), le prénom en
        # casse mixte. On découpe au 1er token MAJUSCULES.
        parts = label.split()
        prenom_parts: list[str] = []
        nom_parts: list[str] = []
        for p in parts:
            # Considère MAJUSCULES si tous caractères alpha sont majuscules
            alpha = [c for c in p if c.isalpha()]
            if alpha and all(c.isupper() for c in alpha):
                nom_parts.append(p)
            else:
                if nom_parts:
                    # On a déjà rencontré du NOM, mais ce token n'est pas
                    # majuscule → probablement un nom composé partiel
                    # ("DE", "LE", etc.). On l'ajoute aux nom_parts.
                    nom_parts.append(p)
                else:
                    prenom_parts.append(p)
        if not nom_parts:
            continue
        prenom = " ".join(prenom_parts).strip()
        nom = " ".join(nom_parts).strip()
        if nom:
            out.append((prenom, nom))
    # Dédup
    seen = set()
    uniq = []
    for prenom, nom in out:
        key = (prenom.lower(), nom.lower())
        if key in seen:
            continue
        seen.add(key)
        uniq.append((prenom, nom))
    return uniq


def scan_senat_akn(
    akn_index_path: Path, matcher: KeywordMatcher,
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    senat_slugs: dict,
    since: str = LEGISLATURE_START,
    dl_slugs_scope_elargi_out: set[str] | None = None,
) -> dict[str, int]:
    """Parse l'index Akoma Ntoso Sénat (`depots.xml`) puis pour chaque
    texte récent dépose les signataires.

    Une PPL/PPR Sénat liste tous ses signataires dans `TLCPerson.showAs`
    sous forme libre. Le premier nommé est le 1er signataire (premiers
    pts) ; les autres sont cosignataires.
    """
    from xml.etree import ElementTree as ET

    stats: dict[str, int] = defaultdict(int)
    # Parse index
    try:
        tree = ET.parse(akn_index_path)
    except Exception as e:
        log.warning("AKN index parse error : %s", e)
        return {}
    root = tree.getroot()

    # Cache des fichiers .akn.xml individuels
    akn_files_dir = CACHE_DIR / "senat_akn_files"
    akn_files_dir.mkdir(parents=True, exist_ok=True)

    # R43-A ter (2026-05-17) — Dédoublonnage par dossier législatif Sénat.
    # Un même dossier (ex. PJL JO 2030 = pjl24-630) apparaît dans plusieurs
    # fichiers AKN (étapes successives : dépôt 1re lecture, retour navette,
    # CMP, etc.). Sans dédup, Arnaud (rapporteur 1 fois sur pjl24-630)
    # serait crédité 4× (= nb d'étapes capturées).
    dl_slugs_scored: set[str] = set()

    # Fetch chaque texte de l'index (filtré par date)
    n_total = 0
    n_sport = 0
    for txt_node in root.findall(".//{*}text") + root.findall("text"):
        url_node = txt_node.find("{*}url") or txt_node.find("url")
        date_node = txt_node.find("{*}lastModifiedDateTime") or txt_node.find("lastModifiedDateTime")
        if url_node is None or date_node is None:
            continue
        url = url_node.text or ""
        date_str = (date_node.text or "")[:10]
        # Filtre brut : on prend tous les textes, le vrai date dépot est dans le .akn.xml
        # mais on peut pré-filtrer sur lastModified (≥ since)
        if date_str < since:
            continue
        # Nom de fichier court pour cache
        local_name = url.split("/")[-1]
        local_path = akn_files_dir / local_name
        if not local_path.exists():
            if not _fetch_via_curl(url, local_path, timeout=30):
                continue
        n_total += 1
        # Parse le fichier individuel
        try:
            akn_tree = ET.parse(local_path)
        except Exception:
            continue
        akn_root = akn_tree.getroot()
        # Namespace AKN 3.0
        NS = {"akn": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}

        # 1) Titre/alias
        titre_alias = ""
        for fra in akn_root.iter("{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}FRBRalias"):
            if fra.get("name") == "intitule-court":
                titre_alias = fra.get("value", "")
                break
        # 2) Date dépôt
        depot_date = ""
        for d in akn_root.iter("{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}FRBRdate"):
            if d.get("name") == "#presentation":
                depot_date = d.get("date", "")
                break
        if depot_date and depot_date < since:
            continue
        # 3) Type (ppl/ppr/pjl/...)
        type_doc = ""
        for bill in akn_root.iter("{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}bill"):
            type_doc = bill.get("name", "")
            break

        # 4) Sport ? Titre matche
        matched, _ = matcher.match(titre_alias)
        if not matched:
            continue
        n_sport += 1

        # Slug dossier législatif Sénat (utilisé pour scraping rapporteurs
        # ET pour dédupliquer le scoring par dossier)
        dl_slug = ""
        for fra in akn_root.iter("{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}FRBRalias"):
            if fra.get("name") == "signet-dossier-legislatif-senat":
                dl_slug = fra.get("value", "")
                break
        # Dédup par dl_slug : on ne score qu'une fois par dossier législatif
        if dl_slug and dl_slug in dl_slugs_scored:
            continue
        if dl_slug:
            dl_slugs_scored.add(dl_slug)
            if dl_slugs_scope_elargi_out is not None:
                dl_slugs_scope_elargi_out.add(dl_slug)

        # Pré-fetch la page dossier législatif Sénat (utilisé pour
        # détection promulgation + extraction rapporteurs). Idempotent
        # (cache local).
        if dl_slug:
            dl_html_path = CACHE_DIR / "senat_dossiers_dl" / f"{dl_slug}.html"
            dl_html_path.parent.mkdir(parents=True, exist_ok=True)
            if not dl_html_path.exists():
                _fetch_via_curl(
                    f"https://www.senat.fr/dossier-legislatif/{dl_slug}.html",
                    dl_html_path, timeout=20,
                )

        # 5) Signataires
        # Le ref auteur est sur FRBRauthor as=#auteur, pointe vers un TLCPerson eId
        author_eid = ""
        for fa in akn_root.iter("{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}FRBRauthor"):
            if fa.get("as") == "#auteur":
                href = fa.get("href", "")
                author_eid = href.lstrip("#")
                break
        # Cherche le TLCPerson correspondant
        signataires: list[tuple[str, str, str]] = []
        for tlc in akn_root.iter("{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}TLCPerson"):
            if tlc.get("eId") == author_eid:
                show_as = tlc.get("showAs", "")
                signataires = _parse_senat_signataires(show_as)
                break

        # 6) Promulgué ? Critère strict Cyril : « n'est adopté qu'un
        # texte promulgué ». On vérifie via le workflow AKN ET via la
        # page dossier législatif Sénat (qui mentionne "Loi n° XXXX" si
        # promulguée).
        is_promulgated = False
        for step in akn_root.iter("{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}step"):
            outcome = (step.get("outcome") or "").lower()
            if "promulguée" in outcome or "promulgu" in outcome:
                is_promulgated = True
                break
        # Fallback : la page HTML dossier mentionne la promulgation
        if not is_promulgated and dl_slug:
            dl_html = CACHE_DIR / "senat_dossiers_dl" / f"{dl_slug}.html"
            if dl_html.exists():
                try:
                    h = dl_html.read_text(encoding="utf-8", errors="ignore")
                    if re.search(r"Loi\s+n\s*°\s*\d+", h, re.I) or "promulgu" in h.lower():
                        is_promulgated = True
                except Exception:
                    pass

        # 7) Scoring
        for i, (civ, prenom, nom) in enumerate(signataires):
            if not nom:
                continue
            sid = _ensure_senat_acteur(prenom, nom, registry, senat_slugs, civ=civ)
            c = counters[sid]
            c.acteur_ref = sid
            c.chambre = "Senat"
            is_first = (i == 0)
            if type_doc == "ppl":
                if is_first:
                    c.ppl_premier_signataire += 1
                    stats["ppl_premier_signataire"] += 1
                    if is_promulgated:
                        c.texte_adopte_premier_signataire += 1
                        stats["texte_adopte_premier_signataire"] += 1
                else:
                    c.ppl_signataire += 1
                    stats["ppl_signataire"] += 1
            elif type_doc in ("ppr", "pre"):  # proposition de résolution
                c.resolution_signataire += 1
                stats["resolution_signataire"] += 1
            # pjl : initiative gouv, on ne crédite pas de pts au sénateur

        # 8) Rapporteurs Sénat (depuis la page dossier législatif)
        # R43-A ter (2026-05-17) : Cyril a fait remarquer que Savin
        # (rapporteur PPL Sport pro Sénat) était absent du top. Le AKN
        # ne porte pas les rapports, on les récupère par scraping HTML.
        for prenom_r, nom_r in _fetch_senat_rapporteurs_from_dl(dl_slug):
            sid_r = _ensure_senat_acteur(prenom_r, nom_r, registry, senat_slugs)
            c_r = counters[sid_r]
            c_r.acteur_ref = sid_r
            c_r.chambre = "Senat"
            c_r.rapporteur_principal += 1
            stats["rapporteur_principal"] += 1

        # 9) Amendements Sénat (commission + séance) via CSV par texte
        # R43-D (2026-05-17) — APRÈS les rapporteurs (qui apportent le
        # prénom complet) pour que le dédup `_ensure_senat_acteur`
        # retrouve l'acteur existant quand le CSV ne porte que le NOM.
        if dl_slug:
            scan_senat_amdt_csv_for_dl(dl_slug, counters, registry, senat_slugs)

    log.info("AKN Sénat : %d textes scannés (≥ %s), %d sport-relevant",
             n_total, since, n_sport)
    log.info("AKN Sénat scoring : %s", dict(stats))
    return dict(stats)


# ---------------------------------------------------------------------------
# 4c. Sénat — amendements via DB veille (faute de dump consolidé)
# ---------------------------------------------------------------------------

def scan_senat_amdt_db(
    matcher: KeywordMatcher,
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    senat_slugs: dict,
    since: str = LEGISLATURE_START,
) -> dict[str, int]:
    """Lit data/veille.sqlite3 pour les amdt Sénat sport déjà ingérés.

    LIMITATION : le pipeline daily n'a pas vocation à conserver des amdt
    Sénat anciens en DB. Coverage = ~6 mois glissants. Le sénateur très
    actif fin 2024 sur PPL Sport pro peut être sous-représenté.
    """
    import sqlite3
    db_path = ROOT / "data" / "veille.sqlite3"
    if not db_path.exists():
        return {}

    stats: dict[str, int] = defaultdict(int)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM items WHERE chamber='Senat' AND category='amendements' "
        "AND matched_keywords != '[]'"
    )
    for row in cur:
        pub = (row["published_at"] or "")[:10]
        if pub and pub < since:
            continue
        try:
            raw = json.loads(row["raw"] or "{}")
        except Exception:
            raw = {}
        # Auteur en clair texte
        auteur = (raw.get("auteur") or "").strip()
        if not auteur:
            continue
        # Heuristique parsing Prénom NOM (le NOM est typiquement en majuscules dans amdt Sénat)
        m = re.match(
            r"^(M\.|Mme)\s*"
            r"(?P<prenom>[^,]+?)\s+"
            r"(?P<nom>[A-ZÉÈÊËÀÂÄÎÏÔÖÙÛÜÇŒ\-' ]+?)$",
            auteur,
        )
        if not m:
            continue
        prenom = m.group("prenom").strip()
        nom = m.group("nom").strip()
        sid = _ensure_senat_acteur(prenom, nom, registry, senat_slugs,
                                    civ=(raw.get("civ") or ""),
                                    groupe=(raw.get("groupe") or ""))
        c = counters[sid]
        c.acteur_ref = sid; c.chambre = "Senat"
        c.amdt_depose += 1
        stats["amdt_depose"] += 1
        sort_label = (raw.get("sort") or raw.get("etat") or "").lower()
        if "adopt" in sort_label:
            c.amdt_adopte += 1
            stats["amdt_adopte"] += 1
    conn.close()
    log.info("Sénat amdt (DB veille, ≥ %s) : %s", since, dict(stats))
    return dict(stats)


# ---------------------------------------------------------------------------
# 5. Output JSON + content Hugo
# ---------------------------------------------------------------------------

def render_outputs(
    counters: dict[str, CompteurActeur],
    registry: dict[str, Acteur],
    top_n: int = 20,
) -> dict:
    """Construit le payload JSON consommé par Hugo.

    R43-A bis (2026-05-17) — Classements séparés AN et Sénat (Cyril :
    le Sénat n'a pas une approche par législature mais par
    renouvellement triennal partiel ; on garde un score identique pour
    comparaison mais on classe séparément).
    """
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
            "score": round(c.score(), 1),
            "stats": {
                "membre_commission_culture": c.membre_commission_culture,
                "membre_groupe_etude_sport": c.membre_groupe_etude_sport,
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
    top_an = [x for x in enriched if x["chambre"] == "AN"][:top_n]
    top_senat = [x for x in enriched if x["chambre"] == "Senat"][:top_n]
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "legislature": "XVIIe",
        "legislature_start": LEGISLATURE_START,
        "scoring": SCORE,
        "top": enriched[:top_n],  # mixte, conservé pour rétrocompat
        "top_an": top_an,
        "top_senat": top_senat,
        "total_parlementaires_actifs": len(enriched),
        "total_an": sum(1 for x in enriched if x["chambre"] == "AN"),
        "total_senat": sum(1 for x in enriched if x["chambre"] == "Senat"),
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

    # 1. Fetch AN (ou cache)
    t0 = time.time()
    paths = {}
    for name, url in AN_DUMPS.items():
        paths[name] = _fetch_cached(url, name)
    # Sénat
    for name, url in SENAT_DUMPS.items():
        ext = ".xml" if "AKN" in name else ".csv"
        try:
            paths[name] = _fetch_cached(url, name, ext=ext)
        except Exception as e:
            log.warning("Sénat fetch fail %s : %s", name, e)
    log.info("Fetch/cache terminé en %.1fs", time.time() - t0)

    # 2. Référentiel acteurs AN
    registry = load_acteurs_an(paths["AMO10"])
    senat_slugs = _load_senat_slugs()

    # 3. Index dosleg sport AN
    dosleg_sport = build_dosleg_sport_index(paths["DOSLEG"], matcher)

    # 4. Scanners AN
    counters: dict[str, CompteurActeur] = defaultdict(CompteurActeur)
    scan_questions_an(paths["QE"], matcher, registry, counters, "qe")
    scan_questions_an(paths["QAG"], matcher, registry, counters, "qag")
    scan_questions_an(paths["QOSD"], matcher, registry, counters, "qosd")
    scan_documents_an(paths["DOSLEG"], matcher, registry, counters, dosleg_sport)
    scan_amendements_an(paths["AMDT"], matcher, registry, counters, dosleg_sport)

    # 5. Sénat (CSV + Akoma Ntoso + DB veille pour amdt)
    if "Q1AN" in paths:
        scan_senat_questions_csv(paths["Q1AN"], matcher, counters, registry, senat_slugs)
    if "QG_SENAT" in paths:
        scan_senat_questions_csv(paths["QG_SENAT"], matcher, counters, registry, senat_slugs)
    senat_dl_scope_elargi: set[str] = set()
    if "AKN_DEPOTS" in paths:
        scan_senat_akn(
            paths["AKN_DEPOTS"], matcher, counters, registry, senat_slugs,
            dl_slugs_scope_elargi_out=senat_dl_scope_elargi,
        )
    scan_senat_amdt_db(matcher, counters, registry, senat_slugs)

    # 5b. Rapports manuels (Sénat — rapports d'info absents AKN)
    scan_manual_reports(counters, registry, senat_slugs)

    # 5c. Appartenances (commission Culture + groupe d'étude sport)
    apply_membership_credits(counters, registry, senat_slugs)

    # 5d. Backfill manuel questions Sénat 2024-07 → 2025-04
    # (période hors couverture open data `questions-depuis-un-an.csv`)
    apply_senat_backfill_2024(counters, registry, senat_slugs)

    # 5d-bis. R43-J : scan amdt PLF sport (filtre strict).
    for plf_slug in SENAT_PLF_SLUGS:
        scan_senat_plf_amdt(plf_slug, counters, registry, senat_slugs)

    # 5d-ter. R43-K (2026-05-17) — Cyril : « tous les amendements sports,
    # y compris sur d'autres textes ». Côté Sénat, on scanne tous les
    # PJL (Projets de Loi) déposés depuis 2024-07 avec filtre matcher
    # strict sport. Volume estimé : ~173 PJL × 2 CSV moyens = ~350
    # fetches. Cache local : runs suivants quasi instantanés.
    # Les PPL et PPR sport sont déjà couverts via le scope élargi
    # `scan_senat_amdt_csv_for_dl`.
    scan_senat_all_pjl_amdt(
        paths.get("AKN_DEPOTS"), counters, registry, senat_slugs,
        exclude_slugs=senat_dl_scope_elargi,
    )

    # 5e. R43-F : enrichir les groupes politiques Sénat manquants
    # (Lafon / Savin / Malhuret n'ont pas posé de Q sport récente,
    # leur groupe est seulement dans la fiche senateur HTML).
    _enrich_senat_groupes(counters, registry, senat_slugs, top_n=50)

    # 6. Output
    payload = render_outputs(counters, registry, top_n=args.top)
    log.info(
        "Total actifs : %d (AN: %d, Sénat: %d)",
        payload["total_parlementaires_actifs"],
        payload["total_an"], payload["total_senat"],
    )
    write_data_json(payload)
    write_hugo_content(payload)

    # Affichage console
    log.info("== Top AN ==")
    for i, p in enumerate(payload["top_an"], 1):
        log.info("  #%2d  %-30s %5d pts  [AN] %s",
                 i, f"{p['prenom']} {p['nom']}"[:30], p["score"], p["groupe"] or "—")
    log.info("== Top Sénat ==")
    for i, p in enumerate(payload["top_senat"], 1):
        log.info("  #%2d  %-30s %5d pts  [Sénat] %s",
                 i, f"{p['prenom']} {p['nom']}"[:30], p["score"], p["groupe"] or "—")


if __name__ == "__main__":
    main()
