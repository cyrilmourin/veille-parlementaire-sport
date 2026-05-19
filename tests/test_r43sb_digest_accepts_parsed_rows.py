"""Tests R43-S.b (2026-05-19) — Hotfix : `digest.build_html` doit
accepter les rows post-`site_export.filtered_rows` où `matched_keywords`
et `raw` sont déjà des objets Python (list/dict), pas des strings JSON.

Cause du crash run daily 18/05 21:14 (TypeError introduit par R43-S) :
    File "src/digest.py", line 117
      matched = json.loads(r.get("matched_keywords") or "[]")
    TypeError: the JSON object must be str, bytes or bytearray, not list

Avant R43-S : `digest_rows = store.fetch_matched_since(...)` retournait
des rows SQLite avec `matched_keywords` en TEXT (string JSON) et `raw`
en TEXT aussi.
Depuis R43-S : `digest_rows = summary["filtered_rows"]` passe par
`site_export._load(rows)` qui parse `matched_keywords` et `raw` en
objets Python. Le `json.loads` initial crashait donc en cascade.

Fix : double tolérance dans `digest.build_html` : si list/dict déjà
parsé → use as-is ; sinon → json.loads avec garde-fou.
"""
from __future__ import annotations

import json
from datetime import datetime


def _make_row_legacy(matched_keywords: str, raw: str) -> dict:
    """Format legacy : strings JSON (provenance store.fetch_matched_since)."""
    return {
        "hash_key": "abc",
        "source_id": "test",
        "uid": "u1",
        "category": "amendements",
        "chamber": "AN",
        "title": "Test amendement sport",
        "url": "https://example.com/a1",
        "published_at": "2026-05-18T10:00:00",
        "summary": "Texte amendement sport football",
        "matched_keywords": matched_keywords,
        "keyword_families": "[]",
        "raw": raw,
        "inserted_at": "2026-05-18T20:00:00",
    }


def _make_row_parsed(matched_keywords: list, raw: dict) -> dict:
    """Format post-R43-S : list/dict déjà parsés (provenance
    site_export.filtered_rows)."""
    r = _make_row_legacy("[]", "{}")
    r["matched_keywords"] = matched_keywords
    r["raw"] = raw
    return r


def test_r43sb_build_html_accepte_matched_keywords_en_list():
    """Une row avec `matched_keywords` déjà parsé en list Python doit
    être traitée correctement par `build_html` — pas de TypeError."""
    from src.digest import build_html

    rows = [_make_row_parsed(["sport", "football"], {"status_label": "déposé"})]
    html, total = build_html(rows, "https://veille.sideline-conseil.fr/")
    assert total == 1, "L'item doit être inclus dans le digest"
    assert "Test amendement sport" in html


def test_r43sb_build_html_accepte_matched_keywords_en_string_json():
    """Format legacy `matched_keywords='["sport"]'` continue de marcher
    (rétro-compatibilité). Garantit que le hotfix ne casse pas l'ancien
    chemin si on doit y revenir."""
    from src.digest import build_html

    rows = [_make_row_legacy('["sport", "football"]', '{"status_label":"déposé"}')]
    html, total = build_html(rows, "https://veille.sideline-conseil.fr/")
    assert total == 1
    assert "Test amendement sport" in html


def test_r43sb_build_html_resilient_string_json_corrompu():
    """`matched_keywords` non-JSON valide → fallback `[]` propre (l'item
    est ignoré, pas de crash). Garde-fou défensif au cas où la DB
    contiendrait une string mal sérialisée."""
    from src.digest import build_html

    rows = [_make_row_legacy("not valid json{", "{}")]
    html, total = build_html(rows, "https://veille.sideline-conseil.fr/")
    assert total == 0, "Item avec matched_keywords cassé doit être ignoré"


def test_r43sb_build_html_resilient_raw_dict_vs_string():
    """`raw` peut être dict (post-R43-S) ou string JSON (legacy).
    Les deux doivent fonctionner."""
    from src.digest import build_html

    # Format parsed (dict)
    row_dict = _make_row_parsed(["sport"], {"status_label": "déposé"})
    h1, t1 = build_html([row_dict], "https://veille.sideline-conseil.fr/")
    assert t1 == 1
    # Format legacy (string)
    row_str = _make_row_legacy('["sport"]', '{"status_label":"déposé"}')
    h2, t2 = build_html([row_str], "https://veille.sideline-conseil.fr/")
    assert t2 == 1


def test_r43sb_build_html_matched_keywords_vide_ignore_item():
    """Cohérence avec le comportement legacy : matched_keywords=[] →
    item exclu du digest. Vrai pour list vide ET pour None."""
    from src.digest import build_html

    # list vide
    rows1 = [_make_row_parsed([], {})]
    _, t1 = build_html(rows1, "https://veille.sideline-conseil.fr/")
    assert t1 == 0
    # None / clé absente
    row_no_mk = _make_row_legacy("[]", "{}")
    del row_no_mk["matched_keywords"]
    _, t2 = build_html([row_no_mk], "https://veille.sideline-conseil.fr/")
    assert t2 == 0
