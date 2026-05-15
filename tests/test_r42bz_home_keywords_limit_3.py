"""R42-BZ (2026-05-15) — Limite à 3 keywords sur les items rendus par
`_fmt_item_line`, utilisée par les <details> dépliables de l'accueil.

R42-BN avait posé la limite côté templates Hugo (pages catégorie, agenda,
dossiers législatifs, recherche). Mais la page d'accueil n'est PAS rendue
par un template Hugo : son contenu central est produit en Markdown par
`src/site_export.py` puis injecté dans `.Content`. Les items des
dropdowns thématiques de l'accueil sortaient donc avec jusqu'à 12 tags.

Ce test garantit que `_fmt_item_line` n'émet pas plus de 3 `<span
class="kw-tag">` quel que soit le nombre de mots-clés en entrée.
"""
from __future__ import annotations

from src.site_export import _fmt_item_line


def _row(keywords: list[str]) -> dict:
    return {
        "title": "PJL Sport pro",
        "url": "https://example.test/item",
        "chamber": "AN",
        "published_at": "2026-05-15T10:00:00",
        "matched_keywords": keywords,
        "keyword_families": ["acteur"],
        "raw": {},
    }


def test_fmt_item_line_limite_keywords_a_3():
    """Plus de 3 mots-clés en entrée → seuls les 3 premiers sont rendus."""
    kws = ["sport", "olympique", "JO 2030", "ANS", "Pass'Sport", "fédération"]
    line = _fmt_item_line(_row(kws))
    assert line.count('class="kw-tag"') == 3
    # Les 3 premiers présents, les suivants absents
    assert "sport" in line
    assert "olympique" in line
    assert "JO 2030" in line
    assert "ANS" not in line
    assert "Pass'Sport" not in line
    assert "fédération" not in line


def test_fmt_item_line_moins_de_3_keywords_inchange():
    """≤ 3 mots-clés en entrée → tous rendus."""
    line = _fmt_item_line(_row(["sport", "olympique"]))
    assert line.count('class="kw-tag"') == 2


def test_fmt_item_line_exactement_3_keywords():
    line = _fmt_item_line(_row(["sport", "olympique", "JO 2030"]))
    assert line.count('class="kw-tag"') == 3


def test_fmt_item_line_with_tags_false_aucun_tag():
    """`with_tags=False` court-circuite : 0 kw-tag rendu."""
    kws = ["sport", "olympique", "JO 2030", "ANS"]
    line = _fmt_item_line(_row(kws), with_tags=False)
    assert "kw-tag" not in line
