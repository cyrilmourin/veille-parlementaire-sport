"""Tests R42-AM — keywords yaml ciblés pour les rapports/avis AN qui
n'étaient pas matchés malgré un sujet 100 % sport.

Vérifie que les keywords ajoutés capturent bien les cas listés par Cyril :
- RINF B2465 « évaluation loi du 2 mars 2022 visant à démocratiser le sport »
- 4 avis PLF « Sport, jeunesse et vie associative » (PLF 2025 + PLF 2026)
- RAPP B2074 OPECST « science dans la mêlée pour une nation sportive »
- RAPP B0699 « pour plus de sport et moins de sucre »

Sans bypass URL (cf. R39-K : Cyril veut un keyword thématique traçable
pour TOUT item qui remonte).
"""
from __future__ import annotations

from pathlib import Path

from src.keywords import KeywordMatcher

ROOT = Path(__file__).resolve().parent.parent
MATCHER = KeywordMatcher(ROOT / "config" / "keywords.yml")


def test_rinf_b2465_democratiser_le_sport_match():
    """RINF B2465 : titre AN contient « démocratiser le sport »."""
    title = ("L'évaluation de la loi n° 2022-296 du 2 mars 2022 visant à "
             "démocratiser le sport en France - N° 2465")
    summary = ("Rapport d'information déposé en application de l'article "
               "145-7 alinéa 1 du règlement, par la commission des affaires "
               "culturelles et de l'éducation sur l'évaluation de la loi "
               "n° 2022-296 du 2 mars 2022 visant à démocratiser le sport "
               "en France (M. Joël Bruneau, M. Bruno Clavet et "
               "Mme Véronique Riotton)")
    kws, fams = MATCHER.match(title, summary, "")
    # "Démocratiser le sport" (ou variante sans accent) doit être dans kws.
    assert "Démocratiser le sport" in kws or "Democratiser le sport" in kws, (
        f"Aucun match « démocratiser sport » dans {kws}"
    )


def test_avis_plf_2026_sport_match_via_mission_budgetaire():
    """Avis B2043 Tome IX 2026 : titre générique « PLF 2026 - N° 2043 Tome IX »
    mais summary contient le nom de la mission « Sport, jeunesse et vie
    associative : Sport ». Le keyword R42-AM capture cette structure."""
    title = "Projet de loi de finances pour 2026 - N° 2043 Tome IX"
    summary = ("Avis de l'Assemblée sur le projet de loi de finances pour "
               "2026 (n°1906). - Sport, jeunesse et vie associative : Sport")
    kws, fams = MATCHER.match(title, summary, "")
    assert any("jeunesse et vie associative" in k.lower() for k in kws), (
        f"Aucun match sur mission budgétaire dans {kws}"
    )


def test_avis_plf_2025_jva_match_aussi():
    """Avis B472 Tome VIII 2025 : même structure mais sous-mission « J&VA »."""
    title = "Projet de loi de finances pour 2025 - N° 472 Tome VIII"
    summary = ("Avis de l'Assemblée sur le projet de loi de finances pour "
               "2025 (n°324). - Sport, jeunesse et vie associative : "
               "Jeunesse et vie associative")
    kws, fams = MATCHER.match(title, summary, "")
    assert any("jeunesse et vie associative" in k.lower() for k in kws)


def test_rapp_b2074_opecst_nation_sportive_match():
    """RAPP B2074 OPECST : titre contient « nation sportive »."""
    title = ("Rapport de l'office parlementaire d'évaluation des choix "
             "scientifiques et technologiques établi au nom de l'office, "
             "sur la science dans la mêlée pour une nation sportive "
             "(M. David Ros)")
    kws, fams = MATCHER.match(title, "", "")
    assert any("nation sportive" in k.lower() for k in kws), (
        f"Aucun match « nation sportive » dans {kws}"
    )


def test_rapp_b0699_sport_sucre_match():
    """RAPP B0699 : titre contient « pour plus de sport et moins de sucre »."""
    title = ("Rapport de la commission des affaires culturelles et de "
             "l'éducation sur la proposition de loi de M. Thierry Sother "
             "et plusieurs de ses collègues pour plus de sport et moins "
             "de sucre (558). (M. Thierry Sother)")
    kws, fams = MATCHER.match(title, "", "")
    assert any("plus de sport et moins de sucre" in k.lower() for k in kws), (
        f"Aucun match « plus de sport et moins de sucre » dans {kws}"
    )


# ---------------------------------------------------------------------------
# Non-régression : titres NON sport ne doivent pas matcher accidentellement
# ---------------------------------------------------------------------------

def test_non_sport_pas_de_faux_positif_via_nouveaux_keywords():
    """Un PLF générique sans mention de la mission Sport ne match pas via
    les keywords R42-AM (vérifie qu'on n'a pas introduit `sport` nu ou
    une formulation trop large)."""
    title = "Projet de loi de finances pour 2026 - N° 2047 Tome V"
    summary = ("Avis de l'Assemblée sur le projet de loi de finances pour "
               "2026 (n°1906). - Écologie, développement et mobilité "
               "durables : Transports terrestres et fluviaux")
    kws, fams = MATCHER.match(title, summary, "")
    # AUCUN match attendu — c'est un avis Transports, pas Sport
    assert not any(
        "jeunesse et vie associative" in k.lower()
        or "nation sportive" in k.lower()
        or "democratiser le sport" in k.lower()
        or "plus de sport et moins de sucre" in k.lower()
        for k in kws
    ), f"Faux positif R42-AM via {kws}"
