"""R42-CI (2026-05-15) — Actualisation des dates des items agenda.

Cyril 2026-05-15 : « La séance (Sport pro) n'est plus le 18 mai, mais le
fait qu'elle ait été décalée (sans date pour l'heure je crois) n'est pas
pris en compte, il faut que les occurrences ingérées restent actualisables ».

Cause racine : `store.upsert_many` utilisait
    published_at = COALESCE(excluded.published_at, items.published_at)
→ si AN re-publie l'item avec `timeStampDebut=NULL` (séance reportée
sans nouvelle date), COALESCE garde l'ancienne date périmée.

Fix R42-CI : pour `category='agenda'`, écraser SYSTÉMATIQUEMENT
`published_at` par la valeur source (même NULL). Pour les autres
catégories on garde COALESCE (protège contre un scraper buggy qui
effacerait une date déjà correcte).

Côté affichage (agenda/list.html) : un item agenda sans date est
classé dans le bucket « À venir » en tête (édito : un report est plus
intéressant à voir qu'un événement passé) avec un badge orange
« 📅 À reprogrammer ».
"""
from __future__ import annotations

from datetime import datetime

from src.models import Item
from src.store import Store


def test_upsert_agenda_clears_published_at_when_source_null(tmp_path):
    """Un item agenda re-upserted avec published_at=None doit voir
    sa date écrasée à NULL (signal officiel de séance reportée)."""
    db = Store(str(tmp_path / "test.sqlite3"))
    it_initial = Item(
        source_id="an_agenda",
        uid="RUANR5L17S2026IDS_seance_18mai",
        category="agenda",
        chamber="AN",
        title="Séance publique — PPL Sport pro",
        url="https://example.com/seance",
        published_at=datetime(2026, 5, 18, 15, 0),
        summary="",
        raw={"path": "agenda"},
    )
    db.upsert_many([it_initial])

    it_reporte = Item(
        source_id="an_agenda",
        uid="RUANR5L17S2026IDS_seance_18mai",
        category="agenda",
        chamber="AN",
        title="Séance publique — PPL Sport pro (reportée)",
        url="https://example.com/seance",
        published_at=None,  # AN n'a pas (encore) reposé de date
        summary="",
        raw={"path": "agenda"},
    )
    db.upsert_many([it_reporte])

    cur = db.conn.cursor()
    cur.execute(
        "SELECT published_at FROM items WHERE uid = ?",
        ("RUANR5L17S2026IDS_seance_18mai",),
    )
    row = cur.fetchone()
    assert row[0] is None, (
        f"Attendu published_at=NULL après report ; obtenu {row[0]!r}. "
        "Le COALESCE protège encore la catégorie agenda."
    )


def test_upsert_agenda_refreshes_to_new_date(tmp_path):
    """Cas symétrique : AN repose une nouvelle date après report. On
    doit prendre la nouvelle date (et non garder l'ancienne via COALESCE)."""
    db = Store(str(tmp_path / "test.sqlite3"))
    it_initial = Item(
        source_id="an_agenda",
        uid="seance_decalage",
        category="agenda",
        chamber="AN",
        title="Séance",
        url="https://example.com/s",
        published_at=datetime(2026, 5, 18, 15, 0),
        summary="",
        raw={},
    )
    db.upsert_many([it_initial])
    it_new = Item(
        source_id="an_agenda",
        uid="seance_decalage",
        category="agenda",
        chamber="AN",
        title="Séance",
        url="https://example.com/s",
        published_at=datetime(2026, 5, 22, 15, 0),  # repoussée de 4 jours
        summary="",
        raw={},
    )
    db.upsert_many([it_new])

    cur = db.conn.cursor()
    cur.execute("SELECT published_at FROM items WHERE uid='seance_decalage'")
    row = cur.fetchone()
    assert row[0] is not None
    assert "2026-05-22" in row[0], (
        f"Nouvelle date doit remplacer l'ancienne ; obtenu {row[0]!r}"
    )


def test_upsert_non_agenda_preserves_date_when_source_null(tmp_path):
    """Non-régression : pour les catégories AUTRES qu'agenda, le COALESCE
    historique protège la date contre un scraper buggy qui renverrait None."""
    db = Store(str(tmp_path / "test.sqlite3"))
    it1 = Item(
        source_id="senat_questions_1an",
        uid="Q123",
        category="questions",
        chamber="Senat",
        title="Question écrite n°123",
        url="https://example.com/q123",
        published_at=datetime(2026, 5, 10, 0, 0),
        summary="",
        raw={},
    )
    db.upsert_many([it1])

    it2 = Item(
        source_id="senat_questions_1an",
        uid="Q123",
        category="questions",
        chamber="Senat",
        title="Question écrite n°123",
        url="https://example.com/q123",
        published_at=None,  # scraper buggy ou champ vide ponctuellement
        summary="",
        raw={},
    )
    db.upsert_many([it2])

    cur = db.conn.cursor()
    cur.execute("SELECT published_at FROM items WHERE uid='Q123'")
    row = cur.fetchone()
    assert row[0] is not None, (
        "Pour les questions, la date initiale doit être préservée si "
        "le nouvel item arrive avec None (protection scraper buggy)."
    )
    assert "2026-05-10" in row[0]


def test_upsert_non_agenda_preserves_date_for_dossiers_legislatifs(tmp_path):
    """Variante du test précédent : dossiers_legislatifs aussi protégés."""
    db = Store(str(tmp_path / "test.sqlite3"))
    it1 = Item(
        source_id="an_dossiers_legislatifs",
        uid="DLR5L17N51732",
        category="dossiers_legislatifs",
        chamber="AN",
        title="PPL Sport pro",
        url="https://example.com/d",
        published_at=datetime(2026, 5, 1),
        summary="",
        raw={},
    )
    db.upsert_many([it1])
    it2 = Item(
        source_id="an_dossiers_legislatifs",
        uid="DLR5L17N51732",
        category="dossiers_legislatifs",
        chamber="AN",
        title="PPL Sport pro",
        url="https://example.com/d",
        published_at=None,
        summary="",
        raw={},
    )
    db.upsert_many([it2])

    cur = db.conn.cursor()
    cur.execute(
        "SELECT published_at FROM items WHERE uid='DLR5L17N51732'"
    )
    row = cur.fetchone()
    assert row[0] is not None and "2026-05-01" in row[0]
