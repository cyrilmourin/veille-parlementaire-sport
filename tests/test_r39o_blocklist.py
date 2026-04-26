"""R39-O (2026-04-26) — Liste rouge d'items à exclure du site (faux positifs
keyword non corrigeables via le dictionnaire sans casser d'autres items
légitimes). Cf. `config/blocklist.yml` et `src/site_export._filter_blocklist`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src import site_export as se


def _row(url: str = "", sid: str = "an_amendements", uid: str = "x", **extra) -> dict:
    return {"source_id": sid, "uid": uid, "url": url, **extra}


# ---------------------------------------------------------------------------
# _canon_block_url — canonicalisation pour matching
# ---------------------------------------------------------------------------

def test_canon_block_url_strips_scheme_and_lowercases():
    a = se._canon_block_url("https://www.Example.COM/path/")
    b = se._canon_block_url("http://www.example.com/path")
    assert a == b
    assert a == "www.example.com/path"


def test_canon_block_url_strips_fragment():
    assert se._canon_block_url("https://x.fr/a#frag") == "x.fr/a"


def test_canon_block_url_handles_empty_and_none():
    assert se._canon_block_url("") == ""
    assert se._canon_block_url("   ") == ""
    assert se._canon_block_url(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _load_blocklist — robustesse aux YAML cassés / absents
# ---------------------------------------------------------------------------

def test_load_blocklist_returns_empty_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", tmp_path / "missing.yml")
    urls, uids = se._load_blocklist()
    assert urls == set()
    assert uids == set()


def test_load_blocklist_tolerates_malformed_yaml(tmp_path, monkeypatch):
    p = tmp_path / "bad.yml"
    p.write_text("not: valid: yaml: at all:::", encoding="utf-8")
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", p)
    urls, uids = se._load_blocklist()
    assert urls == set()
    assert uids == set()


def test_load_blocklist_tolerates_missing_blocklist_key(tmp_path, monkeypatch):
    p = tmp_path / "noroot.yml"
    p.write_text("other_key:\n  - foo\n", encoding="utf-8")
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", p)
    urls, uids = se._load_blocklist()
    assert urls == set()
    assert uids == set()


def test_load_blocklist_parses_url_and_uid_entries(tmp_path, monkeypatch):
    p = tmp_path / "ok.yml"
    p.write_text(
        "blocklist:\n"
        "  - url: https://example.com/A\n"
        "    reason: faux positif\n"
        "  - uid: foo_src::bar123\n"
        "    reason: legacy\n"
        "  - url: \n"  # empty url ignored
        "    reason: ignored\n"
        "  - random_key: ignored\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", p)
    urls, uids = se._load_blocklist()
    assert urls == {"example.com/a"}
    assert uids == {"foo_src::bar123"}


# ---------------------------------------------------------------------------
# _filter_blocklist — sémantique du filtre
# ---------------------------------------------------------------------------

def test_filter_blocklist_no_op_when_yaml_empty(tmp_path, monkeypatch):
    p = tmp_path / "empty.yml"
    p.write_text("blocklist: []\n", encoding="utf-8")
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", p)
    rows = [_row(url="https://an.fr/a"), _row(url="https://an.fr/b")]
    out = se._filter_blocklist(rows)
    assert len(out) == 2


def test_filter_blocklist_drops_row_by_url(tmp_path, monkeypatch):
    """Note R40-D : on utilise une URL non-navigable (pattern Sénat,
    catégorie dossier ou format technique AN). Le pattern URL navigable
    AN amendements `…/amendements/<n>/CION-XXX/CD<n>` est désormais
    skip + warn par `_load_blocklist` (cf. tests R40-D dédiés)."""
    p = tmp_path / "yml.yml"
    p.write_text(
        "blocklist:\n"
        "  - url: https://example.test/blocked-item-fixture\n"
        "    reason: Faux positif\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", p)
    rows = [
        _row(url="https://example.test/blocked-item-fixture"),
        _row(url="https://example.test/other-item-fixture"),
    ]
    out = se._filter_blocklist(rows)
    assert len(out) == 1
    assert "other-item-fixture" in out[0]["url"]


def test_filter_blocklist_url_match_is_scheme_insensitive(tmp_path, monkeypatch):
    p = tmp_path / "yml.yml"
    p.write_text(
        "blocklist:\n"
        "  - url: https://example.test/blocked-item-fixture\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", p)
    rows = [
        # Variante http:// + trailing slash + casse différente
        _row(url="HTTP://example.test/blocked-item-fixture/"),
    ]
    out = se._filter_blocklist(rows)
    assert out == []


def test_filter_blocklist_drops_row_by_uid(tmp_path, monkeypatch):
    p = tmp_path / "yml.yml"
    p.write_text(
        "blocklist:\n"
        "  - uid: an_amendements::ABC123\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", p)
    rows = [
        _row(sid="an_amendements", uid="ABC123", url="https://an.fr/x"),
        _row(sid="an_amendements", uid="OTHER", url="https://an.fr/y"),
    ]
    out = se._filter_blocklist(rows)
    assert len(out) == 1
    assert out[0]["uid"] == "OTHER"


def test_filter_blocklist_idempotent(tmp_path, monkeypatch):
    p = tmp_path / "yml.yml"
    p.write_text(
        "blocklist:\n"
        "  - url: https://an.fr/a\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(se, "_BLOCKLIST_PATH", p)
    rows = [_row(url="https://an.fr/a"), _row(url="https://an.fr/b")]
    once = se._filter_blocklist(rows)
    twice = se._filter_blocklist(once)
    assert len(once) == len(twice) == 1


def test_filter_blocklist_real_yaml_blocks_two_amendements():
    """Sanity check sur le vrai fichier `config/blocklist.yml` versionné :
    les deux amendements PJL agricoles n°2632 sont filtrés via leur UID
    technique. R40-D (2026-04-26) : avant, ce test passait avec l'URL
    navigable parce que le filtre URL était silencieusement no-op
    (cf. test_r40d_blocklist_an_amdt_navigable.py). Maintenant on
    matche par UID, ce qui correspond effectivement au comportement prod.
    """
    uid_cd495 = "AMANR5L17PO419865B2632P0D1N000495"
    uid_cd492 = "AMANR5L17PO419865B2632P0D1N000492"
    rows = [
        _row(
            sid="an_amendements",
            uid=uid_cd495,
            url=f"https://www.assemblee-nationale.fr/dyn/17/amendements/{uid_cd495}",
        ),
        _row(
            sid="an_amendements",
            uid=uid_cd492,
            url=f"https://www.assemblee-nationale.fr/dyn/17/amendements/{uid_cd492}",
        ),
        _row(
            sid="an_amendements",
            uid="AMANR5L17PO419865B9999P0D1N000123",
            url=("https://www.assemblee-nationale.fr/dyn/17/amendements/"
                 "AMANR5L17PO419865B9999P0D1N000123"),
        ),
    ]
    out = se._filter_blocklist(rows)
    out_uids = [r["uid"] for r in out]
    assert uid_cd495 not in out_uids, "amendement CD495 PJL agricoles non filtré"
    assert uid_cd492 not in out_uids, "amendement CD492 PJL agricoles non filtré"
    assert "AMANR5L17PO419865B9999P0D1N000123" in out_uids, (
        "amendement légitime supprimé à tort")
