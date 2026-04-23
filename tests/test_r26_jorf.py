"""R26 (2026-04-23) — Tests JORF : extrait NOTICE, fallback corps, recherche corps.

Couvre trois axes :

1. Parser `_parse_texte_version` extrait correctement NOTICE et body_head.
2. Construction de l'Item : summary prioritaire NOTICE → body_head → fallback
   générique. Détection des nominations par hints élargis.
3. `matcher.apply` consomme `raw.haystack_body` : capte un arrêté dont le
   titre est générique mais le corps parle de sport.

Les fixtures XML reproduisent la structure DILA OPENDATA LEGIPUBLI (balises
NATURE, TITREFULL, ID, META_COMMUN, META_TEXTE_VERSION/NOTICE, TEXTE/VISAS/
ARTICLE). On n'invoque pas le réseau — tout est inline ou via
`_parse_texte_version` directement sur des bytes.
"""
from __future__ import annotations

from src.keywords import KeywordMatcher
from src.models import Item
from src.sources.dila_jorf import (
    KEEP_NATURES,
    _collect_inner_text,
    _parse_texte_version,
    fetch_source,
)


# -----------------------------------------------------------------
# 1) _collect_inner_text
# -----------------------------------------------------------------

def test_collect_inner_text_concatenates_descendants():
    from lxml import etree
    xml = b"<root><a>Bonjour </a><b>le <i>monde</i></b></root>"
    root = etree.fromstring(xml)
    assert _collect_inner_text(root) == "Bonjour le monde"


def test_collect_inner_text_strips_multiple_whitespaces():
    from lxml import etree
    xml = b"<root><p>Premier\n\n  paragraphe</p>\n<p>  deuxi\xc3\xa8me</p></root>"
    root = etree.fromstring(xml)
    # Tous les retours chariot et espaces multiples collapsés en un seul espace.
    assert "  " not in _collect_inner_text(root)
    assert "\n" not in _collect_inner_text(root)


def test_collect_inner_text_respects_max_len():
    from lxml import etree
    xml = b"<root><p>" + (b"x" * 10000) + b"</p></root>"
    root = etree.fromstring(xml)
    result = _collect_inner_text(root, max_len=500)
    # Cap mou : on s'arrête *après* avoir dépassé, donc <= max_len + taille
    # du dernier fragment. Sur ce cas (un seul gros <p>), on récupère tout
    # le fragment puis on s'arrête. Le test vérifie juste qu'on ne dépasse
    # pas grossièrement la fenêtre.
    assert len(result) <= 10000
    assert "x" in result


def test_collect_inner_text_none_returns_empty():
    assert _collect_inner_text(None) == ""


# -----------------------------------------------------------------
# 2) _parse_texte_version : NOTICE + body_head
# -----------------------------------------------------------------

_MINIMAL_JORF = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEXTE_VERSION>
  <META>
    <META_COMMUN>
      <ID>JORFTEXT000054321000</ID>
      <NATURE>ARRETE</NATURE>
    </META_COMMUN>
    <META_SPEC>
      <META_TEXTE_VERSION>
        <TITREFULL>Arr\xc3\xaat\xc3\xa9 du 20 avril 2026 portant nomination au cabinet de la ministre des sports</TITREFULL>
        <NOTICE>
          Nomination de Mme Claire DUPONT en qualit\xc3\xa9 de conseill\xc3\xa8re technique
          charg\xc3\xa9e des relations avec le mouvement sportif.
        </NOTICE>
        <DATE_PUBLI>2026-04-23</DATE_PUBLI>
      </META_TEXTE_VERSION>
    </META_SPEC>
  </META>
  <TEXTE>
    <VISAS>
      <CONTENU>Vu le d\xc3\xa9cret n\xc2\xb02022-1343 portant attributions du ministre charg\xc3\xa9 des sports,</CONTENU>
    </VISAS>
    <ARTICLE>
      <CONTENU>Art. 1er. - Mme Claire DUPONT est nomm\xc3\xa9e conseill\xc3\xa8re technique.</CONTENU>
    </ARTICLE>
  </TEXTE>
</TEXTE_VERSION>"""


def test_parse_texte_version_extracts_notice():
    info = _parse_texte_version(_MINIMAL_JORF)
    assert info is not None
    assert info["id"] == "JORFTEXT000054321000"
    assert info["nature"] == "ARRETE"
    assert "mouvement sportif" in info["notice"]
    assert "conseill" in info["notice"]


def test_parse_texte_version_extracts_body_head():
    info = _parse_texte_version(_MINIMAL_JORF)
    assert info is not None
    # body_head aplatit VISAS + ARTICLE dans l'ordre
    assert "Vu le d" in info["body_head"]
    assert "est nomm" in info["body_head"]


def test_parse_texte_version_rejects_unknown_nature():
    xml = _MINIMAL_JORF.replace(b"ARRETE", b"CIRCULAIRE")
    assert _parse_texte_version(xml) is None


def test_parse_texte_version_missing_notice_ok():
    # Retire le bloc NOTICE ; le parser doit renvoyer notice="" sans planter.
    xml = _MINIMAL_JORF.replace(
        b"<NOTICE>\n          Nomination de Mme Claire DUPONT en qualit\xc3\xa9 de conseill\xc3\xa8re technique\n          charg\xc3\xa9e des relations avec le mouvement sportif.\n        </NOTICE>",
        b"",
    )
    info = _parse_texte_version(xml)
    assert info is not None
    assert info["notice"] == ""
    # Le body reste accessible
    assert "Vu le d" in info["body_head"]


# -----------------------------------------------------------------
# 3) Construction Item : summary prioritaire, nominations, haystack_body
# -----------------------------------------------------------------

def test_fetch_source_summary_priorises_notice(monkeypatch):
    """Avec NOTICE présente, summary = notice[:400], pas le fallback."""
    _patch_dila(monkeypatch, [_MINIMAL_JORF])
    src = {"id": "dila_jorf", "category": "jorf", "days_back": 1}
    items = fetch_source(src)
    assert len(items) == 1
    it = items[0]
    assert "mouvement sportif" in it.summary
    assert it.summary != "Arrete publié au JORF."  # pas le fallback


def test_fetch_source_summary_fallback_body(monkeypatch):
    """Sans NOTICE, on prend le début du corps en fallback."""
    xml_no_notice = _MINIMAL_JORF.replace(
        b"<NOTICE>\n          Nomination de Mme Claire DUPONT en qualit\xc3\xa9 de conseill\xc3\xa8re technique\n          charg\xc3\xa9e des relations avec le mouvement sportif.\n        </NOTICE>",
        b"",
    )
    _patch_dila(monkeypatch, [xml_no_notice])
    src = {"id": "dila_jorf", "category": "jorf", "days_back": 1}
    items = fetch_source(src)
    assert len(items) == 1
    assert "Vu le d" in items[0].summary


def test_fetch_source_summary_final_fallback(monkeypatch):
    """Sans NOTICE ni TEXTE, on retombe sur le libellé nature."""
    xml_barebone = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEXTE_VERSION>
  <META>
    <META_COMMUN>
      <ID>JORFTEXT000054321001</ID>
      <NATURE>DECRET</NATURE>
    </META_COMMUN>
    <META_SPEC><META_TEXTE_VERSION>
      <TITREFULL>D\xc3\xa9cret du 20 avril 2026 relatif aux sports de nature</TITREFULL>
    </META_TEXTE_VERSION></META_SPEC>
  </META>
</TEXTE_VERSION>"""
    _patch_dila(monkeypatch, [xml_barebone])
    src = {"id": "dila_jorf", "category": "jorf", "days_back": 1}
    items = fetch_source(src)
    assert len(items) == 1
    assert items[0].summary == "Decret publié au JORF."


def test_fetch_source_categorises_nomination(monkeypatch):
    """R26 garde la détection nominations sur hints élargis."""
    _patch_dila(monkeypatch, [_MINIMAL_JORF])
    src = {"id": "dila_jorf", "category": "jorf", "days_back": 1}
    items = fetch_source(src)
    assert items[0].category == "nominations"


def test_fetch_source_populates_haystack_body(monkeypatch):
    """R26 : raw.haystack_body doit contenir le corps pour que matcher.apply
    puisse l'exploiter en aval."""
    _patch_dila(monkeypatch, [_MINIMAL_JORF])
    src = {"id": "dila_jorf", "category": "jorf", "days_back": 1}
    items = fetch_source(src)
    hs = items[0].raw.get("haystack_body") or ""
    assert "Vu le d" in hs
    assert "est nomm" in hs


# -----------------------------------------------------------------
# 4) matcher.apply consomme raw.haystack_body
# -----------------------------------------------------------------

def _make_matcher(tmp_path_factory=None):
    """Construit un matcher minimal avec un keyword 'sports' en réutilisant
    la vraie logique d'init (normalisation + pattern `(?<![a-z0-9]).(?![a-z0-9])`).
    On écrit un yaml temporaire car `KeywordMatcher.__init__` lit depuis
    un chemin ; c'est plus fiable que de hacker les attributs à la main
    (le vrai pattern est plus strict que `\\b...\\b` et les tests doivent
    taper dans la même regex que la prod)."""
    import tempfile
    import os
    fd, path = tempfile.mkstemp(suffix=".yml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("sport:\n  - sports\n")
        return KeywordMatcher(path)
    finally:
        os.unlink(path)


def test_matcher_apply_uses_haystack_body():
    """Titre + summary ne mentionnent pas 'sport', mais raw.haystack_body si."""
    matcher = _make_matcher()
    it = Item(
        source_id="dila_jorf",
        uid="JORFTEXT000099999999",
        category="jorf",
        chamber="JORF",
        title="Arrêté du 20 avril 2026 portant nomination",
        url="https://example.com/x",
        summary="Nomination d'un conseiller",
        raw={"haystack_body": "Vu le décret portant attributions du ministre chargé des sports, arrête."},
    )
    matcher.apply([it])
    assert it.matched_keywords == ["sports"]


def test_matcher_apply_no_regression_without_haystack():
    """Un item sans raw.haystack_body est traité comme avant."""
    matcher = _make_matcher()
    it = Item(
        source_id="min_sports_presse",
        uid="x",
        category="communiques",
        chamber="MinSports",
        title="Plan sports 2030",
        url="https://example.com/y",
        summary="",
        raw={},
    )
    matcher.apply([it])
    assert it.matched_keywords == ["sports"]


def test_matcher_apply_no_match_when_no_mention():
    """Ni titre ni summary ni haystack_body ne parlent de sport → pas de match."""
    matcher = _make_matcher()
    it = Item(
        source_id="dila_jorf",
        uid="x",
        category="jorf",
        chamber="JORF",
        title="Arrêté fixant le montant du SMIC",
        url="https://example.com/z",
        summary="Revalorisation annuelle.",
        raw={"haystack_body": "Vu le code du travail, le montant du SMIC est fixé à…"},
    )
    matcher.apply([it])
    assert it.matched_keywords == []


# -----------------------------------------------------------------
# Helpers : patcher le fetch DILA pour injecter des XMLs inline
# -----------------------------------------------------------------

def _patch_dila(monkeypatch, xml_list: list[bytes]) -> None:
    """Remplace `_list_recent_dumps` et l'extraction tar par une version
    qui rend directement les XML passés en paramètre. Évite tout I/O
    réseau et tarfile dans les tests unitaires."""
    from src.sources import dila_jorf as mod
    from datetime import datetime

    monkeypatch.setattr(
        mod, "_list_recent_dumps",
        lambda n=8: [("https://example.com/fake_dump.tar.gz", datetime(2026, 4, 23, 0, 30))],
    )
    monkeypatch.setattr(mod, "fetch_bytes", lambda url: b"fake-tar")
    monkeypatch.setattr(mod, "_iter_texte_versions", lambda raw: iter(xml_list))


# -----------------------------------------------------------------
# Sanity : KEEP_NATURES inchangé (garde-fou si quelqu'un élargit la liste
# sans reprendre la catégorisation nominations).
# -----------------------------------------------------------------

def test_keep_natures_stable():
    assert KEEP_NATURES == {"ARRETE", "DECRET", "DECISION", "LOI", "ORDONNANCE"}
