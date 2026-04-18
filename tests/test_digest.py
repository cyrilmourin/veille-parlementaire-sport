"""Tests unitaires du builder d'email."""
import json
from src.digest import build_html


def test_build_html_empty():
    html, total = build_html([], "https://veille.sideline-conseil.fr")
    assert total == 0
    assert "Aucun nouvel item" in html
    assert "Sideline" in html.lower() or "SIDELINE" in html


def test_build_html_with_items():
    rows = [
        {
            "source_id": "an_amendements", "uid": "1",
            "category": "amendements", "chamber": "AN",
            "title": "Amendement test Pass'Sport",
            "url": "https://example.fr/1",
            "summary": "Résumé test",
            "published_at": "2026-04-17T10:00:00",
            "matched_keywords": json.dumps(["Pass'Sport"]),
        },
        {
            "source_id": "jorf", "uid": "2",
            "category": "jorf", "chamber": "JORF",
            "title": "Arrêté nomination ANS",
            "url": "https://example.fr/2",
            "summary": "",
            "published_at": "2026-04-18T00:00:00",
            "matched_keywords": json.dumps(["ANS"]),
        },
    ]
    html, total = build_html(rows, "https://veille.sideline-conseil.fr")
    assert total == 2
    assert "Amendement test" in html
    assert "Arrêté nomination ANS" in html
    assert "Amendements" in html
    assert "JORF" in html


def test_build_html_skips_unmatched():
    rows = [
        {
            "source_id": "x", "uid": "a", "category": "communiques", "chamber": "",
            "title": "Non pertinent", "url": "#", "summary": "",
            "published_at": "", "matched_keywords": "[]",
        },
    ]
    _, total = build_html(rows, "https://veille.sideline-conseil.fr")
    assert total == 0
