"""R40-I (2026-04-26) — Snippet centré sur le match dans haystack_body.

Bug constaté par Cyril 2026-04-26 après le bump R40-G/H : le matching
keyword utilise `title + summary + haystack_body` (jusqu'à 200k chars
pour les CR), mais le snippet visible était construit UNIQUEMENT depuis
`summary` (2000 chars max). Conséquence : si le keyword a matché à la
position 50k dans le `haystack_body`, le snippet affichait juste les
premiers 800 chars du summary, sans le mot-clé qui a déclenché le
match — UX confuse pour l'utilisateur qui voit l'intro du CR sans
comprendre pourquoi il remonte.

Fix : `KeywordMatcher.apply()` construit désormais le snippet à partir
du haystack le plus complet (haystack_body si présent, fallback
summary, fallback titre). `build_snippet` cherche le 1er match dans le
texte fourni → le snippet est centré sur le keyword effectif.

Vérifié : pour les 4 sources CR (an_cr_commissions, senat_cr_commissions,
senat._fetch_debats_zip, assemblee._normalize_syceron), le summary est
un strict préfixe du haystack_body, donc pas de risque d'introduire du
bruit qui aurait été nettoyé du summary mais pas du haystack.
"""
from __future__ import annotations

from src.keywords import KeywordMatcher
from src.models import Item


import pytest


@pytest.fixture
def matcher_dopage(tmp_path) -> KeywordMatcher:
    """Matcher minimal avec un seul keyword 'dopage' pour le test."""
    yml = tmp_path / "kw.yml"
    yml.write_text("sport_general:\n  - dopage\n", encoding="utf-8")
    return KeywordMatcher(yml)


@pytest.fixture
def matcher_jop(tmp_path) -> KeywordMatcher:
    yml = tmp_path / "kw.yml"
    yml.write_text("sport_general:\n  - JOP\n", encoding="utf-8")
    return KeywordMatcher(yml)


def _make_cr_item(*, summary: str, haystack_body: str) -> Item:
    """Item CR simulé : title neutre + summary court + haystack_body long
    où le keyword n'apparaît qu'au-delà du summary."""
    return Item(
        source_id="senat_cr_culture",
        uid="test-cr-001",
        category="comptes_rendus",
        chamber="Senat",
        title="Semaine du 13 avril 2026",
        url="https://example.test/cr",
        summary=summary,
        raw={"haystack_body": haystack_body},
    )


def test_snippet_centre_sur_match_dans_haystack_body(matcher_dopage):
    """Cas typique R40-I : le keyword "dopage" apparaît à la position
    ~50k dans le haystack_body, hors du summary (2000 chars). Le snippet
    doit contenir "dopage" et le contexte autour."""
    # Summary = 2000 chars de bruit qui ne contient PAS "dopage"
    summary = "Audition de M. Dupont. " * 100  # ~2300 chars de blabla
    summary = summary[:2000]
    # haystack_body = summary + 50k de filler + phrase avec dopage + suite
    filler = "Examen du PJL relatif à l'éducation. " * 1000  # ~37k chars
    keyword_phrase = (
        "Le rapporteur insiste sur la lutte contre le dopage dans "
        "le sport professionnel et amateur. Mesures envisagées : "
        "renforcement de l'AFLD, sanctions plus dures."
    )
    suffix = "Suite des débats. " * 500  # ~9k chars
    haystack_body = summary + filler + keyword_phrase + suffix
    assert "dopage" not in summary, "fixture mal construite : dopage doit être hors summary"
    assert "dopage" in haystack_body
    assert haystack_body.index("dopage") > 30000, (
        "fixture mal construite : dopage doit être au-delà de 30k")

    item = _make_cr_item(summary=summary, haystack_body=haystack_body)
    list(matcher_dopage.apply([item]))

    # Le matching a bien capté le keyword
    assert "dopage" in item.matched_keywords or any(
        "dopage" in k for k in item.matched_keywords)
    # Le snippet contient le passage avec le keyword (R40-I)
    assert "dopage" in item.snippet, (
        f"Snippet ne contient pas 'dopage'. Snippet : {item.snippet!r}")
    # Le snippet contient aussi du contexte autour
    assert ("lutte contre" in item.snippet
            or "AFLD" in item.snippet
            or "sport professionnel" in item.snippet), (
        f"Snippet sans contexte autour du match. Snippet : {item.snippet!r}")


def test_snippet_pas_de_haystack_body_fallback_sur_summary(matcher_dopage):
    """Régression : pour les sources sans `haystack_body` (questions,
    amendements, dossiers, JORF court...), le snippet doit toujours être
    construit depuis le summary comme avant R40-I."""
    summary = (
        "M. Dupont interpelle le Ministre des Sports sur le dopage "
        "dans les championnats régionaux. Il demande des mesures urgentes."
    )
    item = Item(
        source_id="an_questions",
        uid="qe-001",
        category="questions",
        chamber="AN",
        title="Question écrite n°123",
        url="https://example.test/q",
        summary=summary,
        raw={},  # pas de haystack_body
    )
    list(matcher_dopage.apply([item]))
    assert "dopage" in item.snippet
    # Snippet construit depuis le summary qui est court
    assert len(item.snippet) <= len(summary) + 4  # +4 pour "…" éventuels


def test_snippet_match_dans_summary_premier_extrait_correct(matcher_dopage):
    """Quand le keyword est dans le summary ET dans le haystack_body
    (cas le plus fréquent : CR commission qui parle de sport dès la
    1ère page), le snippet doit prendre la 1ère occurrence — qui est
    dans le summary, donc l'extrait reste court et propre."""
    summary = (
        "Audition de la ministre des Sports sur le dopage. "
        "Présentation du plan AFLD 2026."
    )
    # haystack_body étend le summary avec d'autres mentions plus loin
    haystack_body = summary + " " + ("Discussion. " * 5000)  # +60k chars
    item = _make_cr_item(summary=summary, haystack_body=haystack_body)
    list(matcher_dopage.apply([item]))
    assert "dopage" in item.snippet
    # Le snippet est court (premier match était dans les premiers chars)
    # — pas de descente jusqu'au filler de 60k
    assert len(item.snippet) < 1500


def test_snippet_haystack_body_vide_fallback_sur_summary(matcher_dopage):
    """Si haystack_body est explicitement vide ('' ou None) dans le raw,
    on retombe sur summary — pas de crash."""
    item = Item(
        source_id="senat_cr_culture",
        uid="test-empty-haystack",
        category="comptes_rendus",
        chamber="Senat",
        title="CR test",
        url="https://example.test/x",
        summary="Mention du dopage dans l'introduction.",
        raw={"haystack_body": ""},
    )
    list(matcher_dopage.apply([item]))
    assert "dopage" in item.snippet


def test_snippet_aucun_match_retourne_debut_du_texte(matcher_jop):
    """Régression `build_snippet` : si aucun keyword ne matche le texte
    fourni à `build_snippet` (cas où le match vient uniquement du title),
    on retourne le début du texte tronqué — pas de crash."""
    # Item dont le match vient du title (pas du body)
    matcher = matcher_jop
    item = Item(
        source_id="senat_cr_culture",
        uid="test-match-title-only",
        category="comptes_rendus",
        chamber="Senat",
        title="JOP 2030 — préparation",
        url="https://example.test/x",
        summary="Discussion budget audiovisuel public. " * 30,
        raw={
            "haystack_body": "Discussion budget audiovisuel public. " * 1000,
        },
    )
    list(matcher.apply([item]))
    # Match via title → keywords détectés
    assert any("JOP" in k for k in item.matched_keywords)
    # Snippet = début du haystack (puisque pas de JOP dans haystack)
    assert isinstance(item.snippet, str)
    assert len(item.snippet) > 0
