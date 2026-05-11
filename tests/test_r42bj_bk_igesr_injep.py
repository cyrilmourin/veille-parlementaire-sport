"""Tests R42-BJ + R42-BK — handlers dédiés rapports IGESR sport (MinSports)
et publications sport INJEP.

R42-BJ : page MinSports `rapports-de-l-igesr-dans-le-champ-du-sport-1703`,
  rapports en PDF directs hébergés sur `/sites/default/files/YYYY-MM/`.
R42-BK : page INJEP `injep.fr/sport/les-publications-sport/`,
  CPT WordPress `<li class="publication">`, date depuis URL image
  `/wp-content/uploads/YYYY/MM/`.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from src.sources.html_generic import (
    _from_min_sports_igesr_html,
    _from_injep_sport_publications_html,
    fetch_source,
)


# ===========================================================================
# R42-BJ — Handler MinSports rapports IGESR sport
# ===========================================================================

_IGESR_FIXTURE = """<!DOCTYPE html><html><body>
<main>
  <a href="https://www.sports.gouv.fr/sites/default/files/2025-12/rapport-maisons-sport-sante.pdf">
    Les Maisons sport-santé, rapport n° 23-24-183B, conjoint avec l'IGAS, octobre 2025
  </a>
  <a href="https://www.sports.gouv.fr/sites/default/files/2025-08/heritage-jop-paris-2024-educatif.pdf">
    L'héritage des JOP de Paris 2024 au plan éducatif, rapport 24-25-025C, juillet 2025
  </a>
  <a href="https://www.sports.gouv.fr/sites/default/files/2024-07/rapport-quelle-gouvernance-esport.pdf">
    Rapport : Quelle gouvernance pour le développement du e-sport en France ?
  </a>
  <a href="/sites/default/files/2023-02/communautarisme-associations-sportives.pdf">
    Les phénomènes de communautarisme dans les associations sportives et de jeunesse
  </a>
  <a href="https://www.sports.gouv.fr/autre-page">Lien interne sans format IGESR</a>
  <a href="/sites/default/files/2025-12/rapport-maisons-sport-sante.pdf">
    Doublon URL (à dédup)
  </a>
  <a href="/sites/default/files/2025-12/x.pdf">court</a>
</main>
</body></html>"""


def _src_igesr() -> dict:
    return {
        "id": "min_sports_igesr",
        "category": "communiques",
        "url": "https://www.sports.gouv.fr/rapports-de-l-igesr-dans-le-champ-du-sport-1703",
        "format": "min_sports_igesr_html",
        "chamber": "MinSports",
    }


def test_r42bj_extract_4_rapports_uniques_depuis_fixture():
    with patch("src.sources.html_generic.fetch_text", return_value=_IGESR_FIXTURE):
        items = _from_min_sports_igesr_html(_src_igesr())
    assert len(items) == 4  # 4 PDF distincts (dédup OK, lien court rejeté)


def test_r42bj_date_extraite_depuis_url():
    """Pour chaque PDF, la date est extraite du segment `/YYYY-MM/` de l'URL."""
    with patch("src.sources.html_generic.fetch_text", return_value=_IGESR_FIXTURE):
        items = _from_min_sports_igesr_html(_src_igesr())
    dates = {it.url.split("/files/")[1][:7]: it.published_at for it in items}
    assert dates["2025-12"] == datetime(2025, 12, 1)
    assert dates["2025-08"] == datetime(2025, 8, 1)
    assert dates["2024-07"] == datetime(2024, 7, 1)
    assert dates["2023-02"] == datetime(2023, 2, 1)


def test_r42bj_url_absolue_meme_si_href_relatif():
    """Un href relatif `/sites/default/files/...` est converti en URL absolue."""
    with patch("src.sources.html_generic.fetch_text", return_value=_IGESR_FIXTURE):
        items = _from_min_sports_igesr_html(_src_igesr())
    for it in items:
        assert it.url.startswith("https://www.sports.gouv.fr/")


def test_r42bj_chamber_minsports():
    with patch("src.sources.html_generic.fetch_text", return_value=_IGESR_FIXTURE):
        items = _from_min_sports_igesr_html(_src_igesr())
    assert {it.chamber for it in items} == {"MinSports"}


def test_r42bj_dispatch_via_fetch_source():
    """Le dispatcher fetch_source route format=min_sports_igesr_html."""
    with patch("src.sources.html_generic.fetch_text", return_value=_IGESR_FIXTURE):
        items = fetch_source(_src_igesr())
    assert len(items) == 4


def test_r42bj_fetch_ko_soft_fail():
    def _boom(*args, **kwargs):
        raise RuntimeError("ConnectTimeout")
    with patch("src.sources.html_generic.fetch_text", side_effect=_boom):
        items = _from_min_sports_igesr_html(_src_igesr())
    assert items == []


# ===========================================================================
# R42-BK — Handler INJEP publications sport
# ===========================================================================

_INJEP_FIXTURE = """<!DOCTYPE html><html><body>
<ul>
<li class="publication thematique-sport collection-injep-analyses-syntheses">
  <figure><a href="https://injep.fr/publication/abandon-pratique/">
    <img src="https://injep.fr/wp-content/uploads/2026/04/IAS92_couv.jpg" alt=""/>
  </a></figure>
  <h2><a href="https://injep.fr/publication/abandon-pratique/">Entre 14 et 18 ans, un jeune sur quatre abandonne la pratique régulière du sport</a></h2>
  <a class="collection" href="https://injep.fr/collection/injep-analyses-syntheses/">INJEP Analyses &amp; synthèses</a>
</li>
<li class="publication thematique-sport">
  <figure><a href="/publication/pratique-sportive-france-2025/">
    <img src="/wp-content/uploads/2025/11/barometre-2025.jpg"/>
  </a></figure>
  <h2><a href="/publication/pratique-sportive-france-2025/">La pratique sportive en France en 2025 après les Jeux de Paris</a></h2>
</li>
<li class="publication">
  <h2><a href="https://injep.fr/publication/poids-economique/">Poids économique du sport en 2023</a></h2>
  <img src="/wp-content/uploads/2024/06/poids-economique.jpg"/>
</li>
<li class="publication">
  <h2><a href="https://injep.fr/publication/sans-image/">Publication sans image (date inconnue)</a></h2>
</li>
<li class="publication">
  <!-- Pas de h2 → ignoré -->
  <p>Item invalide</p>
</li>
</ul>
</body></html>"""


def _src_injep() -> dict:
    return {
        "id": "injep_sport_publications",
        "category": "communiques",
        "url": "https://injep.fr/sport/les-publications-sport/",
        "format": "injep_sport_publications_html",
        "chamber": "INJEP",
    }


def test_r42bk_extract_4_publications_uniques():
    """4 publications valides extraites (la 5e <li> sans h2 est ignorée)."""
    with patch("src.sources.html_generic.fetch_text", return_value=_INJEP_FIXTURE):
        items = _from_injep_sport_publications_html(_src_injep())
    assert len(items) == 4


def test_r42bk_date_depuis_url_image():
    """La date est extraite de l'URL de l'image WP `/uploads/YYYY/MM/`."""
    with patch("src.sources.html_generic.fetch_text", return_value=_INJEP_FIXTURE):
        items = _from_injep_sport_publications_html(_src_injep())
    by_title = {it.title: it for it in items}
    abandon = by_title["Entre 14 et 18 ans, un jeune sur quatre abandonne la pratique régulière du sport"]
    assert abandon.published_at == datetime(2026, 4, 1)
    france_2025 = by_title["La pratique sportive en France en 2025 après les Jeux de Paris"]
    assert france_2025.published_at == datetime(2025, 11, 1)
    poids = by_title["Poids économique du sport en 2023"]
    assert poids.published_at == datetime(2024, 6, 1)


def test_r42bk_date_none_si_pas_image():
    """Une publication sans image → published_at = None (sera filtré par
    STRICT_DATED_CATEGORIES côté communiques, comportement attendu)."""
    with patch("src.sources.html_generic.fetch_text", return_value=_INJEP_FIXTURE):
        items = _from_injep_sport_publications_html(_src_injep())
    by_title = {it.title: it for it in items}
    sans = by_title["Publication sans image (date inconnue)"]
    assert sans.published_at is None


def test_r42bk_collection_dans_raw():
    """Le label collection (« INJEP Analyses & synthèses ») est posé
    dans raw['collection'] + summary."""
    with patch("src.sources.html_generic.fetch_text", return_value=_INJEP_FIXTURE):
        items = _from_injep_sport_publications_html(_src_injep())
    by_title = {it.title: it for it in items}
    abandon = by_title["Entre 14 et 18 ans, un jeune sur quatre abandonne la pratique régulière du sport"]
    assert "INJEP Analyses" in abandon.summary
    assert "INJEP Analyses" in (abandon.raw.get("collection") or "")


def test_r42bk_chamber_injep():
    with patch("src.sources.html_generic.fetch_text", return_value=_INJEP_FIXTURE):
        items = _from_injep_sport_publications_html(_src_injep())
    assert {it.chamber for it in items} == {"INJEP"}


def test_r42bk_url_absolue():
    """Les hrefs relatifs sont convertis en URL absolues."""
    with patch("src.sources.html_generic.fetch_text", return_value=_INJEP_FIXTURE):
        items = _from_injep_sport_publications_html(_src_injep())
    for it in items:
        assert it.url.startswith("https://injep.fr/")


def test_r42bk_dispatch_via_fetch_source():
    with patch("src.sources.html_generic.fetch_text", return_value=_INJEP_FIXTURE):
        items = fetch_source(_src_injep())
    assert len(items) == 4


def test_r42bk_fetch_ko_soft_fail():
    def _boom(*args, **kwargs):
        raise RuntimeError("HTTP 503")
    with patch("src.sources.html_generic.fetch_text", side_effect=_boom):
        items = _from_injep_sport_publications_html(_src_injep())
    assert items == []


# ===========================================================================
# Famille de source — IGESR et INJEP doivent être en "operateurs_publics"
# ===========================================================================

def test_min_sports_igesr_classe_operateurs_publics():
    """Cyril 2026-05-11 : « INJEP et IGESR à classer dans opérateurs
    publics ». Sans override explicite, `min_sports_igesr` matcherait
    le préfixe `min_` → gouvernement. R42-BJ pose l'override."""
    from src.site_export import _source_family
    assert _source_family("min_sports_igesr", "MinSports") == "operateurs_publics"


def test_injep_sport_publications_classe_operateurs_publics():
    """Idem pour `injep_sport_publications` (pas de préfixe `injep_`
    dans le mapping, donc override explicite nécessaire)."""
    from src.site_export import _source_family
    assert _source_family("injep_sport_publications", "INJEP") == "operateurs_publics"


def test_min_sports_actualites_reste_gouvernement():
    """Non-régression : `min_sports_actualites` reste « gouvernement »
    (l'override IGESR ne déborde pas sur les autres sources MinSports)."""
    from src.site_export import _source_family
    assert _source_family("min_sports_actualites", "MinSports") == "gouvernement"
