"""R41-AR (2026-05-10) — Test blocklist : champ `recategorize_to`.

Demande Cyril : un décret JORF matchait `nomination_event` et était routé
en catégorie `nominations` (R41-A), mais c'est un décret hors champ
sport-institutionnel pertinent uniquement pour la page JORF. Au lieu de
le drop dur (perte d'info), on le recatégorise vers `jorf` pour qu'il
reste visible dans /items/jorf/.

Mécanisme : entrée `blocklist:` étendue avec champ `recategorize_to`
optionnel + champ `from_category` optionnel pour conditionner le reroute
sur la catégorie courante.

    blocklist:
      - url: https://...
        recategorize_to: jorf
        from_category: nominations

Tests :
1. _load_blocklist : retourne 4 containers, dont 2 dicts pour la
   recategorize.
2. _apply_blocklist_recategorize : item match → category mise à jour.
3. _apply_blocklist_recategorize : sans `from_category`, recat
   inconditionnel.
4. _apply_blocklist_recategorize : `from_category` ne match pas → no-op.
5. _filter_blocklist : entrée recat n'est PAS drop (garde l'item).
6. Idempotence : 2 passages laissent le résultat identique.
"""
from __future__ import annotations

import pytest

import src.site_export as se


@pytest.fixture
def blocklist_with_recat(tmp_path, monkeypatch):
    p = tmp_path / "blocklist.yml"
    p.write_text(
        "blocklist:\n"
        "  - url: https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000054027159\n"
        "    recategorize_to: jorf\n"
        "    from_category: nominations\n"
        "    reason: garder dans JORF, pas dans nominations\n"
        "  - url: https://example.com/drop-me\n"
        "    reason: drop dur classique\n"
        "  - uid: dila_jorf::JORFTEXT_recat_uid_test\n"
        "    recategorize_to: jorf\n"
        "    reason: recat inconditionnel via UID\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", p)
    return p


# ---------------------------------------------------------------------------
# 1. _load_blocklist retourne bien 4 containers
# ---------------------------------------------------------------------------

def test_load_blocklist_returns_4_containers(blocklist_with_recat):
    blocked_urls, blocked_uids, recat_urls, recat_uids = se._load_blocklist()
    # Drop dur : seulement l'URL example.com (l'autre URL JORF a recat,
    # donc PAS dans blocked_urls)
    assert "example.com/drop-me" in blocked_urls
    assert ("www.legifrance.gouv.fr/jorf/id/jorftext000054027159"
            not in blocked_urls)
    # Recat : l'URL JORF + l'UID dila_jorf::*
    assert ("www.legifrance.gouv.fr/jorf/id/jorftext000054027159"
            in recat_urls)
    assert recat_urls["www.legifrance.gouv.fr/jorf/id/jorftext000054027159"] == (
        "nominations", "jorf",
    )
    assert "dila_jorf::JORFTEXT_recat_uid_test" in recat_uids
    assert recat_uids["dila_jorf::JORFTEXT_recat_uid_test"] == ("", "jorf")
    assert blocked_uids == set()


# ---------------------------------------------------------------------------
# 2. _filter_blocklist ne drop PAS les entrées recat
# ---------------------------------------------------------------------------

def test_filter_blocklist_keeps_items_with_recat(blocklist_with_recat):
    rows = [
        {
            "url": "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000054027159",
            "category": "nominations",
            "source_id": "dila_jorf",
            "uid": "JORFTEXT000054027159",
        },
        {
            "url": "https://example.com/drop-me",
            "category": "communiques",
        },
        {
            "url": "https://example.com/keep-me",
            "category": "communiques",
        },
    ]
    kept = se._filter_blocklist(rows)
    # Drop dur effectif sur example.com/drop-me
    urls_kept = [r["url"] for r in kept]
    assert "https://example.com/drop-me" not in urls_kept
    # JORF avec recat est CONSERVÉ par _filter_blocklist (recat appliqué
    # plus tard par _apply_blocklist_recategorize).
    assert ("https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000054027159"
            in urls_kept)
    assert "https://example.com/keep-me" in urls_kept


# ---------------------------------------------------------------------------
# 3. _apply_blocklist_recategorize : URL recat avec from_category
# ---------------------------------------------------------------------------

def test_apply_recategorize_url_with_from_category_match(blocklist_with_recat):
    rows = [
        {
            "url": "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000054027159",
            "category": "nominations",
        },
    ]
    out = se._apply_blocklist_recategorize(rows)
    assert out[0]["category"] == "jorf"


def test_apply_recategorize_url_with_from_category_mismatch(blocklist_with_recat):
    """Si la category courante != from_category, on ne touche pas."""
    rows = [
        {
            "url": "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000054027159",
            "category": "communiques",  # pas nominations !
        },
    ]
    out = se._apply_blocklist_recategorize(rows)
    assert out[0]["category"] == "communiques"


# ---------------------------------------------------------------------------
# 4. _apply_blocklist_recategorize : UID sans from_category (inconditionnel)
# ---------------------------------------------------------------------------

def test_apply_recategorize_uid_unconditional(blocklist_with_recat):
    rows = [
        {
            "url": "https://x.example/whatever",
            "category": "communiques",
            "source_id": "dila_jorf",
            "uid": "JORFTEXT_recat_uid_test",
        },
    ]
    out = se._apply_blocklist_recategorize(rows)
    assert out[0]["category"] == "jorf"


# ---------------------------------------------------------------------------
# 5. Idempotence
# ---------------------------------------------------------------------------

def test_apply_recategorize_idempotent(blocklist_with_recat):
    rows = [
        {
            "url": "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000054027159",
            "category": "nominations",
        },
    ]
    out1 = se._apply_blocklist_recategorize(rows)
    out2 = se._apply_blocklist_recategorize(out1)
    assert out1[0]["category"] == out2[0]["category"] == "jorf"


# ---------------------------------------------------------------------------
# 6. No-op si pas d'entrées recat dans le YAML
# ---------------------------------------------------------------------------

def test_apply_recategorize_noop_without_recat_entries(tmp_path, monkeypatch):
    p = tmp_path / "blocklist.yml"
    p.write_text(
        "blocklist:\n"
        "  - url: https://example.com/drop\n"
        "    reason: pure drop\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", p)
    rows = [
        {"url": "https://other.example/", "category": "nominations"},
    ]
    out = se._apply_blocklist_recategorize(rows)
    assert out[0]["category"] == "nominations"
