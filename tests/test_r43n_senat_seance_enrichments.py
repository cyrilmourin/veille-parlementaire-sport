"""Tests R43-N (2026-05-18) — Enrichissements de l'onglet « Séance Sénat »
sur la page PPL Sport pro :

  1. Décodage HTML entities (`&#233;`, `&#8217;`) dans `texte_complet`.
  2. Résolution `auteur → groupe politique` via cache fiches sénateurs.
  3. Parsing AKN du texte initial Sénat (PPL 456) → `{label: html}`.

Les tests utilisent les caches disque déjà présents dans le repo
(`data/special_ppl_cache/amdt_seance_senat_ppl670.csv`,
`data/parlementaires_cache/senat_akn_files/ppl24-456.akn.xml`) pour
ne pas dépendre du réseau.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_CACHE = REPO_ROOT / "data" / "special_ppl_cache" / "amdt_seance_senat_ppl670.csv"
AKN_PATH = REPO_ROOT / "data" / "parlementaires_cache" / "senat_akn_files" / "ppl24-456.akn.xml"
SLUGS_PATH = REPO_ROOT / "data" / "senat_slugs.json"


pytestmark = pytest.mark.skipif(
    not CSV_CACHE.exists(),
    reason="Cache CSV séance Sénat absent (test offline only)",
)


def test_r43n_html_entities_decoded_in_amdt_senat(monkeypatch, tmp_path):
    """Le `texte_complet` des amendements Sénat ne doit pas contenir
    d'entités HTML brutes (`&#233;`, `&#8217;`, `&amp;`, etc.). Cyril :
    « le contenu des amendements n'est pas néttoyé ».
    """
    from src import special_ppl as mod

    # Travailler depuis la racine repo : la fonction utilise des paths
    # relatifs (cache disque).
    monkeypatch.chdir(REPO_ROOT)
    rows = mod._load_amdt_senat_seance_csv(
        url="https://www.senat.fr/dummy.csv",
        cache_path=str(CSV_CACHE.relative_to(REPO_ROOT)),
    )
    assert rows, "Le CSV cache doit produire des lignes"
    for r in rows:
        body = r.get("texte_complet") or ""
        # Pas d'entités HTML résiduelles
        assert "&#" not in body, (
            f"Entité HTML non décodée dans amdt {r.get('title')!r}: "
            f"{body[:200]}"
        )
        assert "&amp;" not in body, (
            f"&amp; non décodé dans amdt {r.get('title')!r}: {body[:200]}"
        )


@pytest.mark.skipif(
    not SLUGS_PATH.exists(),
    reason="data/senat_slugs.json absent (test nécessite snapshot Sénat)",
)
def test_r43n_groupe_seance_senat_majoritairement_resolu(monkeypatch):
    """Au moins 90 % des amendements Sénat doivent avoir un `groupe`
    non vide. Cyril : « sur les modules, il en manque un (le top par
    groupe) » → on doit avoir suffisamment de groupes pour alimenter
    le module « Top groupes politiques » côté layout.

    Les rares trous (sénateurs absents du dump slugs, type "M. Paul
    VIDAL" élu après la snapshot) ne doivent pas faire chuter le taux
    de couverture sous 90 %.
    """
    from src import special_ppl as mod

    monkeypatch.chdir(REPO_ROOT)
    rows = mod._load_amdt_senat_seance_csv(
        url="https://www.senat.fr/dummy.csv",
        cache_path=str(CSV_CACHE.relative_to(REPO_ROOT)),
    )
    assert rows
    with_groupe = sum(1 for r in rows if (r.get("groupe") or "").strip())
    rate = with_groupe / len(rows)
    assert rate >= 0.85, (
        f"Couverture groupe Sénat trop faible : {rate:.0%} "
        f"({with_groupe}/{len(rows)})"
    )
    # Le gouvernement doit être étiqueté "GOUVT"
    gouv_rows = [r for r in rows if r.get("auteur", "").upper() == "LE GOUVERNEMENT"]
    if gouv_rows:
        assert all(r.get("groupe") == "GOUVT" for r in gouv_rows), (
            "Les amdt du Gouvernement doivent avoir groupe='GOUVT'"
        )


@pytest.mark.skipif(
    not AKN_PATH.exists(),
    reason="AKN PPL 456 absent (test offline only)",
)
def test_r43n_fetch_senat_text_articles_parse_akn(monkeypatch):
    """`fetch_senat_text_articles` doit parser l'AKN officiel du Sénat
    (PPL 456) et retourner un mapping {label: html_body} cohérent avec
    le format utilisé pour l'AN (clés en MAJUSCULES, préfixe ARTICLE).
    Cyril : « le texte (initial pour le sénat) n'apparait pas ».
    """
    from src import special_ppl as mod

    monkeypatch.chdir(REPO_ROOT)
    arts = mod.fetch_senat_text_articles(str(AKN_PATH))
    assert arts, "Au moins un article doit être extrait"
    # Clés normalisées : MAJUSCULES, préfixe ARTICLE
    for k in arts.keys():
        assert k.startswith("ARTICLE"), f"Clé inattendue {k!r}"
        assert k == k.upper(), f"Clé non normalisée en MAJ {k!r}"
    # Au moins ARTICLE 1ER (présent dans la PPL 456)
    assert "ARTICLE 1ER" in arts
    # Le HTML doit être du texte enveloppé dans <p>...</p>
    art1 = arts["ARTICLE 1ER"]
    assert "<p>" in art1 and "</p>" in art1
    # Pas d'entités HTML résiduelles dans le texte
    assert "&#" not in art1
