"""R41-G (2026-04-27) — CR commissions AN avec PDF publié en différé.

Bug Lidl porté côté Sport : quand un CR a sa page HTML publiée mais
son PDF pas encore disponible (cas typique : l'AN met la page en
ligne quelques jours après l'audition, mais le transcript PDF est
publié 1-2 semaines plus tard), l'ancien code :
1. Ingérait l'Item avec haystack_body=""
2. Marquait `scanned.add(num)`
3. Au run T+10j, num était dans scanned → skip → on ne re-cherchait
   PLUS le PDF même quand il devenait disponible

Fix : `_fetch_cr` retourne désormais `(item, has_body)` avec
`has_body = pdf_status == 200 and len(body) >= 200`. La boucle
`fetch_source` ne marque comme scanned que si `has_body`.

Cas concret cité : audition cion-eco N076 du 14/04/2026 côté veille
Lidl, équivalent attendu côté Sport sur des CR transverses (e.g.
cion-cedu, cion-soc).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.sources import an_cr_commissions as mod
from src.models import Item


def _make_item(slug: str, session: str, num: int, body: str = "body") -> Item:
    return Item(
        source_id="an_cr_commissions",
        uid=f"an-cr-{slug}-{session}-{num:03d}",
        category="comptes_rendus",
        chamber="AN",
        title=f"CR {num}",
        url=f"http://example.test/{num}",
        published_at=datetime(2026, 4, 22),
        summary="x",
        raw={"haystack_body": body, "slug": slug,
             "session": session, "num": num},
    )


def test_pdf_body_present_marque_scanned(monkeypatch, tmp_path):
    """Cas nominal : PDF présent → has_body=True → scanned marqué."""
    state_file = tmp_path / "an_cr_state.json"
    monkeypatch.setattr(mod, "STATE_PATH", state_file)

    def fake(slug, session, num, label):
        if num == 5:
            return (_make_item(slug, session, num, body="x" * 500),
                    True)  # has_body
        return (None, False)

    monkeypatch.setattr(mod, "_fetch_cr", fake)
    items = mod.fetch_source({
        "commissions": {"cion-cedu": "CCE"},
        "max_new_per_run": 5,
        "miss_tolerance": 3,
        "max_num": 5,
        "session": "2526",
    })
    assert len(items) == 1
    st = json.loads(state_file.read_text())
    # num=5 marqué scanned
    assert 5 in st["2526"]["cion-cedu"]["scanned"]


def test_pdf_body_vide_PAS_marque_scanned(monkeypatch, tmp_path):
    """R41-G fix : si le PDF est absent (body court ou vide), l'item
    est ingéré pour la trace MAIS le num n'est PAS marqué scanned →
    sera ré-ingéré au prochain run quand le PDF sera publié."""
    state_file = tmp_path / "an_cr_state.json"
    monkeypatch.setattr(mod, "STATE_PATH", state_file)

    def fake(slug, session, num, label):
        if num == 5:
            return (_make_item(slug, session, num, body=""),
                    False)  # PAS has_body
        return (None, False)

    monkeypatch.setattr(mod, "_fetch_cr", fake)
    items = mod.fetch_source({
        "commissions": {"cion-cedu": "CCE"},
        "max_new_per_run": 5,
        "miss_tolerance": 3,
        "max_num": 5,
        "session": "2526",
    })
    # L'item est quand même produit (trace de l'existence de la page HTML)
    assert len(items) == 1
    st = json.loads(state_file.read_text())
    # MAIS num=5 NE doit PAS être dans scanned (bug Lidl original)
    assert 5 not in st["2526"]["cion-cedu"]["scanned"], (
        "num avec PDF vide ne doit PAS être marqué scanned (R41-G fix : "
        "permettre re-ingestion quand le PDF est publié plus tard)"
    )


def test_run_apres_pdf_apparu_re_ingere(monkeypatch, tmp_path):
    """Scénario complet en 2 runs :
    - Run T+0 : page HTML 200 mais PDF 404 → item ingéré sans body,
      num PAS marqué scanned
    - Run T+10j : PDF désormais 200 → item ré-ingéré avec body complet,
      num enfin marqué scanned"""
    state_file = tmp_path / "an_cr_state.json"
    monkeypatch.setattr(mod, "STATE_PATH", state_file)

    pdf_published = {"value": False}

    def fake(slug, session, num, label):
        if num == 5:
            if pdf_published["value"]:
                return (_make_item(slug, session, num, body="x" * 1000),
                        True)
            else:
                return (_make_item(slug, session, num, body=""),
                        False)
        return (None, False)

    monkeypatch.setattr(mod, "_fetch_cr", fake)
    src_cfg = {
        "commissions": {"cion-cedu": "CCE"},
        "max_new_per_run": 5,
        "miss_tolerance": 3,
        "max_num": 5,
        "session": "2526",
    }

    # Run T+0 : PDF pas encore publié
    items_t0 = mod.fetch_source(src_cfg)
    assert len(items_t0) == 1
    st_t0 = json.loads(state_file.read_text())
    assert 5 not in st_t0["2526"]["cion-cedu"]["scanned"]

    # Run T+10j : PDF publié
    pdf_published["value"] = True
    items_t10 = mod.fetch_source(src_cfg)
    assert len(items_t10) == 1
    # Body complet cette fois
    assert len(items_t10[0].raw["haystack_body"]) >= 200
    st_t10 = json.loads(state_file.read_text())
    # Maintenant marqué scanned (PDF présent)
    assert 5 in st_t10["2526"]["cion-cedu"]["scanned"]


def test_compat_ascendante_legacy_signature(monkeypatch, tmp_path):
    """Régression : un mock qui retourne encore l'ancien format
    `Item | None` (sans tuple) doit continuer à fonctionner — la boucle
    détecte le format et adapte. Permet d'éviter de casser les tests
    hérités qui n'ont pas encore migré vers le tuple."""
    state_file = tmp_path / "an_cr_state.json"
    monkeypatch.setattr(mod, "STATE_PATH", state_file)

    def fake_legacy(slug, session, num, label):
        # Format pré-R41-G : Item | None (pas tuple)
        if num == 5:
            return _make_item(slug, session, num, body="body")
        return None

    monkeypatch.setattr(mod, "_fetch_cr", fake_legacy)
    items = mod.fetch_source({
        "commissions": {"cion-cedu": "CCE"},
        "max_new_per_run": 5,
        "miss_tolerance": 3,
        "max_num": 5,
        "session": "2526",
    })
    # L'item est ingéré
    assert len(items) == 1
    # En mode legacy, has_body = (Item is not None) = True → scanned marqué
    st = json.loads(state_file.read_text())
    assert 5 in st["2526"]["cion-cedu"]["scanned"]


def test_seuil_has_body_strict_200_chars(monkeypatch):
    """Régression sur le seuil exact : 199 chars → has_body=False ;
    200 chars → has_body=True. Choix Lidl porté tel quel."""
    html_url = (
        "https://www.assemblee-nationale.fr/dyn/17/comptes-rendus/"
        "cion-cedu/l17cion-cedu2526010_compte-rendu"
    )
    pdf_url = html_url + ".pdf"
    html_ok = b"<html><body><h1>OK</h1></body></html>"

    # has_body False — body 199 chars
    def stub_199(url, timeout=None):
        if url == html_url:
            return (200, html_ok)
        if url == pdf_url:
            return (200, b"x")
        return (404, None)
    monkeypatch.setattr(mod, "_fetch_silent", stub_199)
    monkeypatch.setattr(mod, "_extract_pdf_text",
                        lambda b, max_chars=10000: "x" * 199)
    _, has_body_short = mod._fetch_cr("cion-cedu", "2526", 10, "X")
    assert has_body_short is False

    # has_body True — body 200 chars exactement
    monkeypatch.setattr(mod, "_extract_pdf_text",
                        lambda b, max_chars=10000: "x" * 200)
    _, has_body_exact = mod._fetch_cr("cion-cedu", "2526", 10, "X")
    assert has_body_exact is True
