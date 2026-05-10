"""R41-AT (2026-05-10) — Régression dédup dosleg sur word_sets petits.

Bug observé (capture Cyril 2026-05-10 page Dossiers législatifs) : la
PPL n°1560 « Proposition de loi relative à l'organisation, à la gestion
et au financement du sport professionnel » apparaissait en DOUBLON, une
fois avec badge « Première lecture AN » (item Sénat-rerouté en R41-L)
et une fois avec badge « 1ère lecture · commission » (item AN natif).

Cause racine : le titre, après stop-words R13-L (qui retire
« organisation »), produit un word_set de 4 mots significatifs :
{gestion, financement, sport, professionnel}. Or le seuil
`INTERSECTION_MIN = 5` (R18) est plus large que la taille du word_set
→ même si les 2 items ont word_sets identiques, intersection = 4 < 5
→ pas de dédup en passe 2b.

Les passes 2a (URL canon) et 2c (dossier_id) ne matchent pas non plus :
- 2a : URLs différentes (item natif AN vs item Sénat-rerouté)
- 2c : dossier_id AN (DLR5L17N…) ≠ signet Sénat (pjl…)

Fix R41-AT : seuil dynamique. Si min(len_a, len_b) ≥ 3 (plancher
`SMALL_WS_FLOOR`), on demande `intersection ≥ min(5, smaller)` au lieu
de `≥ 5`. Conservateur : tous les mots du plus petit set doivent être
dans le plus grand → faux positifs rares.

Tests :
1. Cas réel PPL Sport pro (titres identiques, 4 mots) → 1 item.
2. Régression : 2 dossiers vraiment distincts (intersection = 0)
   restent séparés.
3. Régression : titres avec 2 mots seulement (sous le plancher) ne
   dédupent pas.
4. Cas standard JOP (titres ≥ 5 mots) toujours dédup correctement.
"""
from __future__ import annotations

from src.site_export import _dedup


def _make_item(uid, title, url, chamber, date, raw=None, source_id="x"):
    return {
        "uid": uid,
        "source_id": source_id,
        "category": "dossiers_legislatifs",
        "chamber": chamber,
        "title": title,
        "url": url,
        "published_at": date,
        "raw": raw or {},
    }


def test_dedup_ppl_sport_pro_titres_identiques_4_mots():
    """PPL n°1560 — 2 items en doublon (titres identiques, 4 mots
    significatifs) doivent fusionner en 1 seul."""
    titre = (
        "Proposition de loi relative à l'organisation, à la gestion et "
        "au financement du sport professionnel"
    )
    an = _make_item(
        "DLR5L17N52049",
        titre,
        "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N52049",
        "AN",
        "2026-05-18",
        raw={"dossier_id": "DLR5L17N52049"},
    )
    senat_rerouted = _make_item(
        "pjl24-456",
        titre,
        "https://www.assemblee-nationale.fr/dyn/17/textes/l17b1560_proposition-loi",
        "AN",
        "2026-05-18",
        raw={"signet": "pjl24-456", "dossier_id": "pjl24-456"},
        source_id="senat_dosleg",
    )
    result = _dedup([an, senat_rerouted])
    dosleg = [r for r in result if r.get("category") == "dossiers_legislatifs"]
    assert len(dosleg) == 1, (
        f"PPL Sport pro : attendu 1 item après dédup, vu {len(dosleg)} : "
        f"{[r['url'] for r in dosleg]}"
    )


def test_dedup_distinct_dossiers_no_intersection_remain_separate():
    """Régression : 2 dossiers vraiment distincts (intersection = 0)
    doivent rester séparés."""
    a = _make_item(
        "DLR5L17N52049",
        "Proposition de loi relative à l'organisation, à la gestion "
        "et au financement du sport professionnel",
        "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N52049",
        "AN",
        "2026-05-18",
    )
    b = _make_item(
        "DLR5L17N99999",
        "Proposition de loi visant à protéger les espèces aquatiques "
        "menacées",
        "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N99999",
        "AN",
        "2026-04-12",
    )
    result = _dedup([a, b])
    dosleg = [r for r in result if r.get("category") == "dossiers_legislatifs"]
    assert len(dosleg) == 2


def test_dedup_two_words_titles_below_floor_dont_merge():
    """Régression : titres trop courts (2 mots significatifs) ne
    doivent PAS dédup même avec intersection = 100% du plus petit set
    — risque faux positif trop élevé."""
    # Titres de 2 mots après normalisation — sous le SMALL_WS_FLOOR.
    a = _make_item(
        "AAA",
        "Proposition de loi sur le financement",
        "https://www.example.com/a",
        "AN",
        "2026-05-01",
    )
    b = _make_item(
        "BBB",
        "Proposition de loi de financement",
        "https://www.example.com/b",
        "AN",
        "2026-05-02",
    )
    result = _dedup([a, b])
    dosleg = [r for r in result if r.get("category") == "dossiers_legislatifs"]
    # 2 mots seulement (« sport »/« financement ») — sous le plancher 3
    # → ne devrait PAS dédup. Mais en réalité, ces 2 items ont même
    # word_set normalisé { "financement" } (1 mot après stopwords).
    # Comme c'est sous WORDS_MIN=4, la passe 2b ne tente rien → 2 items.
    assert len(dosleg) == 2


def test_dedup_jop_long_titles_still_merge():
    """Régression sur le cas standard (titres ≥ 5 mots) : dédup OK."""
    titre = (
        "Projet de loi relatif à l'organisation et au déroulement des "
        "Jeux Olympiques et Paralympiques de 2030 dans les Alpes "
        "françaises"
    )
    a = _make_item(
        "DLR5L17N52100",
        titre,
        "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N52100",
        "AN",
        "2026-03-20",
    )
    b = _make_item(
        "pjl24-630",
        titre,
        "https://www.example.com/other",
        "Senat",
        "2026-03-20",
    )
    result = _dedup([a, b])
    dosleg = [r for r in result if r.get("category") == "dossiers_legislatifs"]
    assert len(dosleg) == 1
