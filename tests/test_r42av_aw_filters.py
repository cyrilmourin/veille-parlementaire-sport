"""Tests R42-AV + R42-AW — filtres faux positifs publications parlement.

R42-AV : exclusion des « Texte comparatif » dans le scraper an_rapports*
         (data-id `-COMPA` ou marqueur titre).
R42-AW : exclusion des publications parlement matchant SEULEMENT la famille
         nomination_event (« élu président » dans un rapport non-sport).
"""
from __future__ import annotations

from bs4 import BeautifulSoup
from unittest.mock import patch

from src.sources.assemblee_rapports import (
    _parse_report_li,
    _extract_reports,
    fetch_source,
)
from src.site_export import _filter_parlement_publications_nominations_only


def _li(html: str):
    return BeautifulSoup(html, "html.parser").find("li")


# ===========================================================================
# R42-AV — Filtre « Texte comparatif »
# ===========================================================================

def test_r42av_exclut_data_id_suffix_compa():
    """Un data-id se terminant par `-COMPA` est rejeté."""
    html = """
    <li data-id="OMC_RAPPANR5L17B2233-COMPA">
      <h3>Projet de loi JOP 2030 - N° 2233 Texte comparatif</h3>
      <a href="/dyn/17/dossiers/x">Dossier</a>
    </li>
    """
    assert _parse_report_li(_li(html)) is None


def test_r42av_exclut_titre_avec_texte_comparatif():
    """Défense en profondeur : même sans `-COMPA` dans data-id, un titre
    contenant « Texte comparatif » est rejeté."""
    html = """
    <li data-id="OMC_RAPPANR5L17B9999">
      <h3>Projet de loi machin - N° 9999 Texte comparatif</h3>
      <a href="/dyn/17/dossiers/x">Dossier</a>
    </li>
    """
    assert _parse_report_li(_li(html)) is None


def test_r42av_exclut_titre_case_insensitive():
    """Match case-insensitive (futur changement de casse côté AN)."""
    html = """
    <li data-id="OMC_RAPPANR5L17B8888">
      <h3>Rapport — N° 8888 TEXTE COMPARATIF</h3>
      <a href="/dyn/17/dossiers/x">Dossier</a>
    </li>
    """
    assert _parse_report_li(_li(html)) is None


def test_r42av_garde_rapport_principal():
    """Non-régression : un rapport principal (sans -COMPA, sans le marqueur
    dans le titre) reste ingéré normalement."""
    html = """
    <li data-id="OMC_RAPPANR5L17B0699">
      <span class="heure">Mis en ligne lundi 12 septembre 2024 à 17h00</span>
      <h3>Pour plus de sport et moins de sucre - N° 699</h3>
      <a href="/dyn/17/dossiers/sport_sucre">Dossier législatif</a>
    </li>
    """
    r = _parse_report_li(_li(html))
    assert r is not None
    # `B(\d+)` capture les zéros initiaux du data-id : B0699 → "0699"
    assert r["num"] == "0699"


def test_r42av_extract_reports_filtre_compa_dans_liste_mixte():
    """Sur une page mélangeant principal + COMPA, seul le principal sort."""
    html = """
    <html><body><ul>
      <li data-id="OMC_RAPPANR5L17B0699">
        <span class="heure">Mis en ligne lundi 12 septembre 2024 à 17h00</span>
        <h3>Pour plus de sport et moins de sucre - N° 699</h3>
        <a href="/dyn/17/dossiers/sport_sucre">Dossier</a>
      </li>
      <li data-id="OMC_RAPPANR5L17B0699-COMPA">
        <h3>Pour plus de sport et moins de sucre - N° 699 Texte comparatif</h3>
        <a href="/dyn/17/dossiers/sport_sucre">Dossier</a>
      </li>
    </ul></body></html>
    """
    out = _extract_reports(html)
    assert len(out) == 1
    assert "Texte comparatif" not in out[0]["title"]


def test_r42av_extract_reports_inclut_rinf_et_avis_principaux():
    """R42-AJ (RINF/AVIS) toujours OK + R42-AV ne filtre que les COMPA."""
    html = """
    <html><body><ul>
      <li data-id="OMC_RINFANR5L17B2465">
        <span class="heure">Mis en ligne mardi 11 février 2026 à 19h35</span>
        <h3>Évaluation loi démocratiser le sport - N° 2465</h3>
        <a href="/dyn/17/dossiers/eval">Dossier</a>
      </li>
      <li data-id="OMC_AVISANR5L17B2043">
        <span class="heure">Mis en ligne lundi 10 novembre 2025 à 19h40</span>
        <h3>PLF 2026 - N° 2043 Tome IX</h3>
        <a href="/dyn/17/dossiers/plf2026">Dossier</a>
      </li>
    </ul></body></html>
    """
    out = _extract_reports(html)
    assert len(out) == 2


# ===========================================================================
# R42-AW — Filtre nomination_event seul sur publications parlement
# ===========================================================================

def _row(source_id: str, category: str = "communiques",
         families: list[str] | None = None, chamber: str = "Senat") -> dict:
    return {
        "source_id": source_id,
        "category": category,
        "chamber": chamber,
        "keyword_families": families or [],
    }


def test_r42aw_drop_rapport_senat_avec_nomination_seule():
    """Rapport Sénat communiques avec families=['nomination_event'] → drop."""
    rows = [_row("senat_rapports", families=["nomination_event"])]
    out = _filter_parlement_publications_nominations_only(rows)
    assert len(out) == 0


def test_r42aw_drop_rapport_an_avec_nomination_seule():
    """Idem côté AN (an_rapports, an_rapports_information, an_avis)."""
    for sid in ("an_rapports", "an_rapports_information", "an_avis"):
        rows = [_row(sid, families=["nomination_event"], chamber="AN")]
        out = _filter_parlement_publications_nominations_only(rows)
        assert len(out) == 0, f"{sid} pas filtré alors qu'il devrait l'être"


def test_r42aw_garde_rapport_avec_famille_sport_en_plus():
    """Si un rapport matche aussi une famille sport (acteur, dispositif,
    federation, theme, evenement) en plus de nomination_event → on garde."""
    for extra in ("acteur", "dispositif", "federation", "theme", "evenement"):
        rows = [_row("senat_rapports",
                     families=["nomination_event", extra])]
        out = _filter_parlement_publications_nominations_only(rows)
        assert len(out) == 1, (
            f"Rapport avec famille {extra} drop à tort"
        )


def test_r42aw_garde_rapport_sans_nomination_event():
    """Rapport avec UNE famille sport pure (acteur) → on garde."""
    rows = [_row("senat_rapports", families=["acteur"])]
    out = _filter_parlement_publications_nominations_only(rows)
    assert len(out) == 1


def test_r42aw_ignore_categories_autres_que_communiques():
    """Un item de catégorie dossiers_legislatifs avec families=
    ['nomination_event'] reste — la règle ne s'applique qu'à publications."""
    rows = [_row("an_dossiers_legislatifs", category="dossiers_legislatifs",
                 families=["nomination_event"], chamber="AN")]
    out = _filter_parlement_publications_nominations_only(rows)
    assert len(out) == 1


def test_r42aw_ignore_sources_non_parlement():
    """Un item communiques d'une autre family_source (ex. presse Olbia avec
    only nomination_event) n'est pas filtré ici — c'est R41-A qui le
    re-route vers nominations."""
    # Source presse business (family_source != parlement).
    # N.B. chamber="" car _source_family priorise chamber="Senat"/"AN" sur
    # source_id et retournerait "parlement" — ici on simule une source
    # presse pure sans chamber parlementaire.
    rows = [_row("olbia", families=["nomination_event"], chamber="")]
    out = _filter_parlement_publications_nominations_only(rows)
    assert len(out) == 1


def test_r42aw_keyword_families_serialise_string():
    """Cas robustesse : si keyword_families est stocké comme string JSON
    (lecture brute DB), on parse correctement."""
    rows = [{
        "source_id": "senat_rapports",
        "category": "communiques",
        "chamber": "Senat",
        "keyword_families": '["nomination_event"]',  # string, pas list
    }]
    out = _filter_parlement_publications_nominations_only(rows)
    assert len(out) == 0
