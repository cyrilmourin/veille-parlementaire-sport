"""R40-A (2026-04-26) — extraction propre de `libelleActe` (dict ou string).

Contexte : le dump open data AN sérialise `libelleActe` comme un dict
`{nomCanonique, libelleCourt}` dans les versions récentes du dump. Avant
ce patch, le parser faisait `str(acte.get("libelleActe") or "")` qui
produisait la repr Python du dict (`"{'nomCanonique': '...'}"`), ce qui :

- empoisonnait `actes_timeline.libelle` (texte technique inutilisable côté UI)
- empoisonnait `raw.libelle_acte` (idem)
- empoisonnait `libelles_haystack` du matcher mots-clés (R36-E) qui
  voyait du JSON-string au lieu de libellés humains, dégradant la
  couverture de matching pour les dossiers à titre générique.

Cas concret reproduit ici : dossier laïcité PIONANR5L17B0509 retiré le
2025-07-09 (UID DLR5L17N50771), dont le JSON open data contient bien
l'acte `RetraitInitiative_Type` avec `libelleActe = {nomCanonique:
"Retrait d'une initiative", libelleCourt: "Retrait d'une initiative"}`.
"""
from __future__ import annotations

from src.sources.assemblee import _libelle_acte_text, _normalize_dosleg


# ---------------------------------------------------------------------------
# 1. Helper _libelle_acte_text — comportement isolé
# ---------------------------------------------------------------------------


def test_libelle_text_dict_priorite_nomCanonique():
    node = {"nomCanonique": "Retrait d'une initiative",
            "libelleCourt": "Retrait"}
    assert _libelle_acte_text(node) == "Retrait d'une initiative"


def test_libelle_text_dict_fallback_libelleCourt():
    node = {"nomCanonique": "", "libelleCourt": "Renvoi commission"}
    assert _libelle_acte_text(node) == "Renvoi commission"


def test_libelle_text_dict_vide_retourne_chaine_vide():
    assert _libelle_acte_text({}) == ""
    assert _libelle_acte_text({"nomCanonique": "", "libelleCourt": ""}) == ""


def test_libelle_text_string_legacy_preservee():
    """Un dump ancien (rare mais possible) avec libelleActe = string."""
    assert _libelle_acte_text("Dépôt") == "Dépôt"


def test_libelle_text_none_retourne_chaine_vide():
    assert _libelle_acte_text(None) == ""


def test_libelle_text_type_inattendu_retourne_chaine_vide():
    assert _libelle_acte_text(123) == ""
    assert _libelle_acte_text(["a", "b"]) == ""


def test_libelle_text_strip_si_seulement_espaces():
    assert _libelle_acte_text({"nomCanonique": "   "}) == ""
    assert _libelle_acte_text({"nomCanonique": "   ",
                               "libelleCourt": "OK"}) == "OK"


# ---------------------------------------------------------------------------
# 2. Régression sur le dossier laïcité réel (DLR5L17N50771)
# ---------------------------------------------------------------------------


def _laicite_dossier_json() -> dict:
    """Mini-fixture inline du dossier PPL n°509 laïcité dans le sport,
    retiré par son auteur le 2025-07-09. Reflète la structure réelle
    du dump open data AN au 2026-04-26 (libelleActe = dict)."""
    return {
        "dossierParlementaire": {
            "@xsi:type": "DossierLegislatif_Type",
            "uid": "DLR5L17N50771",
            "legislature": "17",
            "titreDossier": {
                "titre": ("renforcer le principe de laïcité dans les "
                          "compétitions sportives en interdisant le "
                          "port de tenues ou de signes ostensiblement "
                          "religieux"),
                "titreChemin": "renforcer_principe_laicite_competitions_sportives_signes_religieux",
                "senatChemin": None,
            },
            "procedureParlementaire": {
                "code": "2",
                "libelle": "Proposition de loi ordinaire",
            },
            "actesLegislatifs": {
                "acteLegislatif": {
                    "@xsi:type": "Etape_Type",
                    "uid": "L17-AN1-50771",
                    "codeActe": "AN1",
                    "libelleActe": {
                        "nomCanonique": "1ère lecture (1ère assemblée saisie)",
                        "libelleCourt": "1ère lecture",
                    },
                    "dateActe": None,
                    "actesLegislatifs": {
                        "acteLegislatif": [
                            {
                                "@xsi:type": "DepotInitiative_Type",
                                "uid": "L17-VD221876DI",
                                "codeActe": "AN1-DEPOT",
                                "libelleActe": {
                                    "nomCanonique": "1er dépôt d'une initiative.",
                                    "libelleCourt": "1er dépôt d'une initiative.",
                                },
                                "dateActe": "2024-10-29T00:00:00.000+01:00",
                                "texteAssocie": "PIONANR5L17B0509",
                            },
                            {
                                "@xsi:type": "SaisieComFond_Type",
                                "uid": "L17-VD221877CFS",
                                "codeActe": "AN1-COM-FOND-SAISIE",
                                "libelleActe": {
                                    "nomCanonique": "Renvoi en commission au fond",
                                    "libelleCourt": "Renvoi en commission au fond",
                                },
                                "dateActe": "2024-10-29T00:00:00.000+01:00",
                            },
                            {
                                "@xsi:type": "RetraitInitiative_Type",
                                "uid": "L17-VD227136",
                                "codeActe": "AN1-RTRINI",
                                "libelleActe": {
                                    "nomCanonique": "Retrait d'une initiative",
                                    "libelleCourt": "Retrait d'une initiative",
                                },
                                "dateActe": "2025-07-09T00:00:00.000+02:00",
                                "texteAssocie": "PIONANR5L17B0509",
                            },
                        ],
                    },
                },
            },
        },
    }


def _normalize(monkeypatch_now=None):
    src = {"id": "an_dossiers_legislatifs",
           "category": "dossiers_legislatifs"}
    return list(_normalize_dosleg(_laicite_dossier_json(), src,
                                   "dossiers_legislatifs"))


def test_dossier_laicite_actes_timeline_non_vide():
    """Le dossier laïcité doit produire une timeline avec 3 actes utiles
    (dépôt + renvoi commission + retrait), pas une liste vide."""
    items = _normalize()
    assert len(items) == 1
    timeline = items[0].raw.get("actes_timeline")
    assert isinstance(timeline, list)
    assert len(timeline) == 3


def test_dossier_laicite_libelles_humains_pas_repr_dict():
    """Régression du bug `str(libelleActe)` qui produisait la repr Python
    du dict. Les libellés stockés dans la timeline doivent être les
    `nomCanonique` lisibles."""
    items = _normalize()
    timeline = items[0].raw["actes_timeline"]
    libelles = [a["libelle"] for a in timeline]
    assert "Retrait d'une initiative" in libelles
    assert "1er dépôt d'une initiative." in libelles
    assert "Renvoi en commission au fond" in libelles
    for lib in libelles:
        assert "{" not in lib, f"libelle pollué par repr dict : {lib!r}"
        assert "nomCanonique" not in lib


def test_dossier_laicite_is_retire_True():
    """`is_retire` doit être True grâce à la détection « retrait » dans le
    libellé propre (et non plus dans la repr du dict, ce qui marchait
    fortuitement avant le fix mais polluait le haystack)."""
    items = _normalize()
    assert items[0].raw.get("is_retire") is True


def test_dossier_laicite_published_at_date_du_retrait():
    """`published_at` doit être la date du dernier acte utile = 2025-07-09."""
    items = _normalize()
    pa = items[0].published_at
    assert pa is not None
    assert pa.date().isoformat() == "2025-07-09"


def test_dossier_laicite_libelles_haystack_propre():
    """R36-E — le `libelles_haystack` qui sert au matcher doit contenir
    les libellés humains, pas du JSON-string."""
    items = _normalize()
    haystack = items[0].raw.get("libelles_haystack", "")
    assert "Retrait d'une initiative" in haystack
    assert "Renvoi en commission au fond" in haystack
    assert "{" not in haystack, f"haystack pollué : {haystack[:200]!r}"
    assert "nomCanonique" not in haystack


def test_dossier_laicite_last_libelle_propre():
    """`raw.libelle_acte` (libellé du dernier acte utile, exposé en UI)
    doit aussi être un texte humain."""
    items = _normalize()
    last = items[0].raw.get("libelle_acte", "")
    assert "Retrait" in last
    assert "{" not in last


def test_dossier_laicite_3_actes_utiles():
    """nb_actes_utiles compte les actes datés et non ignorés."""
    items = _normalize()
    assert items[0].raw.get("nb_actes_utiles") == 3
