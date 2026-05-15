"""R42-CV (2026-05-15) — Corrections page spéciale PPL Sport pro.

Cyril 2026-05-15 :
1. Le titre de la page doit être l'intitulé exact du texte (et non
   « Proposition de loi » générique).
2. La page énumérait 300 amendements puis le donut indiquait 200
   « déposés » → cap à 200 supprimé pour aligner les compteurs.
3. Cartouche d'analyse manuelle (lue depuis YAML) au-dessus des
   amendements commission + bloc équivalent pour la séance.
4. Stopwords du nuage : ajouter `mots`, `sénat`, `dédiée`, `effet`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.special_ppl import (
    AN_TEXTE_REF,
    HERO_SUBTITLE,
    HERO_TITLE,
    PPL_KEY,
    _build_wordcloud,
    _WC_STOPWORDS,
    build_payload,
    collect_special_ppl,
    load_analysis,
)


def _row(**kw):
    base = {
        "title": "Item",
        "url": "",
        "category": "amendements",
        "chamber": "AN",
        "published_at": "2026-05-12T09:00:00",
        "raw": {},
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 1. Hero title — intitulé exact dans le payload meta
# ---------------------------------------------------------------------------


def test_meta_hero_title_intitule_complet():
    """Le payload expose `hero_title` = intitulé officiel de la PPL,
    pas le label interne « Spécial PPL Sport professionnel »."""
    payload = build_payload(collect_special_ppl([]))
    title = payload["meta"]["hero_title"]
    assert "organisation" in title
    assert "gestion" in title
    assert "financement" in title
    assert "sport professionnel" in title
    assert title == HERO_TITLE


def test_meta_hero_subtitle_expose():
    """`hero_subtitle` mentionne au minimum le n° AN et la commission Culture."""
    payload = build_payload(collect_special_ppl([]))
    subtitle = payload["meta"]["hero_subtitle"]
    assert "1560" in subtitle
    assert subtitle == HERO_SUBTITLE


def test_meta_hero_title_pas_le_label_interne():
    """Garde-fou : `hero_title` ≠ le slug interne `PPL_TITLE`. Le label
    interne reste exposé via `meta.title` mais ne doit PAS servir de
    titre de page (sinon la page affichait « Spécial PPL Sport pro »)."""
    payload = build_payload(collect_special_ppl([]))
    assert payload["meta"]["hero_title"] != payload["meta"]["title"]


# ---------------------------------------------------------------------------
# 2. Cap 200 → 5000 : alignement compteur badge / total donut
# ---------------------------------------------------------------------------


def test_amdt_commission_pas_tronque_a_200():
    """Cyril 2026-05-15 : « on énumère 300 amendements pour ensuite afficher
    200 amendements déposés ». Le slice à 200 créait l'écart entre le badge
    onglet (counts réel) et le total donut (calculé sur la liste sliced).
    Avec 300 amdt → counts=300 ET len(payload)=300 ET donut total=300."""
    rows = [
        _row(category="amendements", title=f"Amdt n°AC{i}",
             raw={"texte_ref": AN_TEXTE_REF, "sort": "Adopté"})
        for i in range(300)
    ]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["counts"]["amdt_commission"] == 300
    assert len(payload["amdt_commission"]) == 300
    # Le donut total est calculé sur la liste rendue → doit aussi montrer 300
    assert payload["sort_stats_commission"]["total"] == 300


def test_amdt_seance_pas_tronque_a_200():
    """Symétrique pour la séance publique (cap remonté à 5000)."""
    rows = [
        _row(category="amendements", title=f"Amdt n°{i}",
             raw={"texte_ref": AN_TEXTE_REF})
        for i in range(280)
    ]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["counts"]["amdt_seance"] == 280
    assert len(payload["amdt_seance"]) == 280


# ---------------------------------------------------------------------------
# 3. Analyse manuelle (config YAML)
# ---------------------------------------------------------------------------


def test_load_analysis_fichier_absent_retourne_blocs_vides(tmp_path):
    """Pas de fichier → blocs vides, pas d'exception."""
    out = load_analysis(PPL_KEY, str(tmp_path / "nope.yml"))
    assert out["commission"] == {"date_examen": "", "auteur": "", "texte": ""}
    assert out["seance"] == {"date_examen": "", "auteur": "", "texte": ""}


def test_load_analysis_yaml_basique(tmp_path):
    cfg = tmp_path / "ana.yml"
    cfg.write_text(
        "special_ppl:\n"
        "  commission:\n"
        "    date_examen: '12-13 mai 2026'\n"
        "    auteur: 'Cyril'\n"
        "    texte: |\n"
        "      Premier paragraphe.\n"
        "\n"
        "      Deuxième paragraphe.\n"
        "  seance:\n"
        "    date_examen: ''\n"
        "    texte: ''\n",
        encoding="utf-8",
    )
    out = load_analysis(PPL_KEY, str(cfg))
    assert out["commission"]["date_examen"] == "12-13 mai 2026"
    assert out["commission"]["auteur"] == "Cyril"
    assert "Premier paragraphe" in out["commission"]["texte"]
    assert "Deuxième paragraphe" in out["commission"]["texte"]
    assert out["seance"]["texte"] == ""


def test_load_analysis_section_absente(tmp_path):
    """data_key inconnu → blocs vides, pas d'exception."""
    cfg = tmp_path / "ana.yml"
    cfg.write_text("autre_section:\n  commission:\n    texte: 'x'\n",
                   encoding="utf-8")
    out = load_analysis(PPL_KEY, str(cfg))
    assert out["commission"]["texte"] == ""


def test_build_payload_inclut_analysis(tmp_path, monkeypatch):
    """Le payload expose le bloc analysis (vide par défaut si pas de
    fichier config dans le cwd du test)."""
    monkeypatch.chdir(tmp_path)
    payload = build_payload(collect_special_ppl([]))
    assert "analysis" in payload
    assert "commission" in payload["analysis"]
    assert "seance" in payload["analysis"]


def test_load_analysis_routage_par_data_key(tmp_path):
    """Le mapping payload_key → section YAML route correctement la PPL
    équipements vers `special_ppl_equip`."""
    cfg = tmp_path / "ana.yml"
    cfg.write_text(
        "special_ppl:\n"
        "  commission:\n"
        "    texte: 'analyse PPL sport pro'\n"
        "special_ppl_equip:\n"
        "  commission:\n"
        "    texte: 'analyse PPL équipements'\n",
        encoding="utf-8",
    )
    out_sport = load_analysis("ppl-sport-professionnel", str(cfg))
    out_equip = load_analysis(
        "ppl-partenariats-equipements-sportifs", str(cfg)
    )
    assert "sport pro" in out_sport["commission"]["texte"]
    assert "équipements" in out_equip["commission"]["texte"]


# ---------------------------------------------------------------------------
# 4. Stopwords nuage : mots / sénat / dédiée / effet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stopword", [
    "mots", "mot",
    "sénat", "senat",
    "dédiée", "dediee", "dédié", "dedie",
    "effet", "effets",
])
def test_wordcloud_filtre_stopwords_r42cv(stopword):
    """Cyril 2026-05-15 : « Enlève du nuage de mots clés mots, sénat,
    dédiée, effet ». On vérifie que ces tokens sont dans le set de
    stopwords appliqué par `_build_wordcloud`."""
    assert stopword in _WC_STOPWORDS


def test_wordcloud_n_ajoute_pas_mots_filtrés():
    """Test fonctionnel : un extract truffé de stopwords R42-CV ne
    fait pas remonter ces mots dans le nuage."""
    payload_rows = [
        {"extract": (
            "Le présent amendement est dédié au Sénat. Il a pour effet "
            "de modifier les mots de la phrase. Les effets attendus "
            "sont importants pour les fédérations sportives. "
            "L'agrément des ligues professionnelles est revu."
        )}
    ]
    cloud = _build_wordcloud(payload_rows)
    words = {item["word"] for item in cloud}
    # Stopwords R42-CV absents
    for banned in ("mots", "sénat", "dédié", "effet", "effets"):
        assert banned not in words, f"{banned} ne devrait pas être dans le nuage"
    # Mais des mots métier restent (ex. agrément, ligues, fédérations
    # filtrés ailleurs… on prend `agrément` qui n'est pas en stopword)
    assert "agrément" in words or "ligues" in words
