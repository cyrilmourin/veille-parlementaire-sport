"""Tests R43-X (2026-05-19) — Bypass keyword pour les amendements d'un
dossier législatif identifié sport.

Constat Cyril 19/05 : « Je n'ai pas les amendements du dernier dossier
législatif sur les équipements, alors que j'en voie 14 en commission ».
La page AN du dossier DLR5L17N54138 (PPL équipements n°2667) montre
`amendement-count="14"` mais 0 amdt en DB → cause : keyword matcher
rejette les amdt techniques (« Suppression de l'article 5 », « Modifier
l'alinéa 3 ») qui n'ont aucun mot-clé sport dans titre/summary, alors
que leur dossier parent EST sport.

Fix : bypass dosleg pour les amdt seulement. Si `raw.texte_ref` ou
`raw.dossier_id` est dans la whitelist `SPORT_DOSLEG_TEXTE_REFS` (union
des `ALL_TEXTE_REFS` des modules `special_*`), on injecte le
pseudo-keyword `(dossier sport)` pour passer le filtre matched_keywords.

Garde-fous :
- Scope strict catégorie `amendements` UNIQUEMENT (pas CR/agenda/etc.)
- Pas de touche aux items déjà matchés par le matcher standard
- No-op si `SPORT_DOSLEG_TEXTE_REFS` est vide (safe)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class _FakeItem:
    """Stub Item pour les tests, minimal."""
    source_id: str = "test"
    uid: str = "u1"
    category: str = "amendements"
    title: str = "Test"
    url: str = "https://example.com/a"
    raw: dict = field(default_factory=dict)
    matched_keywords: list = field(default_factory=list)
    keyword_families: list = field(default_factory=list)


def test_r43x_sport_dosleg_texte_refs_non_vide():
    """Garde-fou contrat : la whitelist `SPORT_DOSLEG_TEXTE_REFS` doit
    contenir au moins les texte_refs des PPL Sport pro + équipements
    actuellement déclarés en `special_*`."""
    from src.main import SPORT_DOSLEG_TEXTE_REFS

    assert "PIONANR5L17B1560" in SPORT_DOSLEG_TEXTE_REFS, (
        "PPL Sport pro (1560) doit être dans la whitelist"
    )
    assert "PIONANR5L17B2667" in SPORT_DOSLEG_TEXTE_REFS, (
        "PPL équipements (2667) doit être dans la whitelist — c'est le cas "
        "concret motivant R43-X (constat Cyril 19/05)"
    )


def test_r43x_amdt_avec_texte_ref_dans_whitelist_bypass():
    """Amendement avec `raw.texte_ref` dans la whitelist sport → enrichi
    avec `(dossier sport)`. Cas concret R43-X."""
    from src.main import _apply_dosleg_amdt_bypass

    items = [_FakeItem(
        category="amendements",
        title="Suppression de l'article 5",
        raw={"texte_ref": "PIONANR5L17B2667"},
    )]
    n = _apply_dosleg_amdt_bypass(items)
    assert n == 1
    assert items[0].matched_keywords == ["(dossier sport)"]


def test_r43x_amdt_avec_dossier_id_dans_whitelist_bypass():
    """Amendement avec `raw.dossier_id` dans la whitelist sport → enrichi.
    Couvre la convention Sénat où le champ s'appelle dossier_id."""
    from src.main import _apply_dosleg_amdt_bypass

    items = [_FakeItem(
        category="amendements",
        title="Amdt n°COM-71",
        raw={"dossier_id": "PIONANR5L17B1560"},
    )]
    n = _apply_dosleg_amdt_bypass(items)
    assert n == 1
    assert items[0].matched_keywords == ["(dossier sport)"]


def test_r43x_amdt_deja_matched_pas_touche():
    """Si l'amdt est déjà matché par le keyword matcher standard, on
    ne le touche pas (garde son matched_keywords original)."""
    from src.main import _apply_dosleg_amdt_bypass

    items = [_FakeItem(
        category="amendements",
        title="Amdt sur le sport",
        raw={"texte_ref": "PIONANR5L17B2667"},
        matched_keywords=["sport", "football"],
    )]
    n = _apply_dosleg_amdt_bypass(items)
    assert n == 0
    assert items[0].matched_keywords == ["sport", "football"]


def test_r43x_scope_strict_amendements_pas_dautres_categories():
    """Scope strict : un CR / agenda / publication même avec un
    `raw.texte_ref` sport n'est PAS bypass. Le bypass dosleg cible
    UNIQUEMENT les amdt.
    """
    from src.main import _apply_dosleg_amdt_bypass

    categories_non_amdt = [
        "comptes_rendus", "agenda", "questions", "communiques",
        "jorf", "nominations", "dossiers_legislatifs",
    ]
    items = [
        _FakeItem(
            category=cat,
            title=f"item {cat}",
            raw={"texte_ref": "PIONANR5L17B2667"},
        )
        for cat in categories_non_amdt
    ]
    n = _apply_dosleg_amdt_bypass(items)
    assert n == 0, (
        f"Bypass dosleg doit être strict amdt-only. Items enrichis "
        f"abusivement : {[it.category for it in items if it.matched_keywords]}"
    )
    for it in items:
        assert it.matched_keywords == [], (
            f"Item {it.category} ne doit PAS être enrichi"
        )


def test_r43x_amdt_hors_whitelist_pas_bypass():
    """Amendement avec `raw.texte_ref` HORS whitelist sport (ex. PJL
    Calédonie 2529) → pas de bypass."""
    from src.main import _apply_dosleg_amdt_bypass

    items = [_FakeItem(
        category="amendements",
        title="Amdt PJL Nouvelle-Calédonie",
        raw={"texte_ref": "PRJLANR5L17B2529"},
    )]
    n = _apply_dosleg_amdt_bypass(items)
    assert n == 0
    assert items[0].matched_keywords == []


def test_r43x_amdt_raw_vide_safe():
    """Amdt sans `raw` ou raw vide → no-op, pas de crash."""
    from src.main import _apply_dosleg_amdt_bypass

    items = [
        _FakeItem(category="amendements", raw={}),
        _FakeItem(category="amendements", raw=None),  # type: ignore
        _FakeItem(category="amendements", raw={"texte_ref": ""}),
    ]
    n = _apply_dosleg_amdt_bypass(items)
    assert n == 0


def test_r43x_idempotent():
    """Appeler le bypass 2× sur les mêmes items → 2e appel est no-op
    (les items sont déjà matched par le 1er passage)."""
    from src.main import _apply_dosleg_amdt_bypass

    items = [_FakeItem(
        category="amendements",
        raw={"texte_ref": "PIONANR5L17B2667"},
    )]
    n1 = _apply_dosleg_amdt_bypass(items)
    n2 = _apply_dosleg_amdt_bypass(items)
    assert n1 == 1
    assert n2 == 0  # déjà matched, no-op
    assert items[0].matched_keywords == ["(dossier sport)"]
