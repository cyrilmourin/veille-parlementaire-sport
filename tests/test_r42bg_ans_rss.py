"""Tests R42-BG — handler RSS dédié pour l'ANS (agencedusport.fr/flux-rss).

Vérifie les 3 spécificités du flux Drupal ANS + dédup GUID :
- titres / liens encapsulés dans `<a>` au lieu de texte brut
- pubDate au format FR « mer 01/04/2026 - 12:00 » non RFC822
- doublons d'items par GUID dans le feed
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from src.sources.html_generic import (
    _from_ans_rss,
    _parse_ans_date,
    fetch_source,
)


# ---------------------------------------------------------------------------
# _parse_ans_date
# ---------------------------------------------------------------------------

def test_parse_ans_date_format_canonique():
    assert _parse_ans_date("mer 01/04/2026 - 12:00") == datetime(2026, 4, 1, 12, 0)


def test_parse_ans_date_jour_optionnel():
    """Sans préfixe jour, juste la partie numérique."""
    assert _parse_ans_date("01/04/2026 - 12:00") == datetime(2026, 4, 1, 12, 0)


def test_parse_ans_date_heure_avec_minutes():
    assert _parse_ans_date("ven 06/03/2026 - 14:30") == datetime(2026, 3, 6, 14, 30)


def test_parse_ans_date_format_invalide_returns_none():
    assert _parse_ans_date("invalide") is None
    assert _parse_ans_date("") is None
    assert _parse_ans_date(None) is None  # type: ignore[arg-type]


def test_parse_ans_date_jour_invalide_returns_none():
    """31 février → invalide → None."""
    assert _parse_ans_date("lun 31/02/2026 - 10:00") is None


# ---------------------------------------------------------------------------
# _from_ans_rss : end-to-end via mock fetch_bytes
# ---------------------------------------------------------------------------

_FIXTURE_ANS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>ANS</title>
    <link>http://www.agencedusport.fr/</link>
    <item>
      <title><a href="/actualites/marie-cecile-tardieu" hreflang="fr">Marie-Cécile TARDIEU, nommée DG</a></title>
      <link><a href="/actualites/marie-cecile-tardieu" hreflang="fr">view</a></link>
      <guid isPermaLink="false">guid-1</guid>
      <pubDate>mer 01/04/2026 - 12:00</pubDate>
    </item>
    <item>
      <title><a href="/actualites/marie-cecile-tardieu" hreflang="fr">Marie-Cécile TARDIEU, nommée DG</a></title>
      <link><a href="/actualites/marie-cecile-tardieu" hreflang="fr">view</a></link>
      <guid isPermaLink="false">guid-1</guid>
      <pubDate>mer 01/04/2026 - 12:00</pubDate>
    </item>
    <item>
      <title><a href="/actualites/jop-milan-cortina" hreflang="fr">JOP Milan Cortina</a></title>
      <link><a href="/actualites/jop-milan-cortina" hreflang="fr">view</a></link>
      <guid isPermaLink="false">guid-2</guid>
      <pubDate>ven 06/03/2026 - 12:00</pubDate>
    </item>
  </channel>
</rss>
""".encode("utf-8")


def _src() -> dict:
    return {
        "id": "ans",
        "category": "communiques",
        "url": "https://www.agencedusport.fr/flux-rss",
        "format": "ans_rss",
        "chamber": "ANS",
    }


def test_ans_rss_dedup_par_guid():
    """Le feed Drupal duplique chaque item ; on doit dédup par GUID."""
    with patch("src.sources.html_generic.fetch_bytes",
               return_value=_FIXTURE_ANS):
        items = _from_ans_rss(_src())
    assert len(items) == 2
    titles = {it.title for it in items}
    assert titles == {
        "Marie-Cécile TARDIEU, nommée DG",
        "JOP Milan Cortina",
    }


def test_ans_rss_extrait_url_depuis_a_interne():
    """Le href du <a> dans <title> est le vrai lien, pas /view."""
    with patch("src.sources.html_generic.fetch_bytes",
               return_value=_FIXTURE_ANS):
        items = _from_ans_rss(_src())
    urls = {it.url for it in items}
    assert "https://www.agencedusport.fr/actualites/marie-cecile-tardieu" in urls
    assert "https://www.agencedusport.fr/actualites/jop-milan-cortina" in urls
    # Aucun /view ne doit fuiter
    assert not any(it.url.endswith("/view") for it in items)


def test_ans_rss_titre_sans_balise_html():
    """Le titre extrait doit être le TEXTE du <a>, pas le HTML."""
    with patch("src.sources.html_generic.fetch_bytes",
               return_value=_FIXTURE_ANS):
        items = _from_ans_rss(_src())
    for it in items:
        assert "<a" not in it.title
        assert "href=" not in it.title


def test_ans_rss_date_parsee():
    """Les dates FR sont parsées via _parse_ans_date."""
    with patch("src.sources.html_generic.fetch_bytes",
               return_value=_FIXTURE_ANS):
        items = _from_ans_rss(_src())
    by_title = {it.title: it for it in items}
    assert by_title["Marie-Cécile TARDIEU, nommée DG"].published_at == datetime(2026, 4, 1, 12, 0)
    assert by_title["JOP Milan Cortina"].published_at == datetime(2026, 3, 6, 12, 0)


def test_ans_rss_chamber_ans():
    """Tous les items sortent avec chamber=ANS."""
    with patch("src.sources.html_generic.fetch_bytes",
               return_value=_FIXTURE_ANS):
        items = _from_ans_rss(_src())
    assert {it.chamber for it in items} == {"ANS"}


def test_ans_rss_category_propagee():
    """src.category propagé sur Item.category."""
    with patch("src.sources.html_generic.fetch_bytes",
               return_value=_FIXTURE_ANS):
        items = _from_ans_rss(_src())
    assert {it.category for it in items} == {"communiques"}


def test_ans_rss_source_id_propage():
    with patch("src.sources.html_generic.fetch_bytes",
               return_value=_FIXTURE_ANS):
        items = _from_ans_rss(_src())
    assert {it.source_id for it in items} == {"ans"}


def test_ans_rss_fetch_ko_soft_fail():
    """fetch_bytes lève → on retourne [] sans crasher."""
    def _boom(*args, **kwargs):
        raise RuntimeError("ConnectTimeout")
    with patch("src.sources.html_generic.fetch_bytes", side_effect=_boom):
        items = _from_ans_rss(_src())
    assert items == []


def test_ans_rss_dispatch_via_fetch_source():
    """Le dispatcher fetch_source route format=ans_rss vers _from_ans_rss."""
    with patch("src.sources.html_generic.fetch_bytes",
               return_value=_FIXTURE_ANS):
        items = fetch_source(_src())
    assert len(items) == 2


def test_ans_rss_item_sans_date_garde_published_at_none():
    """Une offre d'emploi (pas de pubDate) sort avec published_at=None.

    L'item sera ensuite filtré par STRICT_DATED_CATEGORIES côté
    site_export (catégorie communiques exige une date) — comportement
    attendu : on ne veut pas d'offres d'emploi dans la veille."""
    no_date_fixture = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item>
    <title><a href="/jobs/offer" hreflang="fr">Conseiller.ère</a></title>
    <link><a href="/jobs/offer" hreflang="fr">view</a></link>
    <guid isPermaLink="false">guid-job</guid>
  </item>
</channel></rss>""".encode("utf-8")
    with patch("src.sources.html_generic.fetch_bytes",
               return_value=no_date_fixture):
        items = _from_ans_rss(_src())
    assert len(items) == 1
    assert items[0].published_at is None
