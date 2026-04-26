"""R40-L (2026-04-27) — Bascule Sénat /dossier-legislatif/ → /leg/ +
élargissement scope text-fragment hors comptes_rendus.

Port côté veille parlementaire Lidl 2026-04-27, signalé par Cyril.

Bug constaté (Lidl) : les liens « Voir le dossier législatif » Sénat ne
scrollaient pas au keyword surligné — alors que ça marchait pour l'AN.

Cause : la page `/dossier-legislatif/<slug>.html` est un INDEX (titre
+ timeline) qui ne contient pas le corps du texte. Le text-fragment
`#:~:text=<keyword>` ne matche que si le keyword se trouve dans le
titre — sinon le navigateur reste au top de la page. Côté AN c'est
moins visible : la page `/dyn/17/dossiers/<uid>` répète titre + libellés
des actes, le keyword s'y trouve souvent.

Fix : la page `/leg/<slug>.html` contient l'exposé des motifs +
articles + signatures = matériel riche en mots-clés thématiques. On
bascule sur cette URL avant d'ajouter le text-fragment, uniquement
pour les slugs modernes (sessions 2019-2029) — les anciens (08, 15...)
répondent 404 sur /leg/.

Vérification live (2026-04-27) sur slugs Sport :
- ppl24-247, ppr25-069, pjl23-020 → 200 OK
- pjl22-220 (JOP 2024), pjl24-630 (JOP 2030) → 200 OK
- ppl15-100, ppl08-050 → 404 (anciens)

Le bloc text-fragment est aussi élargi hors `comptes_rendus` (toutes
catégories AN/Sénat/Légifrance bénéficient désormais du fragment), et
le keyword choisi est le plus long (max(kws, key=len)) plutôt que le
1er — plus distinctif sur les pages au contenu varié.
"""
from __future__ import annotations

from src.site_export import _senat_dosleg_to_leg, _SENAT_LEG_BASCULE_RE


# ---------------------------------------------------------------------------
# 1. _senat_dosleg_to_leg — bascule URL
# ---------------------------------------------------------------------------


def test_bascule_slug_moderne_2024():
    url = "https://www.senat.fr/dossier-legislatif/pjl24-630.html"
    expected = "https://www.senat.fr/leg/pjl24-630.html"
    assert _senat_dosleg_to_leg(url) == expected


def test_bascule_slug_moderne_2025():
    url = "https://www.senat.fr/dossier-legislatif/ppr25-069.html"
    assert _senat_dosleg_to_leg(url) == "https://www.senat.fr/leg/ppr25-069.html"


def test_bascule_slug_2019_inclus():
    """La regex inclut explicitement la session 19 (2019-2020)."""
    url = "https://www.senat.fr/dossier-legislatif/ppl19-100.html"
    assert _senat_dosleg_to_leg(url) == "https://www.senat.fr/leg/ppl19-100.html"


def test_bascule_slug_pjl():
    url = "https://www.senat.fr/dossier-legislatif/pjl22-220.html"
    assert _senat_dosleg_to_leg(url) == "https://www.senat.fr/leg/pjl22-220.html"


def test_pas_de_bascule_slug_ancien_2015():
    """Régression : un slug ppl15-XXX ne doit PAS être basculé (les
    pages /leg/ sont 404 pour les sessions <2019)."""
    url = "https://www.senat.fr/dossier-legislatif/ppl15-100.html"
    assert _senat_dosleg_to_leg(url) == url


def test_pas_de_bascule_slug_ancien_2008():
    url = "https://www.senat.fr/dossier-legislatif/ppl08-050.html"
    assert _senat_dosleg_to_leg(url) == url


def test_pas_de_bascule_slug_ancien_2000_et_85():
    for slug in ("ppl00-100", "ppl85-200", "ppl77-001"):
        url = f"https://www.senat.fr/dossier-legislatif/{slug}.html"
        assert _senat_dosleg_to_leg(url) == url


def test_pas_de_bascule_url_an():
    url = "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N50771"
    assert _senat_dosleg_to_leg(url) == url


def test_pas_de_bascule_url_legifrance():
    url = "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000049012345"
    assert _senat_dosleg_to_leg(url) == url


def test_idempotence_si_deja_leg():
    """Si l'URL est déjà sous /leg/, no-op."""
    url = "https://www.senat.fr/leg/pjl24-630.html"
    assert _senat_dosleg_to_leg(url) == url


def test_idempotence_double_appel():
    """Appliquer la bascule deux fois donne le même résultat qu'une fois."""
    url = "https://www.senat.fr/dossier-legislatif/pjl24-630.html"
    once = _senat_dosleg_to_leg(url)
    twice = _senat_dosleg_to_leg(once)
    assert once == twice == "https://www.senat.fr/leg/pjl24-630.html"


def test_url_vide_ou_none():
    assert _senat_dosleg_to_leg("") == ""
    assert _senat_dosleg_to_leg(None) is None


def test_bascule_supporte_http_et_www():
    """La regex accepte http:// et l'absence de www. comme variantes
    (http upgrade et redirects suppression de www. côté Sénat)."""
    for url, expected in (
        ("http://www.senat.fr/dossier-legislatif/pjl24-630.html",
         "http://www.senat.fr/leg/pjl24-630.html"),
        ("https://senat.fr/dossier-legislatif/pjl24-630.html",
         "https://senat.fr/leg/pjl24-630.html"),
    ):
        assert _senat_dosleg_to_leg(url) == expected


def test_bascule_extension_htm_sans_l():
    """La regex tolère .htm (sans le L final) au cas où Sénat varierait."""
    url = "https://www.senat.fr/dossier-legislatif/pjl24-630.htm"
    out = _senat_dosleg_to_leg(url)
    # Le helper réinjecte .html systématiquement (forme canonique servie)
    assert out == "https://www.senat.fr/leg/pjl24-630.html"


def test_pas_de_bascule_slug_inconnu():
    """Slug qui ne match pas le pattern (autre type que ppl/ppr/pjl)
    reste inchangé — par exemple un dossier de mission d'information."""
    url = "https://www.senat.fr/dossier-legislatif/r24-300.html"
    assert _senat_dosleg_to_leg(url) == url


# ---------------------------------------------------------------------------
# 2. Régression sanity sur la regex
# ---------------------------------------------------------------------------


def test_regex_session_range_2019_2029():
    """La regex doit capturer les préfixes de session 19, 20, 21, ..., 29
    (sessions 2019-2030). Tester quelques exemples."""
    for prefix in ("19", "20", "23", "26", "29"):
        url = f"https://www.senat.fr/dossier-legislatif/ppl{prefix}-100.html"
        m = _SENAT_LEG_BASCULE_RE.search(url)
        assert m is not None, f"Regex ne match pas {prefix}"
        assert m.group("slug") == f"ppl{prefix}-100"


def test_regex_rejette_session_pre_2019():
    """Sessions <19 et >29 sont exclues de la regex pour éviter de
    casser les liens vers des textes anciens (404 sur /leg/)."""
    for prefix in ("00", "05", "08", "12", "15", "18", "30", "85"):
        url = f"https://www.senat.fr/dossier-legislatif/ppl{prefix}-100.html"
        m = _SENAT_LEG_BASCULE_RE.search(url)
        assert m is None, f"Regex match {prefix} alors qu'elle ne devrait pas"


def test_regex_capture_les_3_types_de_textes():
    """ppl (proposition de loi), ppr (proposition de résolution), pjl
    (projet de loi). Pas d'autres préfixes (rapports, missions, etc.)."""
    for typ in ("ppl", "ppr", "pjl"):
        url = f"https://www.senat.fr/dossier-legislatif/{typ}24-100.html"
        assert _SENAT_LEG_BASCULE_RE.search(url) is not None
    # Préfixes hors scope
    for typ in ("r", "doc", "ar"):
        url = f"https://www.senat.fr/dossier-legislatif/{typ}24-100.html"
        assert _SENAT_LEG_BASCULE_RE.search(url) is None
