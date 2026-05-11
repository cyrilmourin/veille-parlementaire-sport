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
from src.site_export import (
    _filter_parlement_publications_nominations_only,
    _filter_parlement_cr_nominations_only,
    _filter_publications_texte_comparatif,
)


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


# ===========================================================================
# R42-BF — Miroir export du filtre « Texte comparatif »
# ===========================================================================

def _row_pub(title: str, source_id: str = "an_rapports", chamber: str = "AN") -> dict:
    return {
        "source_id": source_id,
        "category": "communiques",
        "chamber": chamber,
        "title": title,
    }


def test_r42bf_drop_titre_texte_comparatif_minuscule():
    rows = [_row_pub("Projet de loi machin - N° 9999 Texte comparatif")]
    out = _filter_publications_texte_comparatif(rows)
    assert len(out) == 0


def test_r42bf_drop_titre_texte_comparatif_case_insensitive():
    """TEXTE COMPARATIF, Texte Comparatif, etc. — match insensible casse."""
    for title in ("Rapport - TEXTE COMPARATIF",
                  "Rapport - Texte Comparatif",
                  "Rapport - texte COMPARATIF"):
        rows = [_row_pub(title)]
        out = _filter_publications_texte_comparatif(rows)
        assert len(out) == 0, f"Pas filtré : {title!r}"


def test_r42bf_drop_titre_whitespace_insecable():
    """Espace insécable U+00A0 entre « Texte » et « comparatif » — \\s+ matche."""
    rows = [_row_pub("Rapport - Texte comparatif")]
    out = _filter_publications_texte_comparatif(rows)
    assert len(out) == 0


def test_r42bf_garde_titre_sans_marker():
    """Un titre normal de rapport reste."""
    rows = [_row_pub("Pour plus de sport et moins de sucre - N° 699")]
    out = _filter_publications_texte_comparatif(rows)
    assert len(out) == 1


def test_r42bf_ignore_autres_categories():
    """Un dossier législatif qui CONTIENT « Texte comparatif » dans son
    titre n'est PAS filtré (scope strict : category=communiques)."""
    rows = [{
        "source_id": "an_dossiers_legislatifs",
        "category": "dossiers_legislatifs",
        "chamber": "AN",
        "title": "Étude d'un Texte comparatif sur les pratiques sportives",
    }]
    out = _filter_publications_texte_comparatif(rows)
    assert len(out) == 1


def test_r42bf_ignore_sources_non_parlement():
    """Un communiqué CNOSF ou MinSports avec « texte comparatif » dans le
    titre n'est PAS filtré non plus."""
    for sid, ch in (("cnosf", "CNOSF"), ("min_sports_actualites", "MinSports")):
        rows = [_row_pub("Conférence sur le texte comparatif", source_id=sid, chamber=ch)]
        out = _filter_publications_texte_comparatif(rows)
        assert len(out) == 1, f"Filtré à tort sur source {sid}"


def test_r42bf_couvre_an_rapports_information_et_avis():
    """Couverture aussi des nouvelles sources R42-AJ : RINF et AVIS."""
    for sid in ("an_rapports", "an_rapports_information", "an_avis"):
        rows = [_row_pub("Truc - N° X Texte comparatif", source_id=sid, chamber="AN")]
        out = _filter_publications_texte_comparatif(rows)
        assert len(out) == 0, f"{sid} non filtré"


def test_r42bf_ne_touche_pas_les_autres_champs():
    """Les champs autres que title (summary, keywords…) ne sont pas
    consultés — on filtre strictement sur title."""
    rows = [_row_pub("Rapport normal sport")]
    rows[0]["summary"] = "Ce rapport contient une analyse texte comparatif…"
    out = _filter_publications_texte_comparatif(rows)
    # summary mentionne « texte comparatif » mais title non → on garde
    assert len(out) == 1


# ===========================================================================
# R42-BH — Symétrique R42-AW pour comptes_rendus parlement
# ===========================================================================

def _row_cr(source_id: str, families: list[str] | None = None,
            chamber: str = "AN") -> dict:
    return {
        "source_id": source_id,
        "category": "comptes_rendus",
        "chamber": chamber,
        "keyword_families": families or [],
    }


def test_r42bh_drop_cr_an_avec_nomination_seule():
    """CR AN comptes_rendus avec families=['nomination_event'] → drop."""
    for sid in ("an_syceron", "an_cr_commissions"):
        rows = [_row_cr(sid, families=["nomination_event"], chamber="AN")]
        out = _filter_parlement_cr_nominations_only(rows)
        assert len(out) == 0, f"{sid} pas filtré"


def test_r42bh_drop_cr_senat_avec_nomination_seule():
    """Idem côté Sénat (senat_debats, senat_cri, senat_cr_commissions, etc.)."""
    for sid in ("senat_debats", "senat_cri", "senat_cr_commissions",
                "senat_cr_affaires_sociales"):
        rows = [_row_cr(sid, families=["nomination_event"], chamber="Senat")]
        out = _filter_parlement_cr_nominations_only(rows)
        assert len(out) == 0, f"{sid} pas filtré"


def test_r42bh_garde_cr_avec_famille_sport_en_plus():
    """Si un CR matche aussi une famille sport en plus de nomination_event,
    on garde — c'est probablement un vrai item sport avec mention
    incidente d'une nomination."""
    for extra in ("acteur", "dispositif", "federation", "theme", "evenement"):
        rows = [_row_cr("an_syceron",
                        families=["nomination_event", extra])]
        out = _filter_parlement_cr_nominations_only(rows)
        assert len(out) == 1, f"CR avec famille {extra} drop à tort"


def test_r42bh_garde_cr_sans_nomination_event():
    """CR avec UNE famille sport pure (acteur) → on garde."""
    rows = [_row_cr("an_syceron", families=["acteur"])]
    out = _filter_parlement_cr_nominations_only(rows)
    assert len(out) == 1


def test_r42bh_ignore_communiques():
    """Un communiques avec families=['nomination_event'] n'est PAS touché
    par R42-BH — c'est R42-AW (publications) qui gère ce cas."""
    rows = [{
        "source_id": "an_rapports",
        "category": "communiques",
        "chamber": "AN",
        "keyword_families": ["nomination_event"],
    }]
    out = _filter_parlement_cr_nominations_only(rows)
    assert len(out) == 1  # R42-BH n'agit que sur comptes_rendus


def test_r42bh_ignore_amendements_questions_etc():
    """Les autres catégories (amendements, questions, jorf, agenda)
    avec families=['nomination_event'] ne sont PAS touchées."""
    for cat in ("amendements", "questions", "jorf", "agenda",
                "dossiers_legislatifs", "nominations"):
        rows = [{
            "source_id": "an_amendements",
            "category": cat,
            "chamber": "AN",
            "keyword_families": ["nomination_event"],
        }]
        out = _filter_parlement_cr_nominations_only(rows)
        assert len(out) == 1, f"cat={cat} drop à tort"


def test_r42bh_ignore_sources_non_parlement():
    """Un CR d'une source non-parlement (ex. agenda ministériel mal
    catégorisé) n'est pas touché ici."""
    rows = [_row_cr("min_sports_actualites", families=["nomination_event"],
                    chamber="MinSports")]
    out = _filter_parlement_cr_nominations_only(rows)
    assert len(out) == 1


def test_r42bh_keyword_families_serialise_string():
    """Cas robustesse : keyword_families string JSON parsé correctement."""
    rows = [{
        "source_id": "an_syceron",
        "category": "comptes_rendus",
        "chamber": "AN",
        "keyword_families": '["nomination_event"]',
    }]
    out = _filter_parlement_cr_nominations_only(rows)
    assert len(out) == 0
