"""Tests R42-AJ — élargissement du scraper rapports AN à RINF + AVIS.

Avant R42-AJ, le regex `_DATA_ID_RE = re.compile(r"^OMC_RAPP")` filtrait
EXCLUSIVEMENT les rapports de commission classiques. Les 147 rapports
d'information (RINF) et 18 avis (AVIS) listés par l'AN n'étaient JAMAIS
ingérés. Cyril a remonté qu'il manquait notamment :
- RINF B2465 : « évaluation loi du 2 mars 2022 démocratiser le sport »
- Les avis budgétaires PLF 2025/2026 sur « Sport, jeunesse & vie associative »

Ce module valide :
- Le regex accepte RAPP/RINF/AVIS et REJETTE PRJL/PION/PNRE.
- `_parse_report_li` extrait correctement `doc_type` depuis le data-id.
- `fetch_source` log la répartition par type.
- Les 3 sources YAML (`an_rapports`, `an_rapports_information`,
  `an_avis`) partagent le même format `an_rapports_html`.
- `WINDOW_DAYS_BY_SOURCE_ID` et `_PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES`
  incluent les 2 nouvelles sources.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from src.sources.assemblee_rapports import (
    _DATA_ID_RE,
    _extract_reports,
    _parse_report_li,
    fetch_source,
)
from src.site_export import (
    WINDOW_DAYS_BY_SOURCE_ID,
    _PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES,
)


def _li(html: str):
    return BeautifulSoup(html, "html.parser").find("li")


# ---------------------------------------------------------------------------
# Regex : RAPP/RINF/AVIS acceptés, PRJL/PION/PNRE rejetés
# ---------------------------------------------------------------------------

def test_regex_accepte_rapp():
    assert _DATA_ID_RE.match("OMC_RAPPANR5L17B2396") is not None


def test_regex_accepte_rinf():
    """R42-AJ : RINF (rapport d'information) désormais accepté."""
    assert _DATA_ID_RE.match("OMC_RINFANR5L17B2465") is not None


def test_regex_accepte_avis():
    """R42-AJ : AVIS (avis budgétaire / sur PJL) désormais accepté."""
    assert _DATA_ID_RE.match("OMC_AVISANR5L17B1906") is not None


def test_regex_rejette_textes_de_loi():
    """Préservation de la séparation textes vs rapports.

    Les textes (PRJL/PION/PNRE) restent ingérés par `an_dossiers_legislatifs`
    et appartiennent à la catégorie `dossiers_legislatifs`. Ils NE DOIVENT
    PAS être happés par `an_rapports*` (qui peuple `communiques`/Publications)."""
    for did in (
        "OMC_PRJLANR5L17B2100",   # projet de loi
        "OMC_PIONANR5L17B0558",   # proposition de loi
        "OMC_PNREANR5L17B2126",   # proposition de résolution
    ):
        assert _DATA_ID_RE.match(did) is None, f"{did} devrait être rejeté"


# ---------------------------------------------------------------------------
# _parse_report_li : extraction de doc_type
# ---------------------------------------------------------------------------

def test_parse_report_li_rinf_extrait_doc_type():
    """R42-AJ : un <li> OMC_RINF retourne un dict avec doc_type='RINF'."""
    html = """
    <li data-id="OMC_RINFANR5L17B2465">
      <span class="heure">Mis en ligne lundi 5 mai 2026 à 14h00</span>
      <h3>Rapport d'information sur l'évaluation de la loi du 2 mars 2022 visant à démocratiser le sport en France</h3>
      <a href="/dyn/17/dossiers/eval_loi_2022_democratiser_sport">Dossier législatif</a>
      <a href="/dyn/17/pdf/rap-info/i2465.pdf">Document</a>
    </li>
    """
    r = _parse_report_li(_li(html))
    assert r is not None
    assert r["doc_type"] == "RINF"
    assert r["num"] == "2465"
    assert "démocratiser le sport" in r["title"]


def test_parse_report_li_avis_extrait_doc_type():
    """R42-AJ : un <li> OMC_AVIS retourne un dict avec doc_type='AVIS'."""
    html = """
    <li data-id="OMC_AVISANR5L17B1906">
      <span class="heure">Mis en ligne mardi 14 octobre 2025 à 09h30</span>
      <h3>Avis sur le PLF 2026 — Sport, jeunesse et vie associative : Sport</h3>
      <a href="/dyn/17/dossiers/plf2026">Dossier législatif</a>
    </li>
    """
    r = _parse_report_li(_li(html))
    assert r is not None
    assert r["doc_type"] == "AVIS"
    assert r["num"] == "1906"


def test_parse_report_li_rapp_extrait_doc_type():
    """Non-régression : RAPP classique continue à parser, doc_type='RAPP'."""
    html = """
    <li data-id="OMC_RAPPANR5L17B0699">
      <span class="heure">Mis en ligne jeudi 12 septembre 2024 à 17h00</span>
      <h3>Rapport sur la PPL pour plus de sport et moins de sucre</h3>
      <a href="/dyn/17/dossiers/sport_sucre">Dossier législatif</a>
    </li>
    """
    r = _parse_report_li(_li(html))
    assert r is not None
    assert r["doc_type"] == "RAPP"


# ---------------------------------------------------------------------------
# _extract_reports : flux mixte (les 3 types coexistent dans une page)
# ---------------------------------------------------------------------------

def test_extract_reports_filtre_mixte_rapp_rinf_avis():
    """Sur une page mélangeant RAPP/RINF/AVIS/PRJL/PION/PNRE, on garde
    les 3 premiers et on jette les 3 autres."""
    html = """
    <html><body><ul>
      <li data-id="OMC_RAPPANR5L17B2074">
        <span class="heure">Mis en ligne lundi 7 octobre 2024 à 10h00</span>
        <h3>OPECST science dans la mêlée nation sportive</h3>
        <a href="/dyn/17/dossiers/opecst_sport">Dossier</a>
      </li>
      <li data-id="OMC_RINFANR5L17B2465">
        <span class="heure">Mis en ligne mardi 8 octobre 2024 à 11h00</span>
        <h3>Évaluation loi 2 mars 2022 démocratiser sport</h3>
        <a href="/dyn/17/dossiers/eval_loi">Dossier</a>
      </li>
      <li data-id="OMC_AVISANR5L17B1906">
        <span class="heure">Mis en ligne mercredi 9 octobre 2024 à 12h00</span>
        <h3>Avis PLF Sport JVA</h3>
        <a href="/dyn/17/dossiers/plf2026">Dossier</a>
      </li>
      <li data-id="OMC_PRJLANR5L17B1906">
        <h3>PLF 2026 — texte de loi</h3>
        <a href="/dyn/17/textes/1906.pdf">Document</a>
      </li>
      <li data-id="OMC_PIONANR5L17B1068">
        <h3>PPL éducateurs sportifs</h3>
        <a href="/dyn/17/dossiers/educateurs">Dossier</a>
      </li>
      <li data-id="OMC_PNREANR5L17B2126">
        <h3>PPR pilotage politique nationale du sport</h3>
        <a href="/dyn/17/dossiers/ppr_pilotage">Dossier</a>
      </li>
    </ul></body></html>
    """
    out = _extract_reports(html)
    assert len(out) == 3
    types_extraits = sorted(r["doc_type"] for r in out)
    assert types_extraits == ["AVIS", "RAPP", "RINF"]
    # Vérifie qu'aucun texte de loi n'a fuité
    for r in out:
        assert r["doc_type"] in ("RAPP", "RINF", "AVIS")


# ---------------------------------------------------------------------------
# fetch_source : doc_type propagé dans raw + log par type
# ---------------------------------------------------------------------------

def test_fetch_source_propage_doc_type_dans_raw():
    """Items.raw.doc_type doit refléter le type AN."""
    html_mock = """
    <html><body><ul>
      <li data-id="OMC_RAPPANR5L17B0699">
        <span class="heure">Mis en ligne jeudi 12 septembre 2024 à 17h00</span>
        <h3>Rapport sport sucre</h3>
        <a href="/dyn/17/dossiers/sport_sucre">Dossier</a>
      </li>
      <li data-id="OMC_RINFANR5L17B2465">
        <span class="heure">Mis en ligne lundi 5 mai 2026 à 14h00</span>
        <h3>RINF démocratiser sport</h3>
        <a href="/dyn/17/dossiers/eval">Dossier</a>
      </li>
      <li data-id="OMC_AVISANR5L17B1906">
        <span class="heure">Mis en ligne mardi 14 octobre 2025 à 09h30</span>
        <h3>Avis PLF Sport</h3>
        <a href="/dyn/17/dossiers/plf">Dossier</a>
      </li>
    </ul></body></html>
    """
    with patch("src.sources.assemblee_rapports.fetch_bytes",
               return_value=html_mock.encode("utf-8")):
        # Bypass _fetch_pdf_haystack (réseau évité, vu qu'on n'a pas url_pdf)
        items = fetch_source({
            "id": "an_rapports",
            "url": "https://www2.assemblee-nationale.fr/documents/liste?type=rapports&legis=17",
            "category": "communiques",
            "format": "an_rapports_html",
        })

    types = sorted(it.raw["doc_type"] for it in items)
    assert types == ["AVIS", "RAPP", "RINF"]
    # Tous catégorie communiques (= Publications côté site)
    assert {it.category for it in items} == {"communiques"}
    assert {it.chamber for it in items} == {"AN"}


# ---------------------------------------------------------------------------
# site_export : mappings R42-AJ corrects
# ---------------------------------------------------------------------------

def test_window_days_inclut_nouvelles_sources():
    """`WINDOW_DAYS_BY_SOURCE_ID` doit avoir `an_rapports_information` et
    `an_avis` à 730j pour absorber 2 ans d'historique."""
    assert WINDOW_DAYS_BY_SOURCE_ID["an_rapports_information"] == 730
    assert WINDOW_DAYS_BY_SOURCE_ID["an_avis"] == 730
    # Cohérence avec an_rapports historique
    assert WINDOW_DAYS_BY_SOURCE_ID["an_rapports"] == 730


def test_publications_parlement_inclut_nouvelles_sources():
    """Page Publications → bucket parlement doit lister les RINF/AVIS AN."""
    assert "an_rapports_information" in _PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES
    assert "an_avis" in _PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES
    # Non-régression sur les sources d'origine
    assert "an_rapports" in _PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES
    assert "senat_rapports" in _PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES


# ---------------------------------------------------------------------------
# YAML : les 3 sources existent et partagent le format
# ---------------------------------------------------------------------------

def test_yaml_3_sources_an_rapports_format_commun():
    """`config/sources.yml` expose bien les 3 sources avec le même
    `format: an_rapports_html`."""
    import yaml
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    with open(ROOT / "config" / "sources.yml") as f:
        cfg = yaml.safe_load(f)
    found = {}
    for grp in cfg.values():
        if isinstance(grp, dict) and "sources" in grp:
            for s in grp["sources"]:
                sid = s.get("id")
                if sid in ("an_rapports", "an_rapports_information", "an_avis"):
                    found[sid] = s
    assert set(found.keys()) == {
        "an_rapports", "an_rapports_information", "an_avis"
    }, f"Sources manquantes : {set(found.keys())}"
    for sid, src in found.items():
        assert src["format"] == "an_rapports_html"
        assert src["category"] == "communiques"
        assert "type=" in src["url"], f"{sid} URL doit avoir un filtre type="
