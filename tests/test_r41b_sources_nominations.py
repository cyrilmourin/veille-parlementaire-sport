"""R41-B (2026-04-27) — Sources presse spécialisée + fédérations majeures.

Couche 2/2 du chantier nominations sport (couche 1 = R41-A reroute via
famille `nomination_event`). Ajout au YAML de 4 sources actives + 1
désactivée :

- sport_strategies : RSS — référence business sport, nominations + deals
- fff_actualites : HTML — football
- fft_actualites : HTML — tennis
- ffa_actualites : HTML — athlétisme
- ffr_actualites : HTML, DÉSACTIVÉ — SPA Nuxt non-scrapable. À réactiver
  via headless browser plus tard si besoin.

FFBB et FFHB également écartées (SPA / URLs 404). Sites server-rendered
seulement pour cette couche.

Le matcher keyword (R41-A + dictionnaire général) fait le tri à chaque
run : seuls les items qui matchent un keyword (sport-relevant ou
nomination_event) remontent côté site. Pas de pollution massive
attendue par les flux d'actualités (résultats, transferts).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


SOURCES_YAML = Path(__file__).resolve().parent.parent / "config" / "sources.yml"


@pytest.fixture
def all_sources() -> list[dict]:
    with SOURCES_YAML.open() as f:
        cfg = yaml.safe_load(f)
    out: list[dict] = []
    for grp_val in cfg.values():
        if not isinstance(grp_val, dict):
            continue
        for s in grp_val.get("sources", []) or []:
            if isinstance(s, dict):
                out.append(s)
    return out


def _by_id(sources: list[dict]) -> dict[str, dict]:
    return {s.get("id"): s for s in sources if s.get("id")}


# ---------------------------------------------------------------------------
# 1. Présence des nouvelles sources
# ---------------------------------------------------------------------------


def test_r41b_sport_strategies_present(all_sources):
    s = _by_id(all_sources).get("sport_strategies")
    assert s is not None
    assert s["format"] == "rss"
    assert "sportstrategies.com" in s["url"]
    assert s.get("enabled", True) is not False


def test_r41b_federations_majeures_presentes(all_sources):
    """3 fédérations actives + 1 désactivée (FFR SPA Nuxt)."""
    by_id = _by_id(all_sources)
    actives = ("fff_actualites", "fft_actualites", "ffa_actualites")
    for sid in actives:
        s = by_id.get(sid)
        assert s is not None, f"Source {sid} manquante"
        assert s.get("enabled", True) is not False, f"{sid} désactivée"
        assert s["format"] == "html"
        assert s.get("fetch_meta") is True


def test_r41b_ffr_explicitement_desactivee(all_sources):
    """FFR site SPA Nuxt → désactivée volontairement avec marqueur
    `enabled: false`. Si on retire ça plus tard (headless browser),
    il faudra retester live qu'on récupère du HTML scrapable."""
    s = _by_id(all_sources).get("ffr_actualites")
    assert s is not None
    assert s.get("enabled") is False


def test_r41b_toutes_en_categorie_communiques(all_sources):
    """Les 4 sources actives sont en `communiques`. Le re-route R41-A
    bascule automatiquement vers `nominations` les items qui matchent
    la famille `nomination_event`. Pas de catégorie `nominations`
    posée à l'ingestion."""
    by_id = _by_id(all_sources)
    for sid in ("sport_strategies", "fff_actualites", "fft_actualites",
                "ffa_actualites", "ffr_actualites"):
        s = by_id.get(sid)
        assert s is not None
        assert s["category"] == "communiques", (
            f"{sid} doit être en communiques pour bénéficier du reroute "
            "R41-A vers nominations (et conserver les autres items en "
            "communiques s'ils ne matchent pas nomination_event)."
        )


# ---------------------------------------------------------------------------
# 2. Sanity : URLs et formats cohérents
# ---------------------------------------------------------------------------


def test_r41b_urls_https(all_sources):
    by_id = _by_id(all_sources)
    for sid in ("sport_strategies", "fff_actualites", "fft_actualites",
                "ffa_actualites"):
        s = by_id[sid]
        assert s["url"].startswith("https://"), (
            f"{sid} doit utiliser https"
        )


def test_r41b_chamber_propre(all_sources):
    """Les sources fédé ont un `chamber` court qui s'affiche en badge."""
    by_id = _by_id(all_sources)
    assert by_id["fff_actualites"]["chamber"] == "FFF"
    assert by_id["fft_actualites"]["chamber"] == "FFT"
    assert by_id["ffa_actualites"]["chamber"] == "FFA"


def test_r41b_pas_de_doublon_id(all_sources):
    """Régression : pas de duplicate id entre les sources existantes
    et les nouvelles."""
    ids = [s["id"] for s in all_sources if "id" in s]
    assert len(ids) == len(set(ids)), (
        f"IDs dupliqués détectés : "
        f"{sorted(set(x for x in ids if ids.count(x) > 1))}"
    )


# ---------------------------------------------------------------------------
# R41-C — Presse spécialisée sport business
# ---------------------------------------------------------------------------


def test_r41c_olbia_conseil(all_sources):
    s = _by_id(all_sources).get("olbia_conseil")
    assert s is not None
    assert s["format"] == "rss"
    assert "olbia-conseil.com" in s["url"]
    assert s.get("enabled", True) is not False


def test_r41c_cafe_sport_business(all_sources):
    """Sitemap + impersonate (Cloudflare protège les pages mais pas le
    sitemap)."""
    s = _by_id(all_sources).get("cafe_sport_business")
    assert s is not None
    assert s["format"] == "sitemap"
    assert s.get("impersonate") is True, (
        "Cloudflare protège les pages → impersonate requis pour fetch_meta"
    )
    # url_filter cible les éditions /p/<slug>
    assert "/p/" in s.get("url_filter", [None])[0]


def test_r41c_sport_buzz_business(all_sources):
    """Note URL : pas de slash final (le /feed/ redirige vers /feed)."""
    s = _by_id(all_sources).get("sport_buzz_business")
    assert s is not None
    assert s["format"] == "rss"
    assert s["url"].endswith("/feed"), (
        "URL Sport Buzz Business doit pointer sur /feed (sans slash)"
    )


def test_r41c_sport_business_club(all_sources):
    s = _by_id(all_sources).get("sport_business_club")
    assert s is not None
    assert s["format"] == "rss"
    assert "sportbusiness.club" in s["url"]


def test_r41c_toutes_en_communiques(all_sources):
    """Comme les autres sources nominations, en `communiques` avec
    re-route automatique vers nominations via R41-A."""
    by_id = _by_id(all_sources)
    for sid in ("olbia_conseil", "cafe_sport_business",
                "sport_buzz_business", "sport_business_club"):
        s = by_id[sid]
        assert s["category"] == "communiques"
