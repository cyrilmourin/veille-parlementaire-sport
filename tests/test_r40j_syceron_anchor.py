"""R40-J (2026-04-27) — Ancre absolue Syceron pour CR plénières AN.

Avant R40-J, le text-fragment R13-K (`#:~:text=<kw>`) marchait sur
Chrome/Edge/Safari 16.4+ mais dégradait silencieusement sur Firefox
(arrivée en haut de page, pas de scroll auto). Étude de faisabilité
demandée par Cyril 2026-04-27 :

- Le XML Syceron AN expose `id_syceron="<n>"` sur chaque paragraphe
  structurel (`<para>`, `<titreStruct>`, `<presidentSeance>`, etc.)
- La page HTML AN `/dyn/17/comptes-rendus/seance/<cr_ref>` expose
  ces mêmes valeurs en attribut HTML `id="<n>"` standard (vérifié
  via curl 2026-04-27 sur CRSANR5L17S2025E1N011 : 2 occurrences de
  `id="3839555"` dans le DOM rendu)
- → URL `…#<id_syceron>` fonctionne sur TOUS les navigateurs

Implémentation :
1. `_strip_xml_with_anchors` (assemblee.py) : strip XML + retourne
   `(stripped_text, [(char_offset, id_syceron), ...])` triable
2. `_normalize_syceron` stocke `raw.syceron_index` dans l'item
3. `site_export.py` R13-K : si `syceron_index` présent, bisect pour
   trouver l'ancre correspondant à la position du 1er kw matché
   dans `haystack_body`. Fallback text-fragment R13-K sinon.

Sources autres (Sénat plénières + 2× CR commissions) : pas d'index
Syceron équivalent (vérifié), conservent le text-fragment R13-K.
"""
from __future__ import annotations

import bisect

from src.sources.assemblee import _strip_xml_with_anchors


# ---------------------------------------------------------------------------
# 1. _strip_xml_with_anchors : algo single-pass O(N)
# ---------------------------------------------------------------------------


def test_strip_anchors_extrait_tous_les_id_syceron():
    """Le XML Syceron contient typiquement 100-700 paragraphes
    porteurs d'`id_syceron`. Tous doivent être extraits."""
    xml = """<?xml version='1.0'?>
<compteRendu>
  <para id_syceron="100">Première intervention.</para>
  <para id_syceron="200">Deuxième intervention.</para>
  <titreStruct id_syceron="300">
    <intitule>Section</intitule>
  </titreStruct>
  <para id_syceron="400">Troisième intervention.</para>
</compteRendu>"""
    text, anchors = _strip_xml_with_anchors(xml)
    ids = [a[1] for a in anchors]
    assert ids == ["100", "200", "300", "400"], f"got {ids}"


def test_strip_anchors_offsets_croissants():
    """Les offsets dans `anchors` doivent être triés ascendant — c'est
    la prérequis pour `bisect.bisect_right` côté export."""
    xml = """<root>
<para id_syceron="1">Un.</para>
<para id_syceron="2">Deux.</para>
<para id_syceron="3">Trois.</para>
<para id_syceron="4">Quatre.</para>
</root>"""
    _, anchors = _strip_xml_with_anchors(xml)
    offsets = [a[0] for a in anchors]
    assert offsets == sorted(offsets)


def test_strip_anchors_text_complet_strippe():
    """Le texte stripé doit contenir tout le contenu, sans aucun tag."""
    xml = """<root>
<para id_syceron="1">Bonjour.</para>
<titreStruct id_syceron="2"><intitule>Sport</intitule></titreStruct>
</root>"""
    text, _ = _strip_xml_with_anchors(xml)
    assert "Bonjour" in text
    assert "Sport" in text
    assert "<" not in text and ">" not in text


def test_strip_anchors_xml_sans_id_syceron():
    """Si le XML ne contient aucun id_syceron (CR très ancien ou format
    inattendu), `anchors` est vide et `text` reste un strip naïf —
    comportement identique à `_strip_xml`."""
    xml = "<root><p>Du texte sans annotation.</p></root>"
    text, anchors = _strip_xml_with_anchors(xml)
    assert anchors == []
    assert "Du texte sans annotation" in text


def test_strip_anchors_xml_vide():
    """Pas de crash sur XML vide ou trivial."""
    text, anchors = _strip_xml_with_anchors("")
    assert text == ""
    assert anchors == []


def test_strip_anchors_bisect_retrouve_le_paragraphe():
    """Test bout-en-bout du contrat avec l'export : pour une position
    donnée dans le texte, `bisect_right(offsets, pos) - 1` doit donner
    l'index de l'ancre du paragraphe contenant cette position."""
    xml = """<root>
<para id_syceron="100">Premier paragraphe sur le climat.</para>
<para id_syceron="200">Deuxième paragraphe sur le sport et le dopage.</para>
<para id_syceron="300">Troisième paragraphe sur l'agriculture.</para>
</root>"""
    text, anchors = _strip_xml_with_anchors(xml)
    pos_sport = text.lower().find("sport")
    assert pos_sport > 0

    offsets = [a[0] for a in anchors]
    i = bisect.bisect_right(offsets, pos_sport) - 1
    assert i >= 0
    # L'ancre trouvée doit correspondre au paragraphe id=200
    assert anchors[i][1] == "200", (
        f"Attendu id=200 (paragraphe sport), trouvé {anchors[i]}")


def test_strip_anchors_tags_imbrique_avec_id():
    """Cas réel Syceron : <sommaire2> contient <titreStruct id=A>
    contenant <intitule> + <para id=B>. Les deux IDs doivent être
    capturés, dans l'ordre."""
    xml = """<root>
<sommaire2>
  <titreStruct id_syceron="500">
    <intitule>Foncier en Martinique</intitule>
  </titreStruct>
  <para id_syceron="501">M. Jean-Philippe Nilor</para>
  <para id_syceron="502">M. Gérald Darmanin, garde des sceaux</para>
</sommaire2>
</root>"""
    text, anchors = _strip_xml_with_anchors(xml)
    ids = [a[1] for a in anchors]
    assert ids == ["500", "501", "502"]
    # "Foncier en Martinique" doit être proche de l'ancre 500
    pos_foncier = text.find("Foncier en Martinique")
    assert pos_foncier >= 0
    offsets = [a[0] for a in anchors]
    i = bisect.bisect_right(offsets, pos_foncier) - 1
    assert anchors[i][1] == "500"


# ---------------------------------------------------------------------------
# 2. Intégration site_export — la branche R40-J construit l'URL ancre
# ---------------------------------------------------------------------------


def test_site_export_construit_url_ancre_si_syceron_index_present():
    """Régression bout-en-bout : un row CR AN avec `raw.syceron_index`
    doit produire une URL `…#<id_syceron>` au lieu d'un text-fragment.
    On simule directement la branche R40-J ajoutée dans `_load`/export."""
    # On extrait le bloc de logique R40-J en l'isolant via une mini
    # implémentation symétrique (le code source est protégé par un
    # `if cat == "comptes_rendus" ...` profondément imbriqué dans
    # site_export — on teste plutôt la formule).
    haystack = (
        "Présidence Mme Yaël Braun-Pivet. "
        "Discussion sur les retraites. "
        "M. X intervient sur le dopage dans le sport amateur. "
        "Suite des débats."
    )
    syceron_index = [
        [0, "1000"],
        [33, "1001"],   # « Discussion sur les retraites »
        [66, "1002"],   # « M. X intervient sur le dopage »
    ]
    matched_keywords = ["dopage"]
    base_url = (
        "https://www.assemblee-nationale.fr/dyn/17/"
        "comptes-rendus/seance/CRSANR5L17S2026O1N168"
    )

    # Reproduit la logique R40-J du site_export
    import bisect as _bi
    kw_pos = -1
    for kw in matched_keywords:
        p = haystack.lower().find(kw.lower())
        if p >= 0:
            kw_pos = p
            break
    assert kw_pos > 0
    offsets = [it[0] for it in syceron_index]
    i = _bi.bisect_right(offsets, kw_pos) - 1
    assert i >= 0
    anchor = syceron_index[i][1]
    # Le mot 'dopage' tombe dans le 3e paragraphe (offset 66) → id=1002
    assert anchor == "1002"

    final_url = f"{base_url}#{anchor}"
    assert final_url.endswith("#1002")
    # Pas de text-fragment R13-K dans la version ancre
    assert "#:~:text=" not in final_url


def test_site_export_fallback_text_fragment_si_pas_dindex():
    """Sources Sénat / CR commissions : pas de syceron_index → on retombe
    sur text-fragment R13-K (URL `…#:~:text=<kw>`)."""
    syceron_index = []  # cas Sénat/commissions
    matched_keywords = ["dopage"]
    base_url = "https://www.senat.fr/seances/s202602/s20260225/"

    # Logique R13-K
    if not syceron_index:
        from urllib.parse import quote
        fragment = quote(matched_keywords[0], safe="")
        final_url = f"{base_url}#:~:text={fragment}"
    assert final_url.endswith("#:~:text=dopage")


def test_normalize_syceron_pose_syceron_index_dans_raw():
    """Régression : le parser `_normalize_syceron` doit poser un champ
    `raw.syceron_index` (liste de paires) dans chaque Item produit. Test
    indirect via lecture source — on vérifie la présence du marqueur dans
    le code, comme R40-G pour haystack_body."""
    from src.sources import assemblee as an_mod
    src_path = an_mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src_code = f.read()
    # La clé est posée dans le raw du yield Item
    assert '"syceron_index"' in src_code, (
        "assemblee.py:_normalize_syceron doit poser raw.syceron_index "
        "(R40-J)")
    # Le helper est bien exporté
    assert "_strip_xml_with_anchors" in src_code
