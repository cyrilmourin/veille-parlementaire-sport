"""R40-Q (2026-04-27) — Titre CR plénières AN par chapitre matché.

Avant R40-Q, le titre stocké pour un CR AN syceron était construit au
moment du parsing avec le 1er thème détecté en début de fichier — souvent
générique (« Présidence de … ») et sans rapport avec le passage qui
avait déclenché le match keyword. Demande Cyril 2026-04-27 : afficher
plutôt le chapitre du paragraphe matché.

Implémentation :
1. `_extract_syceron_chapters(xml)` : map `{id_syceron: titre}` extrait
   des `<titreStruct id_syceron="X"><intitule>TITRE</intitule>` du XML
2. `_normalize_syceron` stocke `raw.syceron_chapters` en plus de
   `raw.syceron_index` (R40-J), et fixe le titre à un format neutre
   « Séance AN du DD/MM/YYYY — séance plénière »
3. `_load` (export) résout dynamiquement le chapitre via
   `_resolve_syceron_chapter_title(haystack, kws, index, chapters)`
   et réécrit le titre. Si non résolvable, conserve le titre neutre.

Côté Sénat plénière : pas d'index XML aussi accessible (R40-K — chantier),
on s'en tient au titre neutre « Séance du DD MOIS YYYY — séance plénière ».
"""
from __future__ import annotations

from src.sources.assemblee import _extract_syceron_chapters
from src.site_export import _resolve_syceron_chapter_title


# ---------------------------------------------------------------------------
# 1. _extract_syceron_chapters — extraction des titres
# ---------------------------------------------------------------------------


def test_extract_chapters_titreStruct_basique():
    xml = """<root>
<titreStruct id_syceron="100"><intitule>Foncier en Martinique</intitule></titreStruct>
<titreStruct id_syceron="200"><intitule>Aéroport de Castres</intitule></titreStruct>
</root>"""
    out = _extract_syceron_chapters(xml)
    assert out == {"100": "Foncier en Martinique", "200": "Aéroport de Castres"}


def test_extract_chapters_strip_tags_internes():
    """L'intitule peut contenir <italique> ou <br/> — on strip les tags."""
    xml = """<root>
<titreStruct id_syceron="500"><intitule>Article 2 <italique>(seconde délibération)<br/></italique></intitule></titreStruct>
</root>"""
    out = _extract_syceron_chapters(xml)
    assert "500" in out
    title = out["500"]
    assert "<" not in title and ">" not in title
    assert "Article 2" in title and "seconde délibération" in title


def test_extract_chapters_decode_entites_basiques():
    xml = '<root><titreStruct id_syceron="1"><intitule>L&#39;avis de Mme S&amp;K</intitule></titreStruct></root>'
    out = _extract_syceron_chapters(xml)
    assert out["1"] == "L'avis de Mme S&K"


def test_extract_chapters_tronque_120c():
    long_title = "X" * 200
    xml = f'<root><titreStruct id_syceron="9"><intitule>{long_title}</intitule></titreStruct></root>'
    out = _extract_syceron_chapters(xml)
    assert len(out["9"]) <= 120


def test_extract_chapters_xml_sans_titreStruct_renvoie_dict_vide():
    xml = "<root><para>du texte sans titreStruct</para></root>"
    assert _extract_syceron_chapters(xml) == {}


def test_extract_chapters_xml_vide():
    assert _extract_syceron_chapters("") == {}


def test_extract_chapters_premier_intitule_seulement():
    """Si plusieurs <intitule> apparaissent dans un même <titreStruct>
    (cas rare avec sous-tags), on garde le premier."""
    xml = """<root>
<titreStruct id_syceron="1">
  <intitule>Premier titre</intitule>
  <para>blabla</para>
  <intitule>Deuxième titre dans le même bloc</intitule>
</titreStruct>
</root>"""
    out = _extract_syceron_chapters(xml)
    assert out["1"] == "Premier titre"


# ---------------------------------------------------------------------------
# 2. _resolve_syceron_chapter_title — résolution à l'export
# ---------------------------------------------------------------------------


def test_resolve_titre_chapitre_du_keyword_matche():
    """Cas classique : le keyword apparaît dans le 2e chapitre.
    `_resolve` doit retourner le titre du 2e chapitre."""
    haystack = (
        "Premier paragraphe sur l'agriculture. "
        "Deuxième sur les équipements sportifs et le dopage. "
        "Troisième sur le climat."
    )
    syceron_index = [
        [0, "100"],
        [40, "200"],   # offset où commence le bloc équipements sportifs
        [105, "300"],  # offset du climat
    ]
    syceron_chapters = {
        "100": "Agriculture",
        "200": "Sport et dopage",
        "300": "Climat",
    }
    title = _resolve_syceron_chapter_title(
        haystack, ["dopage"], syceron_index, syceron_chapters
    )
    assert title == "Sport et dopage"


def test_resolve_remonte_si_anchor_sans_chapitre():
    """Si le bisect tombe sur un id_syceron qui n'est pas un titre
    (ex. un `<para>` sans entrée dans `chapters`), on remonte au plus
    proche chapitre AVANT cet offset."""
    haystack = (
        "Chapitre A — Présentation. "
        "Intervenant 1 : M. Dupont parle du dopage. "
        "Intervenant 2 : Mme Martin."
    )
    syceron_index = [
        [0, "title-A"],     # titre du chapitre A
        [27, "para-1"],     # M. Dupont parle du dopage (sans chapitre dédié)
        [65, "para-2"],     # Mme Martin
    ]
    syceron_chapters = {"title-A": "Chapitre A — Présentation"}
    title = _resolve_syceron_chapter_title(
        haystack, ["dopage"], syceron_index, syceron_chapters
    )
    # Le mot 'dopage' tombe dans para-1 qui n'a pas de titre, on remonte
    # à title-A.
    assert title == "Chapitre A — Présentation"


def test_resolve_no_match_renvoie_chaine_vide():
    haystack = "Du texte sans le mot cible."
    syceron_index = [[0, "1"]]
    syceron_chapters = {"1": "Titre A"}
    title = _resolve_syceron_chapter_title(
        haystack, ["dopage"], syceron_index, syceron_chapters
    )
    assert title == ""


def test_resolve_structures_vides_renvoie_chaine_vide():
    """No-op si haystack vide / index vide / chapters vide / kws vide."""
    assert _resolve_syceron_chapter_title("", ["x"], [[0, "1"]], {"1": "A"}) == ""
    assert _resolve_syceron_chapter_title("texte", [], [[0, "1"]], {"1": "A"}) == ""
    assert _resolve_syceron_chapter_title("texte", ["x"], [], {"1": "A"}) == ""
    assert _resolve_syceron_chapter_title("texte", ["x"], [[0, "1"]], {}) == ""


def test_resolve_match_avant_premier_chapitre_no_op():
    """Si le keyword est avant le 1er id_syceron de l'index (cas edge :
    keyword dans le préambule avant le 1er <titreStruct>), on ne résout
    rien — on garde le titre neutre par défaut."""
    haystack = "préambule avec dopage avant le sommaire."
    syceron_index = [[100, "1"]]  # 1er anchor à offset 100, kw à offset 15
    syceron_chapters = {"1": "Titre A"}
    title = _resolve_syceron_chapter_title(
        haystack, ["dopage"], syceron_index, syceron_chapters
    )
    assert title == ""


def test_resolve_supporte_id_string_ou_int():
    """Le map `chapters` peut avoir des clés str (depuis JSON) ou int.
    On gère les deux pour robustesse aux changements de sérialisation."""
    haystack = "Texte avec dopage dedans."
    syceron_index = [[0, "42"]]
    # Cas 1 : clé str
    chapters_str = {"42": "Titre Alpha"}
    assert _resolve_syceron_chapter_title(
        haystack, ["dopage"], syceron_index, chapters_str
    ) == "Titre Alpha"


# ---------------------------------------------------------------------------
# 3. Régression : _normalize_syceron pose syceron_chapters
# ---------------------------------------------------------------------------


def test_normalize_syceron_pose_syceron_chapters():
    """`raw.syceron_chapters` doit être présent dans le code."""
    from src.sources import assemblee as an_mod
    src_path = an_mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src_code = f.read()
    assert '"syceron_chapters"' in src_code, (
        "_normalize_syceron doit poser raw.syceron_chapters (R40-Q)"
    )
    assert "_extract_syceron_chapters" in src_code


def test_titre_neutre_par_defaut_an_syceron():
    """Le titre par défaut (avant résolution dynamique à l'export) doit
    être neutre : « Séance AN du DD/MM/YYYY — séance plénière »."""
    from src.sources import assemblee as an_mod
    src_path = an_mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src_code = f.read()
    assert '"Séance AN du {published_at:%d/%m/%Y} — séance plénière"' in src_code, (
        "_normalize_syceron doit poser un titre neutre par défaut (R40-Q)"
    )


def test_titre_neutre_par_defaut_senat_plenary():
    from src.sources import senat as sen_mod
    src_path = sen_mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src_code = f.read()
    assert '"Séance du {date_label} — séance plénière"' in src_code, (
        "senat._fetch_debats_zip doit poser un titre neutre par défaut (R40-Q)"
    )
