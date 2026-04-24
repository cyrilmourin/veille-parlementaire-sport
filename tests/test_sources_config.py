"""Tests de cohérence `config/sources.yml` (R15, 2026-04-22).

Garde-fous minimaux, pas un test d'intégration réseau :
- Chaque source a un `id` unique (évite les collisions hash_key).
- Le `format` est toujours dans la liste des formats gérés.
- Les AAI cœur de cible sport (ANS, AFLD, ARCOM, ANJ) sont présentes et
  actives (régression ops typique : quelqu'un passe enabled:false sans
  prévenir → le digest perd 200+ items/sem).
- Les 3 hautes juridictions (CE, CC, Cassation) sont configurées.
- Tout le groupe `ministeres` a `category` renseigné (sinon le matcher
  refuse de router vers Follaw).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src import normalize  # noqa: E402


_KNOWN_FORMATS = {
    # Parsers dédiés
    "json_zip", "xml_zip", "csv", "csv_zip",
    "akn_index", "akn_discussion",
    "dila_jorf",
    "sitemap", "rss", "html",
    "senat_agenda_daily",
    "data_gouv_agenda",
    "min_sports_agenda_hebdo",
    # R16 (2026-04-22) — parser dédié agenda Élysée (HTML DSFR)
    "elysee_agenda_html",
    # R28 (2026-04-23) — parser dédié rapports AN (HTML listing)
    "an_rapports_html",
    # R35-B (2026-04-24) — scraper CR commissions AN (HTML + PDF pypdf)
    "an_cr_commissions",
}


@pytest.fixture(scope="module")
def cfg():
    path = _ROOT / "config" / "sources.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _iter_sources(cfg):
    for group_name, group in cfg.items():
        if not isinstance(group, dict):
            continue
        for src in (group.get("sources") or []):
            yield group_name, src


def test_source_ids_are_unique(cfg):
    ids = [s["id"] for _g, s in _iter_sources(cfg)]
    dup = [x for x in set(ids) if ids.count(x) > 1]
    assert not dup, f"source_id dupliqués : {dup}"


def test_all_formats_known(cfg):
    for _g, s in _iter_sources(cfg):
        fmt = s.get("format")
        assert fmt in _KNOWN_FORMATS, (
            f"source {s['id']} utilise un format inconnu : {fmt!r}"
        )


def test_all_sources_have_category(cfg):
    for _g, s in _iter_sources(cfg):
        cat = s.get("category")
        assert cat, f"source {s['id']} sans category (bloque le matcher)"


def test_all_sources_have_url(cfg):
    for _g, s in _iter_sources(cfg):
        url = s.get("url", "")
        assert url.startswith("http"), (
            f"source {s['id']} URL invalide : {url!r}"
        )


def test_aai_sport_sources_present_and_enabled(cfg):
    """Régression : les 4 AAI cœur de cible sport doivent rester actives.

    ANS (Agence nationale du Sport), AFLD (anti-dopage), ARCOM (droits
    TV sport, paris sportifs), ANJ (paris sportifs, sport betting) sont
    les 4 AAI dont Cyril scrute les communiqués. Si l'une est
    désactivée, le test échoue pour forcer une review.
    """
    must_be_active = {"ans", "afld", "arcom", "anj"}
    active_ids = {
        s["id"] for _g, s in _iter_sources(cfg)
        if s.get("enabled") is not False
    }
    missing = must_be_active - active_ids
    assert not missing, (
        f"AAI sport désactivées ou absentes : {missing} — "
        "si c'est intentionnel, retirer du test"
    )


def test_high_jurisdictions_configured(cfg):
    """Les hautes juridictions dans le scope doivent avoir une entrée YAML.
    R22 (2026-04-23) : Cour de cassation retirée du scope (site JS-only
    insurmontable, aucun flux RSS exposé côté officiel). Cf. AUDIT_R19.md §7.
    """
    all_ids = {s["id"] for _g, s in _iter_sources(cfg)}
    assert "conseil_etat" in all_ids
    assert "conseil_constit_actualites" in all_ids
    assert "conseil_constit_decisions" in all_ids
    # Cour de cassation volontairement non listée (R22).


def test_dispatch_covers_all_enabled_sources(cfg):
    """Chaque source active doit avoir un handler qui ne raise PAS à
    la résolution (routage). On ne fetch pas — on vérifie juste que le
    dispatcher retourne un callable connu."""
    for group, src in _iter_sources(cfg):
        if src.get("enabled") is False:
            continue
        fn = normalize._dispatch(group, src)
        assert callable(fn), f"pas de handler pour {src['id']}"


def test_r23ij_insep_fdsf_present(cfg):
    """R23-I + R23-J (2026-04-23) — opérateurs et fondations sport ajoutés.

    - INSEP : établissement public MinSports, flux RSS Drupal /fr/actualites.xml,
      poids 3 (cœur de cible).
    - FDSF  : fondation reconnue d'utilité publique adossée au CNOSF, flux
      RSS Squarespace via ?format=rss sur le blog /web/fsf/actualites.

    Garde-fou : régression si l'un est désactivé ou si quelqu'un change
    l'URL sans vérifier. Les deux doivent rester en `format: rss` (pas
    scrape HTML — le RSS officiel est beaucoup plus fiable).
    """
    by_id = {s["id"]: s for _g, s in _iter_sources(cfg)}
    assert "insep" in by_id, "source insep manquante (R23-I)"
    assert "fdsf" in by_id, "source fdsf manquante (R23-J)"
    insep = by_id["insep"]
    fdsf = by_id["fdsf"]
    assert insep.get("format") == "rss", (
        "insep doit rester en format rss (/fr/actualites.xml est un RSS "
        "Drupal natif, pas besoin de scrape HTML)"
    )
    assert fdsf.get("format") == "rss", (
        "fdsf doit rester en format rss (?format=rss est le feed Squarespace "
        "natif, pas besoin de scrape HTML du listing rendu en JS)"
    )
    assert insep.get("enabled", True) is not False, "insep désactivé"
    assert fdsf.get("enabled", True) is not False, "fdsf désactivé"
    # L'URL INSEP DOIT finir par .xml (c'est le suffixe magique Drupal
    # Views pour export RSS — /fr/actualites tout court renvoie le HTML).
    assert insep["url"].endswith(".xml"), (
        f"insep URL doit finir par .xml : {insep['url']!r}"
    )
    # L'URL FDSF DOIT contenir `?format=rss` (suffixe magique Squarespace).
    assert "format=rss" in fdsf["url"], (
        f"fdsf URL doit contenir format=rss : {fdsf['url']!r}"
    )


def test_r23i_insep_chamber_mapping():
    """R23-I : le domaine insep.fr doit mapper vers le badge 'INSEP'.

    Sans ce mapping explicite, _chamber() retomberait sur le fallback
    domaine (qui n'est pas .gouv.fr) → badge "Insep.fr" parasite."""
    from src.sources.html_generic import _chamber
    assert _chamber("www.insep.fr") == "INSEP"
    assert _chamber("insep.fr") == "INSEP"


def test_r23j_fdsf_chamber_mapping():
    """R23-J : le domaine fondation-du-sport-francais.fr → 'FDSF'."""
    from src.sources.html_generic import _chamber
    assert _chamber("www.fondation-du-sport-francais.fr") == "FDSF"
    assert _chamber("fondation-du-sport-francais.fr") == "FDSF"


def test_senat_agenda_uses_daily_format(cfg):
    """R15 : senat_agenda DOIT être en format `senat_agenda_daily`.
    L'ancien `format: html` tombait sur la SPA AngularJS (0 item)."""
    senat_sources = {s["id"]: s for _g, s in _iter_sources(cfg)
                     if _g == "senat"}
    agenda = senat_sources.get("senat_agenda")
    assert agenda is not None, "senat_agenda absent"
    assert agenda["format"] == "senat_agenda_daily", (
        f"senat_agenda doit utiliser senat_agenda_daily, pas "
        f"{agenda['format']!r}"
    )
