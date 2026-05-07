"""R41-L (2026-05-07) — Fixes dossiers législatifs :
1. _dosleg_word_set ne strip plus les `(SIGLE)` au milieu d'un titre
   (régression CREPS Vichy : un seul `(CREPS)` parenthèse coupait tout).
2. _boost_dosleg_with_agenda reroute URL+chamber vers l'AN quand la
   dernière inscription agenda matchée est à l'AN (Cyril : « le lien
   AN doit être privilégié quand l'examen y est en cours »).
"""
from __future__ import annotations

from src.site_export import (
    _boost_dosleg_with_agenda,
    _dosleg_word_set,
)


# ---------------------------------------------------------------------------
# Fix 1 : _dosleg_word_set strip seulement les suffixes courts en fin
# ---------------------------------------------------------------------------


def test_word_set_garde_mots_apres_sigle_milieu_titre():
    """Un `(CREPS)` au milieu ne doit plus tronquer le titre."""
    t = ("Proposition de loi relative à l'expérimentation d'une "
         "gouvernance territoriale unifiée pour le centre de "
         "ressources, d'expertise et de performance sportive (CREPS) "
         "de Vichy")
    ws = _dosleg_word_set(t)
    # creps et vichy doivent être préservés (étaient perdus avant R41-L)
    assert "creps" in ws
    assert "vichy" in ws


def test_word_set_strip_suffixe_pjl_en_fin():
    """Le `(PPL)` en fin doit toujours être strippé."""
    t = "Gouvernance territoriale unifiée pour le CREPS de Vichy (PPL)"
    ws = _dosleg_word_set(t)
    assert "ppl" not in ws
    # creps et vichy préservés
    assert "creps" in ws
    assert "vichy" in ws


def test_word_set_strip_suffixes_empiles():
    """Plusieurs suffixes empilés en fin sont tous strippés."""
    t = "Loi sport professionnel (PPL) (urgence)"
    ws = _dosleg_word_set(t)
    assert "ppl" not in ws and "urgence" not in ws
    # mots significatifs préservés
    assert "sport" in ws
    assert "professionnel" in ws


def test_creps_vichy_dedup_apres_fix():
    """Les deux titres CREPS Vichy doivent désormais avoir une
    intersection ≥ 5 mots significatifs (seuil INTERSECTION_MIN du dedup)."""
    t1 = "Gouvernance territoriale unifiée pour le CREPS de Vichy (PPL)"
    t2 = ("Proposition de loi relative à l'expérimentation d'une "
          "gouvernance territoriale unifiée pour le centre de "
          "ressources, d'expertise et de performance sportive (CREPS) "
          "de Vichy")
    w1, w2 = _dosleg_word_set(t1), _dosleg_word_set(t2)
    shared = w1 & w2
    assert len(shared) >= 5, f"intersection={shared} (attendu ≥ 5)"


# ---------------------------------------------------------------------------
# Fix 2 : reroute URL+chamber vers l'AN quand agenda AN matché
# ---------------------------------------------------------------------------


def _dosleg(title, date="2025-03-18T10:00:00", chamber="Senat",
            url="https://www.senat.fr/dossier-legislatif/s92930456.html"):
    return {
        "category": "dossiers_legislatifs",
        "title": title,
        "published_at": date,
        "chamber": chamber,
        "url": url,
        "raw": {},
    }


def _agenda(title, date, chamber="AN"):
    return {
        "category": "agenda",
        "title": title,
        "published_at": date,
        "chamber": chamber,
    }


def test_reroute_url_vers_an_si_agenda_an_matche():
    """PPL sport pro Sénat + agenda AN avec « (n° 1560) » →
    URL bascule vers /dyn/17/textes/l17b1560_proposition-loi."""
    rows = [
        _dosleg(
            "Proposition de loi relative à l'organisation, à la gestion "
            "et au financement du sport professionnel"
        ),
        _agenda(
            "Examen de la proposition de loi relative à l'organisation, "
            "à la gestion et au financement du sport professionnel "
            "(n° 1560)",
            "2026-05-12T09:00:00",
        ),
    ]
    _boost_dosleg_with_agenda(rows)
    d = rows[0]
    assert d["chamber"] == "AN"
    assert d["url"] == (
        "https://www.assemblee-nationale.fr/dyn/17/textes/"
        "l17b1560_proposition-loi"
    )
    # L'URL et chamber d'origine sont tracés
    raw = d["raw"]
    assert "s92930456" in raw["url_original"]
    assert raw["chamber_original"] == "Senat"


def test_reroute_doctype_projet_loi_si_titre_commence_par_projet():
    rows = [
        _dosleg(
            "Projet de loi relatif à l'organisation, à la gestion et au "
            "financement de la gouvernance des établissements sportifs",
            chamber="Senat",
        ),
        _agenda(
            "Examen du projet de loi relatif à l'organisation, à la "
            "gestion et au financement de la gouvernance des "
            "établissements sportifs (n° 1234)",
            "2026-05-12T09:00:00",
        ),
    ]
    _boost_dosleg_with_agenda(rows)
    assert "l17b1234_projet-loi" in rows[0]["url"]


def test_pas_de_reroute_si_agenda_senat():
    """Si la dernière inscription est au Sénat, on ne touche pas l'URL."""
    rows = [
        _dosleg(
            "Proposition de loi relative à l'organisation, à la gestion "
            "et au financement du sport professionnel"
        ),
        _agenda(
            "Examen de la proposition de loi relative à l'organisation, "
            "à la gestion et au financement du sport professionnel "
            "(n° 1560)",
            "2026-05-12T09:00:00",
            chamber="Senat",
        ),
    ]
    _boost_dosleg_with_agenda(rows)
    # Date boostée mais URL d'origine conservée
    assert rows[0]["chamber"] == "Senat"
    assert "senat.fr" in rows[0]["url"]
    assert "url_original" not in rows[0]["raw"]


def test_pas_de_reroute_si_pas_de_numero_dans_agenda():
    """Si l'agenda ne mentionne pas « (n° XXX) », on ne reroute pas."""
    rows = [
        _dosleg(
            "Proposition de loi relative à l'organisation, à la gestion "
            "et au financement du sport professionnel"
        ),
        _agenda(
            "Examen de la proposition de loi relative à l'organisation, "
            "à la gestion et au financement du sport professionnel",
            "2026-05-12T09:00:00",
        ),
    ]
    _boost_dosleg_with_agenda(rows)
    # Date boostée
    assert rows[0]["published_at"].startswith("2026-05-12")
    # Mais URL non rerouted (pas de numéro)
    assert "senat.fr" in rows[0]["url"]
    assert "url_original" not in rows[0]["raw"]
