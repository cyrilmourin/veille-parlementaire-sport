"""Tests ciblés sur `_normalize_amendement` (src/sources/assemblee.py).

Régressions couvertes :

* R23-A (2026-04-23) — l'API AN renvoie `cycleDeVie.sort` comme STRING
  directe (ex : "Tombé", "Adopté"), pas comme dict `{libelle: ...}`.
  L'ancien path `cycleDeVie.sort.libelle` ne matchait JAMAIS → tous les
  amendements affichaient `etat` (transitoire, souvent "Discuté") à la
  place du sort final. On vérifie :
    1. le parser lit bien le sort string-forme
    2. le parser expose `raw.sous_etat` depuis
       `cycleDeVie.etatDesTraitements.sousEtat.libelle`
    3. la forme legacy dict `{libelle: ...}` reste supportée (fallback)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.sources.assemblee import _normalize_amendement  # noqa: E402


def _base_amendement(sort_node, etat_node, sous_etat_node=None):
    """Fabrique un JSON d'amendement AN minimal mais valide pour le parser."""
    et = {"etat": etat_node}
    if sous_etat_node is not None:
        et["sousEtat"] = sous_etat_node
    return {
        "amendement": {
            "uid": "AMANR5L17PO59048B1234P0D1N123",
            "identification": {"numeroLong": "123"},
            "signataires": {
                "auteur": {
                    "acteurRef": "PA123456",
                    "groupePolitiqueRef": "PO800490",
                }
            },
            "cycleDeVie": {
                "sort": sort_node,
                "etatDesTraitements": et,
                "dateDepot": "2026-04-20",
            },
            "corps": {
                "contenuAuteur": {
                    "dispositif": "Texte dispositif",
                    "exposeSommaire": "Texte exposé",
                }
            },
            "pointeurFragmentTexte": {
                "division": {"articleDesignation": "art. 5"}
            },
            "texteLegislatifRef": "PIONANR5L17BTC2335",
        }
    }


def _run(obj, *, organe_map: dict[tuple[str, bool], str] | None = None,
         groupe_ref: str = ""):
    """Exécute le parser avec des amo_loader mockés.

    `organe_map` permet d'imiter les deux formes du libellé (abrégée vs
    longue) : clé = (po_uid, prefer_long) → valeur = libellé à retourner.
    """
    src = {"id": "an_amendements"}

    def _fake_organe(po_uid, *, prefer_long=True):
        if organe_map is None:
            return "Groupe Exemple"
        return organe_map.get((po_uid, prefer_long), "")

    with patch("src.sources.assemblee.amo_loader.resolve_acteur",
               return_value="Mme Exemple"), \
         patch("src.sources.assemblee.amo_loader.resolve_groupe",
               return_value="GROUP"), \
         patch("src.sources.assemblee.amo_loader.resolve_groupe_ref",
               return_value=groupe_ref), \
         patch("src.sources.assemblee.amo_loader.resolve_organe",
               side_effect=_fake_organe), \
         patch("src.sources.assemblee.amo_loader.resolve_texte_dossier",
               return_value="Dossier exemple"):
        return list(_normalize_amendement(obj, src, "amendements"))


# ---------- R23-A : sort en forme STRING (API AN 2026) --------------------

def test_parser_reads_sort_as_string_form():
    """API AN actuelle : `cycleDeVie.sort` est directement une string."""
    obj = _base_amendement(
        sort_node="Tombé",
        etat_node={"libelle": "Discuté"},
        sous_etat_node={"libelle": "Tombé"},
    )
    items = _run(obj)
    assert len(items) == 1
    raw = items[0].raw
    assert raw["sort"] == "Tombé"
    assert raw["etat"] == "Discuté"
    assert raw["sous_etat"] == "Tombé"
    # Le statut (utilisé dans le summary) doit aussi refléter le sort.
    assert raw["statut"] == "Tombé"


def test_parser_reads_sort_adopte_string():
    obj = _base_amendement(
        sort_node="Adopté",
        etat_node={"libelle": "Discuté"},
    )
    items = _run(obj)
    assert items[0].raw["sort"] == "Adopté"


# ---------- Fallback : sort forme dict legacy -----------------------------

def test_parser_falls_back_on_legacy_dict_form():
    """Rétro-compatibilité : ancien format `{libelle: ...}`."""
    obj = _base_amendement(
        sort_node={"libelle": "Retiré"},
        etat_node={"libelle": "Discuté"},
    )
    items = _run(obj)
    assert items[0].raw["sort"] == "Retiré"


# ---------- Sort vide → raw.sort == "" (pas de fausse valeur) -------------

def test_parser_empty_sort_leaves_raw_sort_empty():
    """Amendement en cours : sort absent → raw.sort vide (mais etat/sousEtat
    peuvent prendre le relais côté chip au rendu)."""
    obj = _base_amendement(
        sort_node=None,
        etat_node={"libelle": "Discuté"},
        sous_etat_node=None,
    )
    items = _run(obj)
    raw = items[0].raw
    assert raw["sort"] == ""
    assert raw["etat"] == "Discuté"


# ---------- sous_etat persisté quand présent ------------------------------

def test_parser_persists_sous_etat_from_etat_des_traitements():
    obj = _base_amendement(
        sort_node=None,
        etat_node={"libelle": "Discuté"},
        sous_etat_node={"libelle": "Adopté sans modif"},
    )
    items = _run(obj)
    raw = items[0].raw
    assert raw["sous_etat"] == "Adopté sans modif"
    # Le statut summary doit tomber sur sous_etat si sort est vide.
    assert raw["statut"] == "Adopté sans modif"


def test_parser_sous_etat_empty_when_absent():
    obj = _base_amendement(
        sort_node="Tombé",
        etat_node={"libelle": "Discuté"},
        sous_etat_node=None,
    )
    items = _run(obj)
    assert items[0].raw["sous_etat"] == ""


# ---------- R23-B : groupe long persisté pour tooltip hover ----------------

def test_parser_persists_groupe_long_from_po_direct():
    """Quand signataires.auteur.groupePolitiqueRef est un POxxx, on résout
    l'abrégé (pour le sigle affiché) ET le libellé long (pour le tooltip)."""
    obj = _base_amendement(
        sort_node="Adopté",
        etat_node={"libelle": "Discuté"},
    )
    # groupePolitiqueRef = PO800490 dans le fixture.
    items = _run(
        obj,
        organe_map={
            ("PO800490", False): "LFI-NFP",
            ("PO800490", True): "La France insoumise - Nouveau Front Populaire",
        },
    )
    raw = items[0].raw
    assert raw["groupe"] == "LFI-NFP"
    assert raw["groupe_long"] == (
        "La France insoumise - Nouveau Front Populaire"
    )


def test_parser_persists_groupe_long_via_cache_ref():
    """Quand le PO n'est pas fourni directement, on utilise
    resolve_groupe_ref(PA) pour retrouver le PO via le cache AMO."""
    # On simule un JSON sans groupePolitiqueRef brut (champ vide).
    obj = _base_amendement(
        sort_node="Adopté",
        etat_node={"libelle": "Discuté"},
    )
    # Écrase le groupePolitiqueRef pour forcer le chemin resolve_groupe_ref.
    obj["amendement"]["signataires"]["auteur"]["groupePolitiqueRef"] = ""
    items = _run(
        obj,
        organe_map={
            ("PO730964", True): "Les Républicains",
        },
        groupe_ref="PO730964",
    )
    raw = items[0].raw
    # Sigle abrégé vient de resolve_groupe (mock="GROUP"), libellé long
    # est bien celui attendu.
    assert raw["groupe_long"] == "Les Républicains"


def test_parser_groupe_long_empty_when_cache_has_no_ref():
    """Cache AMO pas encore rafraîchi → groupe_ref vide → groupe_long vide.
    Le pipeline reste tolérant (pas d'exception, pas de tooltip)."""
    obj = _base_amendement(
        sort_node="",
        etat_node={"libelle": "Discuté"},
    )
    obj["amendement"]["signataires"]["auteur"]["groupePolitiqueRef"] = ""
    items = _run(obj, organe_map={}, groupe_ref="")
    assert items[0].raw["groupe_long"] == ""


# ---------- R23-C : photo portrait AN persistée dans raw ------------------

def test_parser_persists_auteur_photo_url_from_pa_ref():
    """PA123456 → URL déterministe AN, persistée dans raw pour que
    site_export l'expose en frontmatter.

    R23-C2 (2026-04-23) : le pattern historique /tribun/{LEG}/photos/{N}.jpg
    renvoyait 404 (domaine migré). Pattern réel observé :
    /dyn/static/tribun/{LEG}/photos/carre/{N}.jpg.
    """
    obj = _base_amendement(
        sort_node="Adopté",
        etat_node={"libelle": "Discuté"},
    )
    items = _run(obj)
    raw = items[0].raw
    assert raw["auteur_photo_url"] == (
        "https://www.assemblee-nationale.fr/dyn/static/tribun/17/photos/carre/123456.jpg"
    )


def test_parser_auteur_photo_url_empty_when_no_auteur_ref():
    """Sans acteurRef (amendement gouvernement ou mal-formé) →
    auteur_photo_url vide (pipeline tolérant, onerror masque côté template)."""
    obj = _base_amendement(
        sort_node="Adopté",
        etat_node={"libelle": "Discuté"},
    )
    obj["amendement"]["signataires"]["auteur"]["acteurRef"] = ""
    items = _run(obj)
    assert items[0].raw["auteur_photo_url"] == ""
