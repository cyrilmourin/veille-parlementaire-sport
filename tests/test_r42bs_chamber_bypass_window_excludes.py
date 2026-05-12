"""R42-BS — 4 fixes :

1. **Chamber IGESR** : les rapports IGESR sport (`min_sports_igesr`)
   sortent désormais avec `chamber=IGESR` (et plus `MinSports`). Cyril
   2026-05-13 : « le cartouche pour l'IGESR est MIN SPORT, je souhaite
   que ce soit IGESR ».

2. **Retrait bypass keyword IGESR** : `min_sports_igesr` n'est plus dans
   `BYPASS_KEYWORDS_SOURCES`. Conséquence : plus de badge « ⊕ Source
   institutionnelle (flux complet) » sur les cards IGESR.

3. **Fenêtre rapports parlementaires** : retrait de `an_rapports`,
   `senat_rapports`, `an_avis`, etc. de `_DYNAMIC_WINDOWS_BY_SOURCE_ID`.
   La fenêtre nominale 15j faisait disparaître quasi toutes les
   publications parlementaires (volume trop faible / cycle trop long).
   Repassage sur la fenêtre statique 730j (`WINDOW_DAYS_BY_SOURCE_ID`).

4. **Filtre `url_filter_exclude` rétroactif** : applique les patterns
   d'exclusion YAML aux items déjà en DB. R42-BM filtrait au scrape
   uniquement, donc les anciens items CNOSF (composition, conciliation…)
   restaient visibles. Le filtre R42-BS purge à l'export.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src import main as srcmain
from src import site_export
from src.sources import html_generic


# ---------------------------------------------------------------------------
# 1. Chamber IGESR par défaut
# ---------------------------------------------------------------------------

_IGESR_PAGE_HTML = """
<html><body>
  <a href="/sites/default/files/2026-03/rapport-test-igesr-sport.pdf">
    Rapport IGESR n°2026-001 sur le sport-santé
  </a>
</body></html>
"""


def test_min_sports_igesr_chamber_default_is_igesr():
    """Sans chamber explicite, le handler renvoie chamber='IGESR'."""
    src = {
        "id": "min_sports_igesr",
        "category": "communiques",
        "url": "https://www.sports.gouv.fr/rapports-de-l-igesr-...",
        "format": "min_sports_igesr_html",
    }
    with patch("src.sources.html_generic.fetch_text", return_value=_IGESR_PAGE_HTML):
        items = html_generic._from_min_sports_igesr_html(src)
    assert items, "au moins 1 item attendu"
    assert all(it.chamber == "IGESR" for it in items)


def test_min_sports_igesr_chamber_override_respected():
    """Un chamber explicite dans le YAML reste prioritaire (au cas où)."""
    src = {
        "id": "min_sports_igesr",
        "category": "communiques",
        "url": "https://www.sports.gouv.fr/rapports-de-l-igesr-...",
        "format": "min_sports_igesr_html",
        "chamber": "MinSportsOverride",
    }
    with patch("src.sources.html_generic.fetch_text", return_value=_IGESR_PAGE_HTML):
        items = html_generic._from_min_sports_igesr_html(src)
    assert all(it.chamber == "MinSportsOverride" for it in items)


# ---------------------------------------------------------------------------
# 2. Retrait du bypass keyword IGESR
# ---------------------------------------------------------------------------

def test_min_sports_igesr_not_in_bypass_keywords_sources():
    """Aucun badge « (flux complet) » à venir : la source n'est plus
    listée dans le bypass."""
    assert "min_sports_igesr" not in srcmain.BYPASS_KEYWORDS_SOURCES


def test_other_bypass_sources_still_present():
    """Garde-fou : les autres sources opérateurs publics gardent leur
    bypass (ANS, INSEP, INJEP, AFLD, MinSports actu/presse)."""
    expected = {"ans", "insep", "injep_sport_publications", "afld",
                "min_sports_actualites", "min_sports_presse"}
    assert expected.issubset(srcmain.BYPASS_KEYWORDS_SOURCES)


# ---------------------------------------------------------------------------
# 3. Fenêtre rapports parlementaires — retrait de la dynamique 15j
# ---------------------------------------------------------------------------

def test_parlement_rapports_no_longer_in_dynamic_window():
    """Les 6 sources rapports parlementaires ne sont plus en dynamique
    nominale 15j — elles repassent sur la fenêtre statique 730j."""
    parlement_sources = {
        "an_rapports", "senat_rapports",
        "an_rapports_information", "an_avis",
        "an_rapports_application_loi", "an_rapports_information_ce",
    }
    for sid in parlement_sources:
        assert sid not in site_export._DYNAMIC_WINDOWS_BY_SOURCE_ID, (
            f"{sid} encore dans _DYNAMIC_WINDOWS_BY_SOURCE_ID — Cyril a "
            "constaté la disparition des publications parlementaires"
        )


def test_parlement_rapports_window_falls_back_to_static_730(monkeypatch):
    """En nominal comme en full, la fenêtre vient de
    `WINDOW_DAYS_BY_SOURCE_ID` = 730j."""
    monkeypatch.delenv("RUN_MODE", raising=False)  # nominal
    for sid in ("an_rapports", "senat_rapports", "an_avis",
                "an_rapports_information",
                "an_rapports_application_loi",
                "an_rapports_information_ce"):
        assert site_export._window_for("communiques", source_id=sid) == 730


# ---------------------------------------------------------------------------
# 4. Filtre url_filter_exclude rétroactif (CNOSF)
# ---------------------------------------------------------------------------

_FAKE_YAML = textwrap.dedent("""
parlement:
  sources:
    - id: cnosf
      category: communiques
      url: https://cnosf.franceolympique.com/sitemap.xml
      format: sitemap
      url_filter_exclude:
        - "/la-composition-"
        - "conciliation"
    - id: defenseur_droits
      category: communiques
      url: https://www.defenseurdesdroits.fr/rss.xml
      format: rss
""")


@pytest.fixture
def fake_yaml(tmp_path):
    p = tmp_path / "sources.yml"
    p.write_text(_FAKE_YAML, encoding="utf-8")
    return str(p)


def test_load_url_filter_exclude_extracts_patterns(fake_yaml):
    """Le loader renvoie {source_id: [patterns]} avec lowercase."""
    by_sid = site_export._load_url_filter_exclude_by_source(config_path=fake_yaml)
    assert "cnosf" in by_sid
    assert "/la-composition-" in by_sid["cnosf"]
    assert "conciliation" in by_sid["cnosf"]
    # Une source sans url_filter_exclude n'apparaît pas
    assert "defenseur_droits" not in by_sid


def test_filter_excluded_sitemap_urls_drops_legacy_cnosf_item(fake_yaml):
    """L'item CNOSF /la-composition-... est purgé même s'il était en DB
    avant l'ajout du pattern dans R42-BM."""
    rows = [
        {"source_id": "cnosf", "url": "https://cnosf.franceolympique.com/la-composition-de-la-conference-des-conciliateurs"},
        {"source_id": "cnosf", "url": "https://cnosf.franceolympique.com/2026/05/13/podium-jo"},
        {"source_id": "cnosf", "url": "https://cnosf.franceolympique.com/comment-saisir-la-conciliation"},
        {"source_id": "defenseur_droits", "url": "https://www.defenseurdesdroits.fr/un-article"},
    ]
    with patch("src.site_export._load_url_filter_exclude_by_source",
               return_value=site_export._load_url_filter_exclude_by_source(config_path=fake_yaml)):
        kept = site_export._filter_excluded_sitemap_urls(rows)
    urls = [r["url"] for r in kept]
    # Drops : composition + conciliation
    assert all("la-composition" not in u for u in urls)
    assert all("conciliation" not in u for u in urls)
    # Conserve : actu sport CNOSF + RSS défenseur des droits
    assert "https://cnosf.franceolympique.com/2026/05/13/podium-jo" in urls
    assert "https://www.defenseurdesdroits.fr/un-article" in urls
    assert len(kept) == 2


def test_filter_excluded_sitemap_urls_idempotent(fake_yaml):
    """Re-appliquer le filtre 2× ne change rien (rows sans patterns intacts)."""
    rows = [
        {"source_id": "defenseur_droits", "url": "https://www.defenseurdesdroits.fr/un-article"},
    ]
    with patch("src.site_export._load_url_filter_exclude_by_source",
               return_value=site_export._load_url_filter_exclude_by_source(config_path=fake_yaml)):
        kept = site_export._filter_excluded_sitemap_urls(rows)
        kept2 = site_export._filter_excluded_sitemap_urls(kept)
    assert kept == kept2 == rows


def test_filter_excluded_sitemap_urls_no_op_when_no_patterns_configured():
    """Si aucune source n'a `url_filter_exclude`, le filtre est no-op."""
    rows = [{"source_id": "anything", "url": "https://example.com/a"}]
    with patch("src.site_export._load_url_filter_exclude_by_source",
               return_value={}):
        kept = site_export._filter_excluded_sitemap_urls(rows)
    assert kept == rows
