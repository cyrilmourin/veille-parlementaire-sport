"""Tests R27 — bypass matcher pour items rattachés à un organe sport/JOP.

Couvre :
- `assemblee_organes.is_sport_relevant_organe` (whitelist lookup)
- `main._apply_organe_bypass` (injection pseudo-keyword, cas no-op)
- Interaction avec `_apply_source_bypass` (pas de double-count)
- Items `an_agenda` avec `raw.organe` peuplé
- Tolérance sur raw=None / raw non-dict / organe vide
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.assemblee_organes import (
    BYPASS_ORGANE_LABEL,
    SPORT_RELEVANT_ORGANES,
    is_sport_relevant_organe,
)
from src.main import _apply_organe_bypass, _apply_source_bypass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(
    *,
    source_id: str = "an_agenda",
    category: str = "agenda",
    matched: list[str] | None = None,
    raw: dict | None = None,
    title: str = "T",
) -> SimpleNamespace:
    """Fabrique un item minimal avec l'API attendue par le bypass.

    R39-J (2026-04-25) : default category passé de 'reunions_agenda' à
    'agenda' pour refléter la catégorie publique utilisée en prod et
    permettre au bypass R27 (restreint à 'agenda' depuis R39-J) de
    s'appliquer dans les tests qui le ciblent.
    """
    return SimpleNamespace(
        source_id=source_id,
        category=category,
        chamber="AN",
        title=title,
        matched_keywords=matched or [],
        keyword_families=[],
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Whitelist / helper
# ---------------------------------------------------------------------------

def test_whitelist_contains_expected_commissions_permanentes():
    """Commission affaires culturelles/éducation AN dans la whitelist.

    R35-D (2026-04-24) — Les commissions « Affaires sociales » (AN
    PO420120 et Sénat PO211493) ont été retirées : elles traitent
    majoritairement retraites/santé/assurance, et le bypass générait
    >90% de bruit off-topic. Les réunions sport continuent à remonter
    via matching keyword standard (« dopage », « ANS », « JO »...).
    """
    assert "PO419604" in SPORT_RELEVANT_ORGANES  # Affaires culturelles AN
    # Régression R35-D : ne PAS réintroduire sans revoir le filtrage
    assert "PO420120" not in SPORT_RELEVANT_ORGANES  # Affaires sociales AN
    assert "PO211493" not in SPORT_RELEVANT_ORGANES  # Affaires sociales Sénat


def test_whitelist_contains_missions_info_jop():
    """Les 4 MI + CE Fédérations doivent être dans la liste."""
    expected = {"PO804929", "PO825884", "PO806169", "PO695919", "PO825320"}
    assert expected.issubset(SPORT_RELEVANT_ORGANES)


def test_whitelist_contains_groupes_etudes():
    """GE sport / économie / dopage."""
    expected = {"PO285103", "PO746821", "PO402925"}
    assert expected.issubset(SPORT_RELEVANT_ORGANES)


def test_whitelist_size_sanity():
    """10 codes PO documentés (R35-D : -2 affaires sociales).

    Seuil large — si on en ajoute/retire, bumper ce seuil.
    """
    assert 8 <= len(SPORT_RELEVANT_ORGANES) <= 30


def test_is_sport_relevant_organe_true_cases():
    assert is_sport_relevant_organe("PO419604") is True
    assert is_sport_relevant_organe("PO825320") is True
    # Tolère les espaces autour
    assert is_sport_relevant_organe("  PO825884  ") is True


def test_is_sport_relevant_organe_false_cases():
    # Code inconnu
    assert is_sport_relevant_organe("PO000000") is False
    # Chaîne vide
    assert is_sport_relevant_organe("") is False
    # None
    assert is_sport_relevant_organe(None) is False
    # Casse différente : case-sensitive exprès
    assert is_sport_relevant_organe("po419604") is False


# ---------------------------------------------------------------------------
# _apply_organe_bypass
# ---------------------------------------------------------------------------

def test_apply_organe_bypass_disabled_no_op():
    """R39-K (2026-04-25) — le bypass organe est DÉSACTIVÉ. Le test
    couvre désormais le no-op : même un item dans la whitelist organe
    n'est pas enrichi ; il faut un vrai match keyword pour remonter.
    """
    items = [_item(raw={"path": "assemblee:reunion", "organe": "PO419604"})]
    n = _apply_organe_bypass(items)
    assert n == 0
    assert items[0].matched_keywords == []
    assert items[0].keyword_families == []


def test_apply_organe_bypass_no_op_when_already_matched():
    """Un item déjà matché par un keyword métier ne doit PAS être écrasé."""
    items = [
        _item(
            matched=["Sport", "Dopage"],
            raw={"organe": "PO419604"},
        )
    ]
    n = _apply_organe_bypass(items)
    assert n == 0
    assert items[0].matched_keywords == ["Sport", "Dopage"]


def test_apply_organe_bypass_skips_unknown_organe():
    items = [_item(raw={"organe": "PO999999"})]
    n = _apply_organe_bypass(items)
    assert n == 0
    assert items[0].matched_keywords == []


def test_apply_organe_bypass_skips_missing_organe():
    """raw sans clé organe → no-op."""
    items = [_item(raw={"path": "assemblee:reunion"})]
    n = _apply_organe_bypass(items)
    assert n == 0
    assert items[0].matched_keywords == []


def test_apply_organe_bypass_tolerates_raw_none():
    items = [_item(raw=None)]
    n = _apply_organe_bypass(items)
    assert n == 0


def test_apply_organe_bypass_tolerates_raw_non_dict():
    """Certains items legacy peuvent avoir raw=list ou str."""
    items = [_item(raw=["foo", "bar"]), _item(raw="PO419604")]
    n = _apply_organe_bypass(items)
    assert n == 0


def test_apply_organe_bypass_empty_items_list():
    assert _apply_organe_bypass([]) == 0


def test_apply_organe_bypass_disabled_for_all_categories():
    """R39-K (2026-04-25) — le bypass est désactivé partout (CR ET
    agenda). Cyril veut un match keyword explicite quelle que soit
    la catégorie."""
    items = [
        _item(category="comptes_rendus", raw={"organe": "PO419604"}),
        _item(category="comptes_rendus", raw={"organe": "PO825884"}),
        _item(category="agenda", raw={"organe": "PO419604"}),
        _item(category="agenda", raw={"organe": "PO825884"}),
    ]
    n = _apply_organe_bypass(items)
    assert n == 0
    for it in items:
        assert it.matched_keywords == []


def test_apply_organe_bypass_multiple_items_mixed_disabled():
    """R39-K : bypass désactivé. Aucun item de la whitelist n'est
    enrichi. Seul l'item avec un match keyword explicite garde sa
    valeur (préservée — la fonction n'écrase pas les matches).
    """
    items = [
        _item(raw={"organe": "PO419604"}),            # culture/éducation
        _item(raw={"organe": "PO420120"}),            # affaires sociales AN
        _item(raw={"organe": "PO444444"}),            # non-sport
        _item(
            matched=["Sport"],
            raw={"organe": "PO419604"},
        ),                                             # match préservé
        _item(raw={"organe": "PO825884"}),            # MI femmes et sport
    ]
    n = _apply_organe_bypass(items)
    assert n == 0
    assert items[0].matched_keywords == []
    assert items[1].matched_keywords == []
    assert items[2].matched_keywords == []
    assert items[3].matched_keywords == ["Sport"]
    assert items[4].matched_keywords == []


# ---------------------------------------------------------------------------
# Interaction avec _apply_source_bypass (R25-H)
# ---------------------------------------------------------------------------

def test_source_bypass_and_organe_bypass_do_not_double_count():
    """Un item matché par source_bypass ne doit PAS être retouché par organe_bypass."""
    # Item ANS (bypass source) avec aussi un organe sport fictif (ne devrait
    # jamais arriver en vrai : ANS n'a pas de champ organe AN, mais on teste
    # la robustesse).
    items = [
        _item(
            source_id="ans",
            category="communiques",
            raw={"organe": "PO419604"},
        )
    ]
    n_source = _apply_source_bypass(items)
    assert n_source == 1
    assert items[0].matched_keywords == ["(flux complet)"]
    # L'appel organe_bypass ensuite ne doit PAS écraser
    n_organe = _apply_organe_bypass(items)
    assert n_organe == 0
    assert items[0].matched_keywords == ["(flux complet)"]


def test_organe_bypass_label_distinct_from_source_bypass_label():
    """Les deux labels doivent être différents pour distinguer l'origine côté site."""
    assert BYPASS_ORGANE_LABEL != "(flux complet)"
    assert "organe" in BYPASS_ORGANE_LABEL.lower()
