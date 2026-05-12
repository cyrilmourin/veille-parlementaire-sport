"""R42-BP — Router scope du predicate `min_sports_*`.

Avant R42-BP, la règle du ROUTER `src.normalize` envoyait TOUT format
commençant par `min_sports_` à `min_sports.fetch_source`, qui ne gère
en réalité que `min_sports_agenda_hebdo`. Conséquence : la source
`min_sports_igesr` (format `min_sports_igesr_html`) tombait dans la
branche WARNING « format non géré » et renvoyait 0 items, alors qu'un
handler dédié existe pourtant dans `html_generic.py`.

Fix : restreindre le predicate à `min_sports_agenda_`.
"""
from __future__ import annotations

from src.normalize import _dispatch
from src.sources import html_generic, min_sports


def test_min_sports_agenda_routed_to_min_sports():
    """L'agenda hebdo reste routé vers min_sports.fetch_source."""
    src = {
        "id": "min_sports_agenda_ministre",
        "format": "min_sports_agenda_hebdo",
        "url": "https://www.sports.gouv.fr/",
        "category": "agendas",
    }
    assert _dispatch("ministeres", src) is min_sports.fetch_source


def test_min_sports_igesr_html_routed_to_html_generic():
    """`min_sports_igesr_html` doit aller à html_generic (où le handler
    `_from_min_sports_igesr_html` est implémenté), pas à min_sports."""
    src = {
        "id": "min_sports_igesr",
        "format": "min_sports_igesr_html",
        "url": "https://www.sports.gouv.fr/rapports-de-l-igesr-...",
        "category": "rapports_operateurs",
    }
    assert _dispatch("ministeres", src) is html_generic.fetch_source


def test_other_min_sports_formats_fall_through_to_html_generic():
    """Tout futur format `min_sports_xxx_html` (non agenda) doit aller à
    html_generic par défaut, où on peut ajouter un handler dédié sans
    toucher au router."""
    src = {
        "id": "min_sports_other",
        "format": "min_sports_publications_html",
        "url": "https://www.sports.gouv.fr/publications",
        "category": "communiques",
    }
    assert _dispatch("ministeres", src) is html_generic.fetch_source
