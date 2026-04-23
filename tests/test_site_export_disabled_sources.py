"""Tests sur le filtre `_filter_disabled_sources` (R22b, 2026-04-23).

Motivation métier : quand une source est marquée `enabled: false` dans
config/sources.yml (ex. `alpes_2030_news` en R17, `senat_theme_sport_rss`
en R19-B), ses items déjà en DB continuent de s'afficher sur le site
jusqu'à expiration de la fenêtre de publication (30 à 180 jours selon
catégorie). Le filtre rend la désactivation effective immédiatement au
prochain export, sans dépendre d'un reset DB.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.site_export import (  # noqa: E402
    _filter_disabled_sources,
    _load_disabled_source_ids,
)


def test_load_disabled_source_ids_reads_real_yaml():
    """Sentinelle : lit le vrai config/sources.yml et vérifie que les
    sources marquées `enabled: false` remontent. Test de régression si
    quelqu'un casse le chemin du fichier ou la structure YAML."""
    disabled = _load_disabled_source_ids()
    # Au 2026-04-23, ces IDs sont documentés `enabled: false` :
    # - alpes_2030_news (R17, source Google News écartée)
    # - senat_theme_sport_rss (R19-B, flux RSS thème Sport qui produisait
    #   des doublons de pjl24-630)
    assert "alpes_2030_news" in disabled, (
        "alpes_2030_news devrait être dans les sources disabled"
    )
    assert "senat_theme_sport_rss" in disabled, (
        "senat_theme_sport_rss devrait être dans les sources disabled"
    )


def test_filter_removes_rows_from_disabled_source():
    """Un row avec `source_id` matchant une source disabled doit être
    exclu. Un row d'une source active doit passer."""
    rows = [
        {"uid": "a", "source_id": "alpes_2030_news", "title": "Google News item"},
        {"uid": "b", "source_id": "an_dosleg", "title": "Dossier AN"},
        {"uid": "c", "source_id": "senat_theme_sport_rss", "title": "Sénat RSS"},
    ]
    with patch(
        "src.site_export._load_disabled_source_ids",
        return_value={"alpes_2030_news", "senat_theme_sport_rss"},
    ):
        kept = _filter_disabled_sources(rows)
    kept_ids = {r["uid"] for r in kept}
    assert kept_ids == {"b"}, (
        f"attendu uniquement 'b' (an_dosleg), vu {kept_ids}"
    )


def test_filter_is_noop_when_no_disabled_sources():
    """Si `_load_disabled_source_ids` retourne un set vide (yaml illisible
    ou toutes sources actives), le filtre doit être no-op."""
    rows = [
        {"uid": "a", "source_id": "foo"},
        {"uid": "b", "source_id": "bar"},
    ]
    with patch("src.site_export._load_disabled_source_ids", return_value=set()):
        kept = _filter_disabled_sources(rows)
    assert len(kept) == 2
    assert [r["uid"] for r in kept] == ["a", "b"]


def test_filter_handles_missing_source_id_gracefully():
    """Row sans `source_id` (edge-case historique) doit être conservé —
    on ne veut pas perdre de données à cause d'un champ manquant."""
    rows = [
        {"uid": "a"},
        {"uid": "b", "source_id": ""},
        {"uid": "c", "source_id": "alpes_2030_news"},
    ]
    with patch(
        "src.site_export._load_disabled_source_ids",
        return_value={"alpes_2030_news"},
    ):
        kept = _filter_disabled_sources(rows)
    kept_ids = {r["uid"] for r in kept}
    assert kept_ids == {"a", "b"}, (
        f"rows sans source_id doivent passer, vu {kept_ids}"
    )
