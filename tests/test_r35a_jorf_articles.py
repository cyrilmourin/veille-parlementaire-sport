"""R35-A (2026-04-24) — Tests JORF : corps depuis les fichiers ARTICLE séparés.

Sur le dump DILA JORF, TEXTE_VERSION.xml contient les métadonnées (titre,
nature, date, ministère) et des `<CONTENU/>` systématiquement VIDES. Le
corps réel des décrets et arrêtés — visas, articles numérotés, listes de
nominations — est stocké dans des fichiers ARTICLE séparés (`JORFARTI*.xml`)
rattachés au texte parent via `<CONTEXTE><TEXTE cid="JORFTEXT..."/>`.

Le cas réel qui a motivé ce patch : JORFTEXT000053930076
« Décret du 22 avril 2026 portant promotion et nomination à titre
exceptionnel dans l'ordre national de la Légion d'honneur » — aucun mot
sport dans le titre mais 15 lignes sur biathlon, ski-alpinisme et Jeux
Olympiques de Milan-Cortina dans l'article 1. Avant R35-A : non détecté
par le matcher. Après R35-A : matché via le body agrégé.

On ne touche pas le réseau : on construit un mini-tarball en mémoire.
"""
from __future__ import annotations

import io
import tarfile

from src.sources import dila_jorf as mod


def _make_minimal_tarball(files: dict[str, bytes]) -> bytes:
    """Construit un .tar.gz en mémoire avec les fichiers donnés
    (chemin → octets)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, data in files.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


_ARTICLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ARTICLE>
  <META>
    <META_COMMUN><ID>JORFARTI000053930077</ID><NATURE>Article</NATURE></META_COMMUN>
  </META>
  <CONTEXTE>
    <TEXTE cid="JORFTEXT000053930076" nature="DECRET">
      <TITRE_TXT>D\xc3\xa9cret du 22 avril 2026</TITRE_TXT>
    </TEXTE>
  </CONTEXTE>
  <BLOC_TEXTUEL>
    <CONTENU>
      <p>Par d\xc3\xa9cret du Pr\xc3\xa9sident, sur le rapport de la ministre des sports,
      sont nomm\xc3\xa9s dans l'ordre national de la L\xc3\xa9gion d'honneur :
      M. Fillon-Maillet (biathlon), Mme Jeanmonnot (biathlon), M. Perrot (biathlon)
      aux jeux Olympiques de Milan-Cortina. M. Tabouret en ski de fond aux
      jeux Paralympiques de Milan-Cortina.</p>
    </CONTENU>
  </BLOC_TEXTUEL>
</ARTICLE>"""


def test_index_articles_by_cid_extracts_body():
    """Un tarball minimal avec un seul ARTICLE doit être indexé par le cid
    du texte parent."""
    tar_bytes = _make_minimal_tarball({
        "20260423-004647/jorf/global/article/JORF/ARTI/JORFARTI000053930077.xml":
            _ARTICLE_XML,
    })
    idx = mod._index_articles_by_cid(tar_bytes)
    assert "JORFTEXT000053930076" in idx
    body = idx["JORFTEXT000053930076"]
    assert "ministre des sports" in body
    assert "biathlon" in body
    assert "Milan-Cortina" in body


def test_index_articles_by_cid_ignores_non_article_files():
    """Les fichiers hors /article/ ne doivent pas être parcourus."""
    tar_bytes = _make_minimal_tarball({
        "20260423/jorf/global/texte/version/JORFTEXT000000000001.xml":
            b"<TEXTE_VERSION><META/></TEXTE_VERSION>",
        "20260423/jorf/global/conteneur/JORFCONT000000000001.xml":
            b"<CONTENEUR/>",
    })
    idx = mod._index_articles_by_cid(tar_bytes)
    assert idx == {}


def test_index_articles_by_cid_skips_malformed_xml():
    """Un ARTICLE mal formé ne plante pas l'indexation globale."""
    tar_bytes = _make_minimal_tarball({
        "20260423/jorf/global/article/BAD.xml": b"<ARTICLE>broken",
        "20260423/jorf/global/article/JORFARTI000053930077.xml": _ARTICLE_XML,
    })
    idx = mod._index_articles_by_cid(tar_bytes)
    # Le bon article est indexé, le mauvais ignoré silencieusement
    assert "JORFTEXT000053930076" in idx


def test_index_articles_by_cid_concatenates_multiple_articles_same_cid():
    """Un décret avec plusieurs articles doit voir leurs corps concaténés."""
    art2 = _ARTICLE_XML.replace(
        b"JORFARTI000053930077",
        b"JORFARTI000053930078",
    ).replace(
        b"M. Fillon-Maillet (biathlon)",
        b"M. Autre athlete en ski-alpinisme",
    )
    tar_bytes = _make_minimal_tarball({
        "20260423/jorf/global/article/JORFARTI000053930077.xml": _ARTICLE_XML,
        "20260423/jorf/global/article/JORFARTI000053930078.xml": art2,
    })
    idx = mod._index_articles_by_cid(tar_bytes)
    body = idx["JORFTEXT000053930076"]
    assert "biathlon" in body
    assert "ski-alpinisme" in body


def test_fetch_source_uses_articles_body_when_texte_version_empty(monkeypatch):
    """Cas end-to-end : TEXTE_VERSION sans corps, articles séparés avec le
    corps sport → haystack_body doit être rempli depuis les ARTICLE."""
    texte_version_empty = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEXTE_VERSION>
  <META>
    <META_COMMUN>
      <ID>JORFTEXT000053930076</ID>
      <NATURE>DECRET</NATURE>
    </META_COMMUN>
    <META_SPEC>
      <META_TEXTE_VERSION>
        <TITREFULL>D\xc3\xa9cret du 22 avril 2026 portant promotion et nomination \xc3\xa0 titre exceptionnel dans l'ordre national de la L\xc3\xa9gion d'honneur</TITREFULL>
        <DATE_PUBLI>2026-04-23</DATE_PUBLI>
      </META_TEXTE_VERSION>
    </META_SPEC>
  </META>
  <NOTICE><CONTENU/></NOTICE>
  <VISAS><CONTENU/></VISAS>
</TEXTE_VERSION>"""
    from datetime import datetime
    monkeypatch.setattr(
        mod, "_list_recent_dumps",
        lambda n=8: [("https://example.com/fake.tar.gz", datetime(2026, 4, 23, 0, 30))],
    )
    monkeypatch.setattr(mod, "fetch_bytes", lambda url: b"fake")
    monkeypatch.setattr(
        mod, "_iter_texte_versions",
        lambda raw: iter([texte_version_empty]),
    )
    monkeypatch.setattr(
        mod, "_index_articles_by_cid",
        lambda raw: {
            "JORFTEXT000053930076": (
                "Par décret du Président, sur le rapport de la ministre "
                "des sports, sont nommés aux jeux Olympiques de Milan-Cortina "
                "MM. Fillon-Maillet et Jeanmonnot en biathlon."
            ),
        },
    )
    src = {"id": "dila_jorf", "category": "jorf", "days_back": 1}
    items = mod.fetch_source(src)
    assert len(items) == 1
    it = items[0]
    hs = it.raw.get("haystack_body", "")
    assert "jeux Olympiques" in hs
    assert "ministre des sports" in hs
    # Le summary aussi doit utiliser le body (fallback R26) puisque NOTICE
    # est vide.
    assert "jeux Olympiques" in it.summary or "ministre des sports" in it.summary


def test_fetch_source_keeps_texte_version_body_when_present(monkeypatch):
    """Non-régression R26 : si TEXTE_VERSION contient un corps non vide
    (cas rare mais possible sur anciens textes), on ne doit PAS l'écraser
    avec les articles — priorité au corps déjà trouvé."""
    texte_version_full = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEXTE_VERSION>
  <META>
    <META_COMMUN><ID>JORFTEXT000000000001</ID><NATURE>DECRET</NATURE></META_COMMUN>
    <META_SPEC><META_TEXTE_VERSION>
      <TITREFULL>D\xc3\xa9cret ancien (sport)</TITREFULL>
    </META_TEXTE_VERSION></META_SPEC>
  </META>
  <TEXTE><ARTICLE><CONTENU>Corps ancien format dans TEXTE_VERSION, mot cle sport.</CONTENU></ARTICLE></TEXTE>
</TEXTE_VERSION>"""
    from datetime import datetime
    monkeypatch.setattr(
        mod, "_list_recent_dumps",
        lambda n=8: [("x", datetime(2026, 4, 23))],
    )
    monkeypatch.setattr(mod, "fetch_bytes", lambda url: b"fake")
    monkeypatch.setattr(
        mod, "_iter_texte_versions",
        lambda raw: iter([texte_version_full]),
    )
    # Articles index renvoie autre chose pour le même id — on ne doit pas
    # l'utiliser puisque le texte_version a déjà un body.
    monkeypatch.setattr(
        mod, "_index_articles_by_cid",
        lambda raw: {"JORFTEXT000000000001": "CE TEXTE NE DOIT PAS APPARAITRE"},
    )
    src = {"id": "dila_jorf", "category": "jorf", "days_back": 1}
    items = mod.fetch_source(src)
    assert len(items) == 1
    hs = items[0].raw.get("haystack_body", "")
    assert "Corps ancien" in hs
    assert "NE DOIT PAS APPARAITRE" not in hs
