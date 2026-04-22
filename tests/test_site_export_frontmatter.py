"""Tests de régression pour le frontmatter des pages item.

R15 (2026-04-22) : `date:` doit toujours être émise (fallback
`inserted_at`) pour éviter le filtre silencieux Hugo sur les items
`type: <cat>` sans date. `published_at_real` + `has_real_date`
permettent aux templates de savoir si la date est fiable.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.site_export import _write_item_pages  # noqa: E402


def _row(
    uid: str = "u1",
    published_at: str | None = None,
    inserted_at: str = "2026-04-22T08:00:00",
) -> dict:
    return {
        "hash_key": f"src::{uid}",
        "source_id": "src",
        "uid": uid,
        "category": "agenda",
        "chamber": "AN",
        "title": "Réunion de commission",
        "url": "https://example.com/1",
        "published_at": published_at,
        "summary": "texte",
        "matched_keywords": '["sport"]',
        "keyword_families": '["sport"]',
        "snippet": "",
        "raw": "{}",
        "inserted_at": inserted_at,
    }


def test_frontmatter_emits_real_date_when_available(tmp_path):
    rows = [_row(published_at="2026-04-10T09:00:00")]
    _write_item_pages(tmp_path, rows)
    # 1 fichier .md produit
    files = list((tmp_path / "agenda").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "date: 2026-04-10T09:00:00" in text
    assert "published_at_real: 2026-04-10T09:00:00" in text
    assert "has_real_date: true" in text


def test_frontmatter_falls_back_to_inserted_at_when_no_date(tmp_path):
    """Cas AN agenda R15 : dateless items ne doivent plus être filtrés
    silencieusement par Hugo."""
    rows = [_row(published_at=None, inserted_at="2026-04-22T08:00:00")]
    _write_item_pages(tmp_path, rows)
    files = list((tmp_path / "agenda").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    # `date:` doit être présente (sinon Hugo filtre)
    assert "date: 2026-04-22T08:00:00" in text
    # pas de `published_at_real:` puisque la date réelle est absente
    assert "published_at_real:" not in text
    # flag `has_real_date: false` pour signaler le fallback
    assert "has_real_date: false" in text


def test_frontmatter_has_real_date_false_when_empty_string(tmp_path):
    """published_at="" (pas NULL mais chaîne vide) doit être traité
    comme absent — sinon has_real_date serait truthy à tort."""
    rows = [_row(published_at="", inserted_at="2026-04-22T08:00:00")]
    _write_item_pages(tmp_path, rows)
    files = list((tmp_path / "agenda").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "has_real_date: false" in text
    assert "published_at_real:" not in text
    # fallback inserted_at
    assert "date: 2026-04-22T08:00:00" in text
