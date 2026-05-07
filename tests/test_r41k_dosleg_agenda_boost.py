"""R41-K (2026-05-07) — Tests du boost de date dossier législatif via
inscriptions agenda liées.

Cas Cyril : la PPL « organisation, gestion et financement du sport
professionnel » est déposée au Sénat le 18/03/2025 (dosleg), puis
inscrite à l'examen en commission AN le 12/05/2026 et en plénière le
18/05/2026 (agenda). Sans boost, le dossier reste classé sur sa date
de dépôt et tombe en bas de liste alors qu'il est en examen actif.

Le boost recalcule `published_at` = max(published_at, dernière date
agenda matchée). Le matching utilise `_dosleg_word_set` (intersection
de mots significatifs ≥ 4 ET ≥ 50% des mots du dosleg).
"""
from __future__ import annotations

from src.site_export import _boost_dosleg_with_agenda


def _dosleg(title: str, date: str = "2025-03-18T10:00:00",
            chamber: str = "Senat") -> dict:
    return {
        "category": "dossiers_legislatifs",
        "title": title,
        "published_at": date,
        "chamber": chamber,
        "raw": {},
    }


def _agenda(title: str, date: str, chamber: str = "AN") -> dict:
    return {
        "category": "agenda",
        "title": title,
        "published_at": date,
        "chamber": chamber,
    }


# ---------------------------------------------------------------------------
# Cas nominal : la PPL sport pro est boostée
# ---------------------------------------------------------------------------


def test_ppl_sport_pro_boostee_via_agenda_commission():
    """PPL Sénat 18/03/2025 + inscription commission AN 12/05/2026
    → published_at boosté à 2026-05-12."""
    rows = [
        _dosleg(
            "Proposition de loi relative à l'organisation, à la gestion "
            "et au financement du sport professionnel"
        ),
        _agenda(
            "Examen de la proposition de loi, adoptée par le Sénat après "
            "engagement de la procédure accélérée, relative à "
            "l'organisation, à la gestion et au financement du sport "
            "professionnel (n° 1560)",
            "2026-05-12T09:00:00",
        ),
    ]
    _boost_dosleg_with_agenda(rows)
    assert rows[0]["published_at"].startswith("2026-05-12")
    assert rows[0]["raw"]["effective_at_source"] == "agenda"
    assert rows[0]["raw"]["published_at_original"] == "2025-03-18T10:00:00"


def test_ppl_sport_pro_prend_le_plus_recent_si_plusieurs_agendas():
    """Plusieurs inscriptions agenda → garde la plus récente."""
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
        _agenda(
            "Discussion de la proposition de loi relative à "
            "l'organisation, à la gestion et au financement du sport "
            "professionnel",
            "2026-05-18T15:00:00",
        ),
    ]
    _boost_dosleg_with_agenda(rows)
    assert rows[0]["published_at"].startswith("2026-05-18")


# ---------------------------------------------------------------------------
# Garde-fous : pas de boost si match faible / agenda plus ancien
# ---------------------------------------------------------------------------


def test_dosleg_pas_boostee_si_agenda_plus_ancien():
    rows = [
        _dosleg(
            "Proposition de loi relative à l'organisation, à la gestion "
            "et au financement du sport professionnel",
            date="2026-04-15T10:00:00",
        ),
        _agenda(
            "Examen de la proposition de loi relative à l'organisation, "
            "à la gestion et au financement du sport professionnel",
            "2026-03-12T09:00:00",
        ),
    ]
    _boost_dosleg_with_agenda(rows)
    # Date originale conservée
    assert rows[0]["published_at"] == "2026-04-15T10:00:00"
    # Pas de trace de boost
    assert "effective_at_source" not in rows[0]["raw"]


def test_dosleg_pas_boostee_si_match_trop_faible():
    """Agenda mentionne d'autres mots-clés → pas de match."""
    rows = [
        _dosleg(
            "Proposition de loi relative à l'organisation, à la gestion "
            "et au financement du sport professionnel"
        ),
        _agenda(
            "Examen du projet de loi de finances 2026 — mission Sport",
            "2026-05-15T09:00:00",
        ),
    ]
    _boost_dosleg_with_agenda(rows)
    assert rows[0]["published_at"] == "2025-03-18T10:00:00"
    assert "effective_at_source" not in rows[0]["raw"]


def test_dosleg_seul_pas_de_modification():
    """Pas d'agenda dans la liste → no-op."""
    rows = [
        _dosleg("Proposition de loi sport professionnel organisation gestion"),
    ]
    before = rows[0]["published_at"]
    _boost_dosleg_with_agenda(rows)
    assert rows[0]["published_at"] == before


def test_agenda_seul_pas_de_modification():
    """Pas de dosleg dans la liste → no-op."""
    rows = [
        _agenda("Examen sport professionnel", "2026-05-12T09:00:00"),
    ]
    before = rows[0]["published_at"]
    _boost_dosleg_with_agenda(rows)
    assert rows[0]["published_at"] == before


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


def test_idempotent_meme_resultat_au_2e_passage():
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
    after_first = rows[0]["published_at"]
    _boost_dosleg_with_agenda(rows)
    assert rows[0]["published_at"] == after_first


# ---------------------------------------------------------------------------
# N'affecte pas les autres catégories
# ---------------------------------------------------------------------------


def test_categories_non_dosleg_intactes():
    """Communiqué, JORF, etc. ne sont pas touchés."""
    rows = [
        {
            "category": "communiques",
            "title": "Sport professionnel : organisation et gestion",
            "published_at": "2025-01-01T00:00:00",
            "raw": {},
        },
        _agenda(
            "Examen sport professionnel organisation gestion financement",
            "2026-05-12T09:00:00",
        ),
    ]
    _boost_dosleg_with_agenda(rows)
    assert rows[0]["published_at"] == "2025-01-01T00:00:00"
    assert "effective_at_source" not in rows[0]["raw"]
