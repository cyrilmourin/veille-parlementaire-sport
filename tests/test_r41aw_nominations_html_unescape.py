"""R41-AW (2026-05-10) — fix nomination extraction sur HTML brut RSS.

Bug observé en prod 2026-05-10 (signalement Cyril) : après le merge de
R41-AU (multi-nominations + URL préservée), les items Olbia restaient
visibles sur la page Nominations avec leur titre source intact
(« Cette semaine, Olbia a appris que… ») au lieu d'être splittés et
normalisés.

Cause : `extract_nomination_facts` / `extract_all_nominations` ne
faisait PAS de `html.unescape` ni de strip HTML tags AVANT de matcher
les regex de verbe performatif. Les flux RSS WordPress (Olbia, Café du
Sport Business, Sport Stratégies, FFF…) émettent une <description>
qui contient :
- des entités HTML (`&nbsp;`, `&#8217;`, `&amp;`, `&laquo;`)
- des balises HTML (`<p>`, `<br>`, `<a href>`)

Avec `a&nbsp;été nommé`, la regex `\\ba\\s+été\\s+nommé\\b` ne match
pas — `&nbsp;` est du texte litéral, pas `\\s`. Aucune nomination
extraite → pas de split, pas de normalisation R41-AU.

Symétrique au fix R41-AO côté KeywordMatcher.

Fix : nouveau `_preclean_text(text)` qui fait html.unescape + strip
tags + collapse whitespace, appelé en tête de
`extract_nomination_facts` ET `extract_all_nominations`.

Tests :
1. Phrase HTML brute (`<p>X a&nbsp;été nommé Y du Z</p>`) → fact extrait.
2. Entités encodées (`&#8217;`, `&laquo;`, `&raquo;`) → text normalisé.
3. Multi-nominations dans HTML brut (newsletter Olbia type) → N facts.
4. Idempotence : 2 passages produisent le même résultat.
5. Régression : texte plat (sans HTML) toujours OK.
"""
from __future__ import annotations

from src.nominations import (
    _preclean_text,
    extract_all_nominations,
    extract_nomination_facts,
)


# ---------------------------------------------------------------------------
# 1. _preclean_text — comportements unitaires
# ---------------------------------------------------------------------------


def test_preclean_decodes_html_entities():
    src = "Eric Woerth a&nbsp;été nommé pr&eacute;sident du PMU."
    cleaned = _preclean_text(src)
    assert "a été" in cleaned
    assert "président" in cleaned
    assert "&nbsp;" not in cleaned
    assert "&eacute;" not in cleaned


def test_preclean_strips_html_tags():
    src = "<p>Eric Woerth <strong>a été nommé</strong> président du PMU.</p>"
    cleaned = _preclean_text(src)
    assert "<p>" not in cleaned
    assert "<strong>" not in cleaned
    assert "Eric Woerth" in cleaned
    assert "a été nommé" in cleaned


def test_preclean_idempotent():
    src = "<p>Eric Woerth a&nbsp;été nommé.</p>"
    once = _preclean_text(src)
    twice = _preclean_text(once)
    assert once == twice


def test_preclean_handles_non_string():
    assert _preclean_text(None) == ""
    assert _preclean_text("") == ""
    assert _preclean_text(123) == ""


# ---------------------------------------------------------------------------
# 2. Régression du bug observé : RSS WordPress avec entités HTML
# ---------------------------------------------------------------------------


def test_extract_nomination_from_html_with_nbsp_entity():
    """Régression du bug Olbia 2026-05-10 : `a&nbsp;été nommé` doit
    matcher comme `a été nommé`."""
    html = (
        "<p>Eric Woerth a&nbsp;été nommé président du PMU.</p>"
    )
    facts = extract_nomination_facts(html)
    assert facts is not None
    assert facts["person"] == "Eric Woerth"
    assert "président" in facts["function"]


def test_extract_nomination_from_html_with_apostrophe_entity():
    """Apostrophe typographique encodée en HTML (`&#8217;`)."""
    html = (
        "Camille Emi&eacute; devient pr&eacute;sidente de l&#8217;Office "
        "fran&ccedil;ais du sport."
    )
    facts = extract_nomination_facts(html)
    assert facts is not None
    assert "Camille" in facts["person"]
    assert "président" in facts["function"]


def test_extract_all_olbia_style_newsletter_html():
    """Newsletter Olbia type — HTML brut avec 2-3 nominations dans le
    summary. Avant R41-AW : 0 fact. Après : ≥ 2 facts."""
    html = (
        "<p>Cette semaine, Olbia a appris que&hellip;</p>"
        "<p>Eric Woerth a&nbsp;été nommé président du PMU.</p>"
        "<p>Camille Emié devient directrice de la communication "
        "de la FFF.</p>"
        "<p>Pierre Martin a&nbsp;été élu président de la "
        "Fédération française de tennis.</p>"
    )
    facts = extract_all_nominations(html)
    assert len(facts) >= 2, (
        f"Attendu ≥ 2 facts depuis HTML brut, vu {len(facts)}"
    )
    persons = {f["person"] for f in facts}
    assert any("Eric Woerth" in p for p in persons), (
        f"Eric Woerth manquant : {persons}"
    )


# ---------------------------------------------------------------------------
# 3. Régression : texte plat fonctionne toujours
# ---------------------------------------------------------------------------


def test_extract_plain_text_still_works():
    facts = extract_nomination_facts(
        "Eric Woerth a été nommé président du PMU."
    )
    assert facts is not None
    assert facts["person"] == "Eric Woerth"


def test_extract_all_plain_text_still_works():
    facts = extract_all_nominations(
        "Eric Woerth a été nommé président du PMU. "
        "Camille Emié devient directrice de la communication de la FFF."
    )
    assert len(facts) >= 2
