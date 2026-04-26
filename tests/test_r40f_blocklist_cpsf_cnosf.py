"""R40-F (2026-04-26) — Sanity check blocklist : pages parasites CPSF + CNOSF.

Demande Cyril : exclure 12 pages "régions" du site france-paralympique.fr
(`/actualites//region/<region>` — note le double slash, bug de génération
côté Drupal CPSF reflété tel quel dans le listing) et 1 page statique du
CNOSF (`/comite-de-deontologie-du-cnosf`). Ces URLs ressortent en
catégorie `communiques` mais ne sont pas des actualités datées —
respectivement des index régionaux et une page de présentation
permanente.

Test : vérifier que `_load_blocklist()` charge bien les 13 URLs et que
le filtre `_filter_blocklist` les drop effectivement sur des rows
représentatifs.
"""
from __future__ import annotations

from src.site_export import _filter_blocklist, _load_blocklist


_REGIONS_CPSF = (
    "auvergne-rhone-alpes",
    "bourgogne-franche-comte",
    "bretagne",
    "centre-val-de-loire",
    "grand-est",
    "hauts-de-france",
    "ile-de-france",
    "normandie",
    "nouvelle-aquitaine",
    "occitanie",
    "pays-de-la-loire",
    "provence-alpes-cote-dazur",
)


def test_blocklist_contient_les_12_regions_cpsf():
    """Les 12 URLs régions CPSF (avec double slash `//region/`) sont
    bien dans la blocklist canonicalisée."""
    blocked_urls, _ = _load_blocklist()
    for region in _REGIONS_CPSF:
        canon = f"france-paralympique.fr/actualites//region/{region}"
        assert canon in blocked_urls, (
            f"Région CPSF {region!r} pas dans la blocklist : "
            f"attendu {canon!r}")


def test_blocklist_contient_page_cnosf_deontologie():
    blocked_urls, _ = _load_blocklist()
    assert ("cnosf.franceolympique.com/comite-de-deontologie-du-cnosf"
            in blocked_urls)


def test_filter_drops_regions_cpsf_et_garde_actu_legitime():
    """Régression bout-en-bout : 12 rows régions CPSF (à drop) + 1 row
    CNOSF (à drop) + 1 actualité légitime CPSF (à conserver)."""
    rows = []
    for region in _REGIONS_CPSF:
        rows.append({
            "source_id": "france_paralympique",
            "uid": f"region-{region}",
            "url": f"https://france-paralympique.fr/actualites//region/{region}",
            "title": f"Index région {region}",
            "category": "communiques",
            "raw": {},
        })
    rows.append({
        "source_id": "cnosf",
        "uid": "comite-deonto",
        "url": "https://cnosf.franceolympique.com/comite-de-deontologie-du-cnosf",
        "title": "Comité de déontologie CNOSF",
        "category": "communiques",
        "raw": {},
    })
    # Actualité légitime CPSF (à conserver)
    rows.append({
        "source_id": "france_paralympique",
        "uid": "actu-jop-paris",
        "url": "https://france-paralympique.fr/actualites/jop-paris-2024-medailles-record",
        "title": "JOP Paris 2024 : médailles record",
        "category": "communiques",
        "raw": {},
    })

    out = _filter_blocklist(rows)
    out_uids = [r["uid"] for r in out]
    # Toutes les régions doivent disparaître
    for region in _REGIONS_CPSF:
        assert f"region-{region}" not in out_uids, f"Région {region} pas filtrée"
    # La page CNOSF doit disparaître
    assert "comite-deonto" not in out_uids
    # L'actualité légitime doit rester
    assert "actu-jop-paris" in out_uids
    # Et c'est la SEULE qui reste
    assert len(out) == 1


def test_filter_garde_variantes_non_listees():
    """Une URL CPSF non listée (région inexistante / nouveau slug) ou
    une URL CNOSF d'actualité réelle doivent rester. Garde-fou contre
    une regex trop large."""
    rows = [
        {
            "source_id": "france_paralympique",
            "uid": "actu-recente",
            # URL d'actualité datée, pas un index région
            "url": "https://france-paralympique.fr/actualites/audition-anpc-2026",
            "title": "Audition ANPC 2026",
            "category": "communiques",
            "raw": {},
        },
        {
            "source_id": "cnosf",
            "uid": "comite-autre",
            # Autre comité CNOSF, pas le déontologie
            "url": "https://cnosf.franceolympique.com/comite-executif",
            "title": "Comité exécutif",
            "category": "communiques",
            "raw": {},
        },
    ]
    out = _filter_blocklist(rows)
    assert len(out) == 2


def test_canonicalisation_double_slash_preserve():
    """Le canonicaliseur ne déduplique PAS les `//` internes — c'est
    précisément ce qu'on veut puisque le bug Drupal CPSF expose les
    URLs avec `/actualites//region/` exact."""
    from src.site_export import _canon_block_url
    canon = _canon_block_url(
        "https://france-paralympique.fr/actualites//region/bretagne")
    # Le double slash doit rester
    assert "//region/" in canon
