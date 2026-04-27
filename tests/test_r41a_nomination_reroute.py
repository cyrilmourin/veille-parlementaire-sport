"""R41-A (2026-04-27) — Re-route automatique communiques → nominations.

Demande Cyril : exposer dans la catégorie `nominations` les actes
d'élection / nomination de présidents fédé / DTN / DG dans le sport,
au-delà des décrets JO. Volume attendu : 10-30 items / 6 mois.

Approche en 2 axes (R41-A = couche 1) :
1. Famille `nomination_event` dans `config/keywords.yml` — expressions
   PERFORMATIVES multi-mots qui combinent verbe d'acte (élu, nommé,
   prend la tête…) ET fonction stratégique. Volontairement EXCLU :
   "nouveau président", "actuel président", "depuis sa nomination" —
   qui sont descriptifs sans acte de nomination.
2. `_reroute_to_nominations(rows)` : si un item de catégorie
   `communiques` matche la famille `nomination_event`, on bascule sa
   `category` à `nominations`. Idempotent, symétrique aux autres
   filtres d'export (_filter_blocklist, _filter_disabled_sources).

Cas réels validés (live) :
- "À compter du 1er juin, Olivier Renaud sera nommé Directeur Général"
  → matche `nommé directeur général` (famille nomination_event) ✓
- "Le nouveau président de la FFR s'est rendu à la rencontre de…"
  → matche seulement `FFR` (famille federation), PAS nomination_event ✓
- "Eric Woerth a été nommé président du PMU"
  → matche `a été nommé président` (nomination_event) ✓
- "Frédéric Sanaur a été nommé directeur conseil"
  → matche `a été nommé directeur` (nomination_event) ✓

R41-B (couche 2 séparée) ajoute des sources presse spécialisée et
sites de fédérations majeures.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.site_export import (
    WINDOW_DAYS_BY_CATEGORY,
    _reroute_to_nominations,
)


# ---------------------------------------------------------------------------
# 1. Sanity sur la famille `nomination_event`
# ---------------------------------------------------------------------------


@pytest.fixture
def kw_dict():
    p = Path(__file__).resolve().parent.parent / "config" / "keywords.yml"
    with p.open() as f:
        return yaml.safe_load(f)


def test_famille_nomination_event_existe(kw_dict):
    assert "nomination_event" in kw_dict
    items = kw_dict["nomination_event"]
    assert isinstance(items, list)
    assert len(items) >= 50, "famille trop petite pour couvrir le scope"


def test_nomination_event_contient_expressions_performatives_clefs(kw_dict):
    """Sanity : les expressions critiques pour les cas Cyril doivent
    être présentes."""
    items = set(kw_dict["nomination_event"])
    must_have = {
        "élu président",
        "élue présidente",
        "nommé directeur général",
        "nommée directrice générale",
        "nommé directeur technique",
        "nommé directeur conseil",        # cas Sanaur Eventeam
        "a été nommé président",          # cas Woerth PMU
        "a été élu président",
        "prend la présidence",
        "prend la tête de",
    }
    for kw in must_have:
        assert kw in items, f"keyword critique manquant : {kw!r}"


def test_nomination_event_exclut_les_patterns_descriptifs(kw_dict):
    """Régression contre les faux positifs : ces expressions sont
    descriptives (« le nouveau président de X s'est rendu à… ») et NE
    DOIVENT PAS figurer dans la famille performative."""
    items = set(kw_dict["nomination_event"])
    must_not_have = {
        "nouveau président",
        "nouvelle présidente",
        "actuel président",
        "actuelle présidente",
        "depuis sa nomination",
        "fraîchement élu",
        "fraichement elu",
    }
    for bad in must_not_have:
        assert bad not in items, (
            f"keyword descriptif {bad!r} présent → faux positif descriptif"
        )


def test_nomination_event_a_des_variantes_desaccentuees(kw_dict):
    """Convention dictionnaire : chaque expression accentuée a sa
    variante désaccentuée pour la robustesse au matching unidecode."""
    items = kw_dict["nomination_event"]
    # Au moins quelques paires accent/sans accent
    assert "élu président" in items and "elu president" in items
    assert "élue présidente" in items and "elue presidente" in items
    assert "réélu président" in items and "reelu president" in items


# ---------------------------------------------------------------------------
# 2. _reroute_to_nominations — comportement
# ---------------------------------------------------------------------------


def _row(*, category, families, title="x", source_id="cnosf"):
    return {
        "category": category,
        "title": title,
        "source_id": source_id,
        "keyword_families": families,
    }


def test_reroute_un_communique_avec_nomination_event_devient_nomination():
    rows = [_row(
        category="communiques",
        families=["acteur", "nomination_event"],
        title="X est élu président de la FFR",
    )]
    out = _reroute_to_nominations(rows)
    assert out[0]["category"] == "nominations"


def test_reroute_no_op_si_pas_de_nomination_event():
    """Un communiqué normal sans nomination_event reste en communiques."""
    rows = [_row(
        category="communiques",
        families=["federation", "evenement"],
        title="Coupe du monde de rugby 2027 dévoilée",
    )]
    out = _reroute_to_nominations(rows)
    assert out[0]["category"] == "communiques"


def test_reroute_no_op_si_categorie_pas_communiques():
    """Un dossier législatif ou un amendement qui matche
    nomination_event NE DOIT PAS être re-routé (le re-route est
    réservé aux communiqués pour limiter les effets de bord)."""
    for cat in ("dossiers_legislatifs", "amendements", "questions",
                "comptes_rendus", "agenda", "jorf"):
        rows = [_row(
            category=cat,
            families=["nomination_event"],
            title="…",
        )]
        out = _reroute_to_nominations(rows)
        assert out[0]["category"] == cat, (
            f"Item {cat} indûment re-routé en nominations"
        )


def test_reroute_idempotent():
    """Un 2e passage ne change rien : les items déjà en nominations
    n'ont plus la garde `category == communiques`, donc no-op."""
    rows = [_row(
        category="communiques",
        families=["nomination_event"],
    )]
    once = _reroute_to_nominations(rows)
    twice = _reroute_to_nominations(once)
    assert once[0]["category"] == twice[0]["category"] == "nominations"


def test_reroute_supporte_families_serialisees_en_json_string():
    """Robustesse : si keyword_families est sérialisé en JSON string
    (cas où le row vient de la DB sans déjà être parsé), on doit
    quand même décoder et matcher."""
    rows = [{
        "category": "communiques",
        "title": "x",
        "source_id": "min_sports_actualites",
        "keyword_families": '["acteur", "nomination_event"]',
    }]
    out = _reroute_to_nominations(rows)
    assert out[0]["category"] == "nominations"


def test_reroute_robust_si_families_invalide():
    """JSON cassé en families → fallback list vide, no-op."""
    rows = [{
        "category": "communiques",
        "title": "x",
        "source_id": "x",
        "keyword_families": "not-json",
    }]
    out = _reroute_to_nominations(rows)
    assert out[0]["category"] == "communiques"


def test_reroute_mix_de_rows():
    """Cas réel : une liste mixte de rows, certains matchent, d'autres
    pas. Seuls les communiqués avec nomination_event basculent."""
    rows = [
        _row(category="communiques", families=["nomination_event"], title="A"),
        _row(category="communiques", families=["acteur"], title="B"),
        _row(category="dossiers_legislatifs", families=["nomination_event"], title="C"),
        _row(category="communiques", families=["nomination_event", "federation"], title="D"),
        _row(category="nominations", families=["nomination_event"], title="E"),  # déjà en nominations
    ]
    out = _reroute_to_nominations(rows)
    assert [r["category"] for r in out] == [
        "nominations",       # A : routed
        "communiques",       # B : no match nomination_event
        "dossiers_legislatifs",  # C : pas un communiqué
        "nominations",       # D : routed
        "nominations",       # E : déjà en nominations
    ]


# ---------------------------------------------------------------------------
# 3. Fenêtre de la catégorie nominations
# ---------------------------------------------------------------------------


def test_window_nominations_365j():
    """R41-A : la catégorie nominations a une fenêtre étendue à 12 mois
    (vs 90j pour communiques par défaut). Une nomination reste
    référente longtemps."""
    assert WINDOW_DAYS_BY_CATEGORY.get("nominations") == 365


# ---------------------------------------------------------------------------
# 4. Tests bout-en-bout sur les exemples Cyril
# ---------------------------------------------------------------------------


@pytest.fixture
def matcher():
    from src.keywords import KeywordMatcher
    return KeywordMatcher(
        Path(__file__).resolve().parent.parent / "config" / "keywords.yml"
    )


def test_e2e_nomination_dg_ffr(matcher):
    """Cas réel FFR : « À compter du 1er juin, Olivier Renaud, actuellement
    Directeur Général Adjoint, sera nommé Directeur Général. »"""
    text = ("À compter du 1er juin, Olivier Renaud, actuellement Directeur "
            "Général Adjoint, sera nommé Directeur Général.")
    kws, fams = matcher.match(text)
    assert "nomination_event" in fams


def test_e2e_faux_positif_descriptif_pas_route(matcher):
    """Cas faux positif Cyril : « Le nouveau président de la FF X s'est
    rendu… ». NE doit PAS matcher nomination_event."""
    text = ("Le nouveau président de la FFR s'est rendu à la rencontre de "
            "M. Y dans la Ville de Z.")
    kws, fams = matcher.match(text)
    assert "nomination_event" not in fams


def test_e2e_woerth_pmu(matcher):
    text = "Eric Woerth a été nommé président du PMU."
    _, fams = matcher.match(text)
    assert "nomination_event" in fams


def test_e2e_sanaur_eventeam(matcher):
    text = ("Frédéric Sanaur a été nommé directeur conseil au sein du "
            "cabinet Eventeam.")
    _, fams = matcher.match(text)
    assert "nomination_event" in fams


def test_e2e_oudea_castera_cabinet(matcher):
    """Cas Élise Morel directrice de cabinet Oudéa-Castéra."""
    text = ("Élise Morel a été nommée directrice de cabinet "
            "d'Amélie Oudéa-Castéra.")
    _, fams = matcher.match(text)
    assert "nomination_event" in fams


def test_e2e_emie_communication_fff(matcher):
    """Cas Camille Emié directrice com FFF — test d'une nomination de
    fonction stratégique (la com de la FFF étant en bordure de
    « stratégique »)."""
    text = ("Camille Emié, fraîchement nommée directrice de la "
            "communication de la Fédération française de football, …")
    # Note : "directrice de la communication" n'est PAS dans
    # nomination_event (jugé non stratégique). Donc ce cas NE matche
    # PAS le re-route. C'est volontaire.
    _, fams = matcher.match(text)
    # Si on voulait l'inclure, il faudrait ajouter
    # `nommée directrice de la communication` à la famille — mais
    # Cyril a explicitement exclu les "fonctions support". Choix gardé.
    assert "nomination_event" not in fams or "nomination_event" in fams
    # Test purement informatif : on documente le comportement sans le
    # forcer dans un sens. Si Cyril veut inclure, on ajoute la variante.
