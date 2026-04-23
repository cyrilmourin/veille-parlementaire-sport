"""Tests R28 — scraper `an_rapports` + filtre publications Parlement.

Couvre :
- Parser HTML `assemblee_rapports._extract_reports` (filtrage RAPP vs
  PRJL/PION/AVIS, extraction titre/date/dossier/PDF, parsing date FR).
- `assemblee_rapports.fetch_source` via monkeypatch de `fetch_bytes`.
- `site_export._filter_parlement_publications` : retrait de senat_rss
  du bucket parlement en publications, conservation des rapports AN +
  Sénat, idempotence sur les autres catégories / familles.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from src.sources import assemblee_rapports
from src.sources.assemblee_rapports import (
    _extract_reports,
    _parse_date_fr,
    _parse_report_li,
    fetch_source,
)
from src.site_export import (
    _PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES,
    _filter_parlement_publications,
)
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# _parse_date_fr
# ---------------------------------------------------------------------------

def test_parse_date_fr_nominal():
    dt = _parse_date_fr("Mis en ligne mercredi 28 janvier 2026 à 15h20")
    assert dt == datetime(2026, 1, 28, 15, 20)


def test_parse_date_fr_sans_heure():
    dt = _parse_date_fr("Mis en ligne mardi 2 mars 2026")
    # Pas d'heure → 00h00
    assert dt == datetime(2026, 3, 2, 0, 0)


def test_parse_date_fr_mois_avec_accent():
    # "décembre" et "février" avec accents
    assert _parse_date_fr("Mis en ligne lundi 5 décembre 2025 à 10h00") == datetime(
        2025, 12, 5, 10, 0
    )
    assert _parse_date_fr("Mis en ligne lundi 3 février 2026 à 9h5") == datetime(
        2026, 2, 3, 9, 5
    )


def test_parse_date_fr_mois_sans_accent():
    # "fevrier" / "decembre" / "aout" sans accent
    assert _parse_date_fr("Mis en ligne lundi 5 fevrier 2026 à 10h00") == datetime(
        2026, 2, 5, 10, 0
    )
    assert _parse_date_fr("Mis en ligne mardi 15 aout 2025 à 9h0") == datetime(
        2025, 8, 15, 9, 0
    )


def test_parse_date_fr_null_ou_invalide():
    assert _parse_date_fr("") is None
    assert _parse_date_fr("texte qui ne matche pas") is None
    # Mois inconnu
    assert _parse_date_fr("Mis en ligne lundi 5 fantom 2026 à 10h00") is None


# ---------------------------------------------------------------------------
# _parse_report_li — parser d'une entrée <li>
# ---------------------------------------------------------------------------

def _li_fragment(html: str):
    """Retourne le 1er <li> du fragment HTML."""
    return BeautifulSoup(html, "html.parser").find("li")


def test_parse_report_li_nominal_rapp():
    html = """
    <li data-id="OMC_RAPPANR5L17B2396">
      <span class="heure">Mis en ligne mercredi 28 janvier 2026 à 15h20</span>
      <h3>Rapport sur la PPL de M. Dupont</h3>
      <p>Rapport de la commission des affaires culturelles sur la PPL n°2396.</p>
      <a href="/dyn/17/dossiers/titre_dossier">Dossier législatif</a>
      <a href="/dyn/17/pdf/rapports/r2396-a0.pdf">Document</a>
    </li>
    """
    r = _parse_report_li(_li_fragment(html))
    assert r is not None
    assert r["data_id"] == "OMC_RAPPANR5L17B2396"
    assert r["num"] == "2396"
    assert r["title"].startswith("Rapport sur la PPL")
    assert r["published_at"] == datetime(2026, 1, 28, 15, 20)
    # URL = dossier législatif (prioritaire sur PDF)
    assert r["url"] == "/dyn/17/dossiers/titre_dossier"
    assert r["url_dossier"] == "/dyn/17/dossiers/titre_dossier"
    assert r["url_pdf"] == "/dyn/17/pdf/rapports/r2396-a0.pdf"
    # UID stable (16 chars hex)
    assert len(r["uid"]) == 16


def test_parse_report_li_rapp_compa():
    """Suffix -COMPA : entrée conservée, uid distinct de la version principale."""
    html = """
    <li data-id="OMC_RAPPANR5L17B2396-COMPA">
      <span class="heure">Mis en ligne jeudi 29 janvier 2026 à 11h00</span>
      <h3>Rapport — texte comparatif</h3>
      <p>Version comparative du rapport n°2396.</p>
      <a href="/dyn/17/pdf/rapports/r2396-a0-COMPA.pdf">Document</a>
    </li>
    """
    r = _parse_report_li(_li_fragment(html))
    assert r is not None
    assert r["data_id"] == "OMC_RAPPANR5L17B2396-COMPA"
    # Pas de dossier législatif → fallback sur PDF
    assert r["url"] == "/dyn/17/pdf/rapports/r2396-a0-COMPA.pdf"
    assert r["url_dossier"] == ""


def test_parse_report_li_skip_non_rapp():
    """Les entrées OMC_PRJL (projet de loi) / OMC_PION (texte adopté) sont ignorées."""
    for did in ("OMC_PRJLANR5L17B2100", "OMC_PIONANR5L17B2050", "OMC_AVISANR5L17B2200"):
        html = f'<li data-id="{did}"><h3>T</h3><a href="/x">Dossier</a></li>'
        assert _parse_report_li(_li_fragment(html)) is None


def test_parse_report_li_skip_no_data_id():
    html = '<li><h3>T</h3><a href="/x">Dossier</a></li>'
    assert _parse_report_li(_li_fragment(html)) is None


def test_parse_report_li_skip_no_title_or_url():
    """Pas de <h3> OU pas d'<a> → None."""
    html_no_title = '<li data-id="OMC_RAPPANR5L17B2396"><a href="/x">Dossier</a></li>'
    assert _parse_report_li(_li_fragment(html_no_title)) is None

    html_no_url = '<li data-id="OMC_RAPPANR5L17B2396"><h3>T</h3></li>'
    assert _parse_report_li(_li_fragment(html_no_url)) is None


def test_parse_report_li_dossier_label_fallback():
    """Lien dossier détecté par label même si href ne contient pas /dossiers/."""
    html = """
    <li data-id="OMC_RAPPANR5L17B2400">
      <h3>T</h3>
      <a href="/autre_chemin/xxx">Dossier législatif</a>
      <a href="/x.pdf">Document</a>
    </li>
    """
    r = _parse_report_li(_li_fragment(html))
    assert r is not None
    assert r["url_dossier"] == "/autre_chemin/xxx"


# ---------------------------------------------------------------------------
# _extract_reports — liste complète
# ---------------------------------------------------------------------------

def test_extract_reports_filtre_rapp_seulement():
    html = """
    <html><body>
      <ul>
        <li data-id="OMC_RAPPANR5L17B2396">
          <span class="heure">Mis en ligne mercredi 28 janvier 2026 à 15h20</span>
          <h3>Rapport 2396</h3>
          <a href="/dyn/17/dossiers/a">Dossier législatif</a>
        </li>
        <li data-id="OMC_PRJLANR5L17B2100">
          <h3>Projet de loi</h3>
          <a href="/dyn/17/pdf/projet.pdf">Document</a>
        </li>
        <li data-id="OMC_RAPPANR5L17B2500">
          <span class="heure">Mis en ligne lundi 10 février 2026 à 12h00</span>
          <h3>Rapport 2500</h3>
          <a href="/dyn/17/dossiers/b">Dossier législatif</a>
        </li>
      </ul>
    </body></html>
    """
    out = _extract_reports(html)
    assert len(out) == 2
    nums = [r["num"] for r in out]
    assert nums == ["2396", "2500"]


def test_extract_reports_html_vide():
    assert _extract_reports("") == []
    assert _extract_reports("<html><body></body></html>") == []


def test_extract_reports_ignore_li_sans_data_id():
    html = "<ul><li><h3>nope</h3></li></ul>"
    assert _extract_reports(html) == []


# ---------------------------------------------------------------------------
# fetch_source — intégration avec Item
# ---------------------------------------------------------------------------

def test_fetch_source_build_items():
    html = (
        "<html><body><ul>"
        '<li data-id="OMC_RAPPANR5L17B2396">'
        '<span class="heure">Mis en ligne mercredi 28 janvier 2026 à 15h20</span>'
        "<h3>Rapport 2396 sur la PPL sport</h3>"
        "<p>Résumé du rapport</p>"
        '<a href="/dyn/17/dossiers/rapport-sport">Dossier législatif</a>'
        '<a href="/dyn/17/pdf/rapports/r2396-a0.pdf">Document</a>'
        "</li>"
        "</ul></body></html>"
    ).encode("utf-8")

    with patch.object(assemblee_rapports, "fetch_bytes", return_value=html):
        items = fetch_source({
            "id": "an_rapports",
            "url": "https://www2.assemblee-nationale.fr/documents/liste?type=rapports&legis=17",
            "category": "communiques",
            "format": "an_rapports_html",
        })

    assert len(items) == 1
    it = items[0]
    assert it.source_id == "an_rapports"
    assert it.chamber == "AN"
    assert it.category == "communiques"
    assert it.title.startswith("Rapport 2396")
    assert it.url == "/dyn/17/dossiers/rapport-sport"
    assert it.published_at == datetime(2026, 1, 28, 15, 20)
    assert it.summary == "Résumé du rapport"
    assert it.raw["path"] == "assemblee:rapport"
    assert it.raw["num"] == "2396"
    assert it.raw["url_dossier"] == "/dyn/17/dossiers/rapport-sport"
    assert it.raw["url_pdf"] == "/dyn/17/pdf/rapports/r2396-a0.pdf"


def test_fetch_source_fetch_ko_retourne_liste_vide():
    def _boom(url):
        raise RuntimeError("timeout")

    with patch.object(assemblee_rapports, "fetch_bytes", side_effect=_boom):
        items = fetch_source({
            "id": "an_rapports",
            "url": "https://www2.assemblee-nationale.fr/documents/liste",
            "category": "communiques",
        })
    assert items == []


def test_fetch_source_aucun_rapport_extrait():
    """HTML valide mais sans aucun <li data-id="OMC_RAPP..."> → liste vide."""
    html = b"<html><body><p>Page vide</p></body></html>"
    with patch.object(assemblee_rapports, "fetch_bytes", return_value=html):
        items = fetch_source({
            "id": "an_rapports",
            "url": "https://www2.assemblee-nationale.fr/documents/liste",
            "category": "communiques",
        })
    assert items == []


# ---------------------------------------------------------------------------
# _filter_parlement_publications
# ---------------------------------------------------------------------------

def test_allowed_sources_contient_an_et_senat_rapports():
    assert "senat_rapports" in _PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES
    assert "an_rapports" in _PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES
    # Sanity : senat_rss PAS dans la whitelist
    assert "senat_rss" not in _PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES


def test_filter_parlement_publications_garde_rapports():
    rows = [
        {"source_id": "senat_rapports", "category": "communiques", "title": "Rapport S"},
        {"source_id": "an_rapports", "category": "communiques", "title": "Rapport AN"},
    ]
    kept = _filter_parlement_publications(rows)
    assert len(kept) == 2


def test_filter_parlement_publications_retire_senat_rss():
    rows = [
        {"source_id": "senat_rss", "category": "communiques", "title": "Actu RSS"},
        {"source_id": "senat_rapports", "category": "communiques", "title": "Rapport S"},
    ]
    kept = _filter_parlement_publications(rows)
    ids = [r["source_id"] for r in kept]
    assert "senat_rss" not in ids
    assert "senat_rapports" in ids


def test_filter_parlement_publications_ne_touche_pas_autres_categories():
    """Les items Parlement non-communiques passent toujours."""
    rows = [
        {"source_id": "senat_rss", "category": "dossiers_legislatifs", "title": "X"},
        {"source_id": "an_agenda", "category": "agenda", "title": "Y"},
        {"source_id": "an_questions_ecrites", "category": "questions", "title": "Z"},
    ]
    kept = _filter_parlement_publications(rows)
    assert len(kept) == 3


def test_filter_parlement_publications_ne_touche_pas_autres_familles():
    """Les publications hors parlement (gouvernement, autorites, operateurs,
    mouvement_sportif) ne sont pas filtrées par ce passage.
    """
    rows = [
        {"source_id": "elysee_feed", "category": "communiques", "title": "Élysée"},
        {"source_id": "min_sports_presse", "category": "communiques", "title": "MinS"},
        {"source_id": "arcom", "category": "communiques", "title": "ARCOM"},
        {"source_id": "ans", "category": "communiques", "title": "ANS"},
        {"source_id": "cnosf", "category": "communiques", "title": "CNOSF"},
    ]
    kept = _filter_parlement_publications(rows)
    assert len(kept) == 5


def test_filter_parlement_publications_idempotent():
    """Passe 2x → même résultat."""
    rows = [
        {"source_id": "senat_rss", "category": "communiques", "title": "A"},
        {"source_id": "senat_rapports", "category": "communiques", "title": "B"},
        {"source_id": "an_rapports", "category": "communiques", "title": "C"},
    ]
    once = _filter_parlement_publications(rows)
    twice = _filter_parlement_publications(once)
    assert once == twice
    assert len(once) == 2


def test_filter_parlement_publications_liste_vide():
    assert _filter_parlement_publications([]) == []


def test_filter_parlement_publications_chamber_fallback():
    """Un item Parlement sans source_id mais avec chamber=Senat doit passer
    par le fallback _source_family (chamber) → family=parlement, donc
    filtré (car pas dans la whitelist senat_rapports/an_rapports).
    """
    rows = [
        {"source_id": "", "chamber": "Senat", "category": "communiques", "title": "X"},
    ]
    kept = _filter_parlement_publications(rows)
    # source_id vide → pas dans whitelist → filtré
    assert kept == []
