"""R42-BU — Blocklist 2 dossiers AN + chamber mutable + article premier.

Cyril 2026-05-13 :
1. Blocklister 2 dossiers AN (DLR5L17N51939 Football, DLR5L17N52055
   Associations sportives) — hors scope sport-institutionnel.
2. Le cartouche IGESR reste affiché « MinSports » sur les cards bien
   qu'on ait basculé chamber=IGESR en R42-BS — cause racine : l'upsert
   `store.upsert_many` traitait `chamber` en immuable, donc les ~28
   rapports IGESR déjà en DB conservaient leur ancien chamber. On le
   passe en mutable (réécrit si nouveau non-vide).
3. « Article premier » et « Article 1er » doivent être regroupés pour
   le tri. Normalisation au parse → « 1ER ».
"""
from __future__ import annotations

from src.sources.assemblee import _normalize_article_designation


# ---------------------------------------------------------------------------
# 1. Blocklist : présence des 2 dossiers
# ---------------------------------------------------------------------------

def test_blocklist_contains_dossier_football():
    """Le dossier DLR5L17N51939 (Football) doit être en blocklist."""
    import yaml
    with open("config/blocklist.yml", "r", encoding="utf-8") as fp:
        cfg = yaml.safe_load(fp) or {}
    urls = {
        e.get("url") for e in cfg.get("blocklist", [])
        if isinstance(e, dict) and e.get("url")
    }
    assert "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N51939" in urls


def test_blocklist_contains_dossier_associations_sportives():
    import yaml
    with open("config/blocklist.yml", "r", encoding="utf-8") as fp:
        cfg = yaml.safe_load(fp) or {}
    urls = {
        e.get("url") for e in cfg.get("blocklist", [])
        if isinstance(e, dict) and e.get("url")
    }
    assert "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N52055" in urls


# ---------------------------------------------------------------------------
# 2. Chamber mutable côté store.upsert_many
# ---------------------------------------------------------------------------

def test_upsert_refreshes_chamber(tmp_path):
    """Si on ré-upserte un item avec un nouveau chamber non-vide, la DB
    doit refléter la mise à jour (au lieu de garder l'ancien)."""
    from src.store import Store
    from src.models import Item

    db_path = tmp_path / "test.sqlite3"
    store = Store(str(db_path))

    it1 = Item(
        source_id="min_sports_igesr",
        uid="https://example.com/rapport-igesr-001.pdf",
        category="communiques",
        chamber="MinSports",  # ancien chamber, avant R42-BS
        title="Rapport IGESR n°2026-001 sport-santé",
        url="https://example.com/rapport-igesr-001.pdf",
        summary="",
        raw={"path": "min_sports_igesr_html"},
    )
    store.upsert_many([it1])

    # Re-upsert avec chamber=IGESR (après R42-BS YAML+handler)
    it2 = Item(
        source_id="min_sports_igesr",
        uid="https://example.com/rapport-igesr-001.pdf",
        category="communiques",
        chamber="IGESR",
        title="Rapport IGESR n°2026-001 sport-santé",
        url="https://example.com/rapport-igesr-001.pdf",
        summary="",
        raw={"path": "min_sports_igesr_html"},
    )
    store.upsert_many([it2])

    cur = store.conn.cursor()
    cur.execute(
        "SELECT chamber FROM items WHERE uid = ?",
        ("https://example.com/rapport-igesr-001.pdf",),
    )
    row = cur.fetchone()
    assert row[0] == "IGESR", (
        f"chamber non-mis à jour : attendu IGESR, obtenu {row[0]!r}"
    )


def test_upsert_keeps_chamber_when_new_is_empty(tmp_path):
    """Si le nouvel item arrive avec chamber=None ou '', on garde
    l'existant (pas de régression)."""
    from src.store import Store
    from src.models import Item

    db_path = tmp_path / "test.sqlite3"
    store = Store(str(db_path))

    it1 = Item(
        source_id="src1",
        uid="uid1",
        category="communiques",
        chamber="MinSports",
        title="Item 1",
        url="https://example.com/1",
        summary="",
        raw={},
    )
    store.upsert_many([it1])

    it2 = Item(
        source_id="src1",
        uid="uid1",
        category="communiques",
        chamber="",  # vide
        title="Item 1",
        url="https://example.com/1",
        summary="",
        raw={},
    )
    store.upsert_many([it2])

    cur = store.conn.cursor()
    cur.execute("SELECT chamber FROM items WHERE uid = ?", ("uid1",))
    row = cur.fetchone()
    assert row[0] == "MinSports", "chamber écrasé par valeur vide (régression)"


# ---------------------------------------------------------------------------
# 3. Normalisation article premier / 1er / 1
# ---------------------------------------------------------------------------

def test_article_premier_to_1er():
    assert _normalize_article_designation("premier") == "1ER"
    assert _normalize_article_designation("PREMIER") == "1ER"
    assert _normalize_article_designation("Premier") == "1ER"


def test_article_1er_to_1er():
    assert _normalize_article_designation("1er") == "1ER"
    assert _normalize_article_designation("1ER") == "1ER"
    assert _normalize_article_designation("1 er") == "1ER"


def test_article_1_alone_to_1er():
    """L'AN expose parfois juste « 1 » (sans suffixe). Normalisation."""
    assert _normalize_article_designation("1") == "1ER"


def test_article_with_prefix_preserves_prefix():
    """« ARTICLE PREMIER » → « ARTICLE 1ER » (préfixe préservé en upper)."""
    assert _normalize_article_designation("Article premier") == "ARTICLE 1ER"
    assert _normalize_article_designation("ARTICLE PREMIER") == "ARTICLE 1ER"
    assert _normalize_article_designation("article 1er") == "ARTICLE 1ER"


def test_article_premier_bis_normalized():
    """« 1er bis » → « 1ER bis » (préserve le suffixe)."""
    assert _normalize_article_designation("1er bis") == "1ER bis"
    assert _normalize_article_designation("premier A") == "1ER A"


def test_article_others_unchanged():
    """Les autres articles passent inchangés."""
    assert _normalize_article_designation("2") == "2"
    assert _normalize_article_designation("3 bis") == "3 bis"
    assert _normalize_article_designation("4 quater") == "4 quater"
    assert _normalize_article_designation("10") == "10"


def test_article_empty_returns_empty():
    assert _normalize_article_designation("") == ""
    assert _normalize_article_designation(None) is None


def test_article_premier_and_1er_compare_equal_post_normalize():
    """Le but R42-BU : après normalisation, les 2 formes sont identiques."""
    a = _normalize_article_designation("premier")
    b = _normalize_article_designation("1er")
    c = _normalize_article_designation("1")
    d = _normalize_article_designation("PREMIER")
    assert a == b == c == d == "1ER"
