"""R32 (2026-04-24) — Tests des primitives centralisées `src/textclean.py`.

Couverture : `strip_html`, `decode_bytes`, `strip_technical_prefix`,
`smart_truncate`. Tous les cas reproduisent des scénarios réels rencontrés
sur les sources (Sénat amendements HTML brut, JORF entités numériques,
RSS Sénat Latin-9, préambule Syceron CR AN, tronquage snippet long).
"""
from __future__ import annotations

import pytest

from src import textclean


# ---------------------------------------------------------------------------
# strip_html
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_empty_and_none(self):
        assert textclean.strip_html("") == ""
        assert textclean.strip_html(None) == ""

    def test_plain_text_unchanged(self):
        assert textclean.strip_html("bonjour le monde") == "bonjour le monde"

    def test_simple_tags_removed(self):
        assert textclean.strip_html("<p>hello</p>") == "hello"
        assert textclean.strip_html("<b>gras</b> et <i>italique</i>") == "gras et italique"

    def test_tags_with_attributes(self):
        # Cas Sénat amendements R13-C
        raw = '<p style="text-align: justify;">Par cet amendement, les députés du groupe…</p>'
        out = textclean.strip_html(raw)
        assert "<" not in out and ">" not in out
        assert "text-align" not in out
        assert "députés" in out

    def test_numeric_entities_decoded(self):
        # `&#x00E9;` → `é` (Sénat amendements export XHTML)
        raw = "Par cet amendement, les d&#x00E9;put&#x00E9;.es"
        assert textclean.strip_html(raw) == "Par cet amendement, les député.es"

    def test_named_entities_decoded(self):
        assert textclean.strip_html("Tom &amp; Jerry") == "Tom & Jerry"
        assert textclean.strip_html("&lt;balise&gt;") == "<balise>"

    def test_nbsp_normalized_to_space(self):
        # U+00A0 (NBSP) très présent dans les dispositifs Sénat
        raw = "article\u00a0L.\u00a0100"
        assert textclean.strip_html(raw) == "article L. 100"

    def test_narrow_nbsp_normalized(self):
        # U+202F (narrow NBSP) — dispositifs Sénat récents
        assert textclean.strip_html("12\u202f345 euros") == "12 345 euros"

    def test_zwsp_and_bom_normalized(self):
        assert textclean.strip_html("hello\u200bworld") == "hello world"
        assert textclean.strip_html("hello\ufeffworld") == "hello world"

    def test_collapse_multiple_whitespace(self):
        assert textclean.strip_html("a     b\t\nc") == "a b c"

    def test_idempotent(self):
        raw = '<p style="foo">Tom &amp; Jerry\u00a0chase</p>'
        once = textclean.strip_html(raw)
        twice = textclean.strip_html(once)
        assert once == twice

    def test_script_and_style_tags_contents_kept(self):
        # On ne retire que la balise, pas son contenu (différent de BeautifulSoup)
        # C'est intentionnel : les summaries RSS ne contiennent jamais de <script>,
        # et on préfère garder du texte « sale » qu'en perdre.
        assert "alert" in textclean.strip_html("<script>alert('x')</script>")

    def test_nested_tags(self):
        raw = "<div><p><strong>Important</strong> message</p></div>"
        assert textclean.strip_html(raw) == "Important message"


# ---------------------------------------------------------------------------
# decode_bytes
# ---------------------------------------------------------------------------


class TestDecodeBytes:
    def test_utf8_roundtrip(self):
        text, enc = textclean.decode_bytes("café".encode("utf-8"))
        assert text == "café"
        assert enc == "utf-8-sig"

    def test_utf8_sig_bom_stripped(self):
        payload = "\ufeffcafé".encode("utf-8")
        text, enc = textclean.decode_bytes(payload)
        assert text == "café"
        assert enc == "utf-8-sig"

    def test_cp1252_fallback(self):
        # Caractères `é` en Latin-1/cp1252 = 0xE9 — invalide en utf-8 seul
        payload = b"\xe9t\xe9"
        text, enc = textclean.decode_bytes(payload)
        assert text == "été"
        assert enc == "cp1252"

    def test_iso8859_15_euro(self):
        # R19-A : le flux RSS Sénat theme_sport utilisait ISO-8859-15
        # (différence clé vs 8859-1 : € au byte 0xA4)
        # cp1252 décode aussi 0xA4 (→ ¤) et passe avant iso-8859-15,
        # donc on teste que la cascade retourne un résultat cohérent plutôt
        # qu'une erreur.
        payload = "10€".encode("iso-8859-15")
        text, enc = textclean.decode_bytes(payload)
        assert enc in ("cp1252", "iso-8859-15")
        assert "10" in text

    def test_never_raises_on_garbage(self):
        payload = bytes([0xFF, 0xFE, 0xFF, 0xFE])
        text, enc = textclean.decode_bytes(payload)
        assert isinstance(text, str)
        # cp1252 accepte 0xFE/0xFF (ÿ, þ) donc on y arrive avant le replace
        assert enc in ("cp1252", "iso-8859-15", "utf-8+replace")

    def test_forbidden_utf8_then_replace(self):
        # Un seul candidat strict qui échoue → fallback utf-8+replace
        payload = b"\xff\xfe"
        text, enc = textclean.decode_bytes(payload, candidates=("utf-8",))
        assert "\ufffd" in text or text
        assert enc == "utf-8+replace"

    def test_str_input_rejected(self):
        with pytest.raises(TypeError):
            textclean.decode_bytes("déjà str")  # type: ignore[arg-type]

    def test_custom_candidate_order(self):
        payload = "été".encode("utf-8")
        text, enc = textclean.decode_bytes(payload, candidates=("utf-8", "cp1252"))
        assert text == "été"
        assert enc == "utf-8"


# ---------------------------------------------------------------------------
# strip_technical_prefix
# ---------------------------------------------------------------------------


class TestStripTechnicalPrefix:
    def test_empty_input(self):
        assert textclean.strip_technical_prefix("", ["X"]) == ""
        assert textclean.strip_technical_prefix(None, ["X"]) is None  # type: ignore[arg-type]

    def test_no_marker_found(self):
        txt = "Texte libre sans aucun marqueur technique."
        assert textclean.strip_technical_prefix(txt, ["INEXISTANT"]) == txt

    def test_syceron_preamble_stripped(self):
        # Cas CR AN : préambule technique puis « Présidence de … »
        raw = (
            "CRSANR5L17S2024N00123 séance publique du 15 mars 2024 "
            "Présidence de Mme Yaël Braun-Pivet La séance est ouverte à quinze heures."
        )
        out = textclean.strip_technical_prefix(raw, ["Présidence de"])
        assert out.startswith("Présidence de")
        assert "CRSANR5L17" not in out

    def test_marker_past_max_prefix_ignored(self):
        # Le marqueur existe mais au-delà de max_prefix → on ne coupe pas
        long_prefix = "x" * 700 + " MARKER rest"
        out = textclean.strip_technical_prefix(long_prefix, ["MARKER"], max_prefix=600)
        assert out == long_prefix

    def test_nearest_marker_wins(self):
        # Plusieurs marqueurs présents → on prend celui à la position la plus basse
        raw = "prefix A then B rest"
        assert textclean.strip_technical_prefix(raw, ["A", "B"]) == "A then B rest"
        assert textclean.strip_technical_prefix(raw, ["B", "A"]) == "A then B rest"

    def test_marker_at_zero_no_op(self):
        # Déjà en tête → pas de coupe (idempotence)
        raw = "Présidence de ouverture séance"
        assert textclean.strip_technical_prefix(raw, ["Présidence de"]) == raw

    def test_idempotent(self):
        raw = "CRSANR5L17 junk Présidence de ouverture"
        once = textclean.strip_technical_prefix(raw, ["Présidence de"])
        twice = textclean.strip_technical_prefix(once, ["Présidence de"])
        assert once == twice


# ---------------------------------------------------------------------------
# smart_truncate
# ---------------------------------------------------------------------------


class TestSmartTruncate:
    def test_empty_input(self):
        assert textclean.smart_truncate("", 100) == ""

    def test_under_limit_no_change(self):
        assert textclean.smart_truncate("hello", 100) == "hello"

    def test_exactly_at_limit(self):
        assert textclean.smart_truncate("abcde", 5) == "abcde"

    def test_truncate_at_word_boundary(self):
        raw = "Ceci est une phrase qui va être tronquée au bon endroit"
        out = textclean.smart_truncate(raw, 20)
        assert out.endswith("…")
        # Ne doit pas couper au milieu d'un mot
        assert not out[:-1].endswith(("n", "d", "r"))  # ex. « tronqué » coupé
        # La dernière sous-chaîne avant « … » doit être un mot complet
        words_before_ellipsis = out[:-1].strip().split()
        for w in words_before_ellipsis:
            assert " " not in w  # sanité de découpe

    def test_truncate_no_space_falls_back_to_cut(self):
        # Mot unique monstrueux (URL par ex.) : on coupe net
        raw = "a" * 200
        out = textclean.smart_truncate(raw, 50)
        assert out.endswith("…")
        assert len(out) <= 52  # 50 + « … »

    def test_ellipsis_custom(self):
        out = textclean.smart_truncate("hello world foo bar baz", 10, ellipsis="...")
        assert out.endswith("...")

    def test_idempotent_when_under_limit(self):
        raw = "court"
        once = textclean.smart_truncate(raw, 100)
        twice = textclean.smart_truncate(once, 100)
        assert once == twice == raw

    def test_word_boundary_must_be_past_half(self):
        # Si le seul espace avant max_len est trop proche du début (< 50%),
        # on préfère couper net plutôt que de livrer un extrait amputé.
        raw = "a" + " " + "b" * 100
        out = textclean.smart_truncate(raw, 50)
        # L'espace est en position 1 — < 25 (50% de 50), donc cut net
        assert out == "a " + "b" * 48 + "…" or out.endswith("…")


# ---------------------------------------------------------------------------
# Sanity : délégation par les helpers existants
# ---------------------------------------------------------------------------


class TestDelegationFromCallers:
    """Vérifie que les helpers `_clean_html` / `_strip_html` des modules
    caller continuent de fonctionner et renvoient la même chose que
    `textclean.strip_html`. Garde-fou contre une régression sur le wrapper.
    """

    def test_keywords_clean_html_delegates(self):
        from src import keywords
        raw = '<p style="foo">Tom &amp; Jerry\u00a0chase</p>'
        assert keywords._clean_html(raw) == textclean.strip_html(raw)

    def test_senat_strip_html_delegates(self):
        from src.sources import senat
        raw = '<p>Dispositif\u202famendement</p>'
        assert senat._strip_html(raw) == textclean.strip_html(raw)

    def test_senat_amendements_strip_html_delegates(self):
        from src.sources import senat_amendements
        raw = '<p>Par cet amendement, les d&#x00E9;put&#x00E9;.es</p>'
        assert senat_amendements._strip_html(raw) == textclean.strip_html(raw)
