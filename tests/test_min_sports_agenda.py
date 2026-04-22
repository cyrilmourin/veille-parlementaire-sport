"""Tests du connecteur `min_sports` (R15, 2026-04-22).

Scrape le bulletin hebdomadaire d'agenda du ministère des Sports
(https://www.sports.gouv.fr/agenda-previsionnel-de-<ministre>-<id>).
Couvre :
- Parse nominal du bulletin Marina Ferrari (5 jours, ~10 créneaux)
- Résolution du slug variable depuis la home
- Créneaux nommés (Matin / Après-midi / Journée) vs horaires précis
- Descriptions contenant des `<strong>` imbriqués (noms de personnes)
- Lieu optionnel via `<em>`
- Robustesse : pas de `<h2>` de semaine → retour vide
- Routage `format=min_sports_agenda_hebdo` via normalize._dispatch
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src import normalize  # noqa: E402
from src.sources import min_sports  # noqa: E402


# Fixture : extrait représentatif du bulletin Marina Ferrari (20 avril 2026).
# Gardé minimal mais préservant toutes les variantes de créneaux que le
# scraper doit gérer.
_FIXTURE_FULL = """
<html><body>
<div class="sports-gouv-container">
<h2>Agenda prévisionnel de Marina FERRARI, ministre des Sports, de la Jeunesse et de la Vie associative, pour la semaine du 20 avril 2026</h2>
<p> </p>
<h5><span><span><span>Lundi 20 avril </span></span></span></h5>
<p><span><span><span><strong>Après-midi</strong>   Réunion des membres du Bureau exécutif du COJOP Alpes françaises 2030</span></span></span></p>
<p><span><span><span><em>Décines-Charpieu (69)</em></span></span></span></p>
<p> </p>
<h5><span><span><span>Mardi 21 avril </span></span></span></h5>
<p><span><span><span><strong>08h45</strong>    Déplacement à Grenoble à l'occasion de l'ouverture du salon Mountain Planet</span></span></span></p>
<p><span><span><span><em>IP diffusée – Grenoble (38)</em></span></span></span></p>
<p><span><span><span><strong>16h15</strong>     Entretien avec <strong>Kenny JEAN-MARIE</strong>, préfigurateur de la ligue guadeloupéenne de football</span></span></span></p>
<p><span><span><span><em>Ministère des Sports, de la Jeunesse et de la Vie associative – Paris 13ème</em></span></span></span></p>
<p><span><span><span><strong>19h00</strong>    Remise de la Légion d'Honneur à <strong>Guillaume RODELET</strong> </span></span></span></p>
<p><span><span><span><em>Ministère des Sports, de la Jeunesse et de la Vie associative – Paris 13ème</em></span></span></span></p>
<p> </p>
<h5><span><span><span>Mercredi 22 avril</span></span></span></h5>
<p><span><span><span><strong>10h00</strong>    Conseil des ministres</span></span></span></p>
<p><span><span><span><em>Palais de l'Elysée – Paris 8ème </em></span></span></span></p>
<p> </p>
<h5><span><span><span>Vendredi 24 avril  </span></span></span></h5>
<p><span><span><span><strong>Journée</strong>   Déplacement à Breal-sous-Monfort consacré aux colonies de vacances </span></span></span></p>
<p><span><span><span><em>NAR à venir – Ille-et-Vilaine (35)</em></span></span></span></p>
<p><span><span><span><strong>20h45</strong>     Match de Ligue 1 Brest / Lens</span></span></span></p>
<p><span><span><span><em>Stade Francis Le Blé – Brest (29)</em></span></span></span></p>
</div>
</body></html>
"""


# ------------------------------------------------------------------
# Helpers pour injecter du HTML sans réseau
# ------------------------------------------------------------------

def _make_fetch(text: str):
    def _fake(url: str) -> str:
        return text
    return _fake


def _src(**overrides):
    base = {
        "id": "min_sports_agenda",
        "url": "https://www.sports.gouv.fr/agenda-previsionnel-de-marina-ferrari-1787",
        "format": "min_sports_agenda_hebdo",
        "chamber": "MinSports",
        "title_prefix": "MinSports —",
        "category": "agenda",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# Tests de parsing HTML
# ------------------------------------------------------------------

def test_parse_nominal_full_week(monkeypatch):
    """Parse nominal : 8 créneaux dans la fixture, titres préfixés,
    week_start correct, dates croissantes lun→ven."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_FULL))
    items = min_sports.fetch_source(_src())

    # 8 créneaux dans la fixture : 1 lundi + 3 mardi + 1 mercredi + 2 vendredi
    # = 7 (j'ai oublié un ? recomptons : Lundi=1, Mardi=3, Mercredi=1, Vendredi=2 = 7)
    assert len(items) == 7, f"expected 7 events, got {len(items)}"

    titles = [it.title for it in items]
    assert all(t.startswith("MinSports —") for t in titles)

    # Week start = 2026-04-20 → lundi=20, mardi=21, mercredi=22, vendredi=24
    days = sorted({it.published_at.date().isoformat() for it in items})
    assert days == ["2026-04-20", "2026-04-21", "2026-04-22", "2026-04-24"]


def test_parse_time_formats(monkeypatch):
    """Les heures « HHhMM » sont parsées en titre lisible HH:MM."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_FULL))
    items = min_sports.fetch_source(_src())

    # Conseil des ministres = mercredi 10h00
    cm = next(
        it for it in items
        if "Conseil des ministres" in it.title
    )
    assert cm.published_at.hour == 10
    assert cm.published_at.minute == 0
    # Format titre : "10h00 — Conseil des ministres"
    assert "10h00" in cm.title
    assert cm.raw["location"].startswith("Palais de l'Elysée")


def test_parse_named_slot_afternoon(monkeypatch):
    """« Après-midi » est converti en 14h00 par défaut et conservé
    tel quel dans le titre."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_FULL))
    items = min_sports.fetch_source(_src())

    cojop = next(it for it in items if "COJOP" in it.title)
    assert cojop.published_at.hour == 14
    assert cojop.published_at.minute == 0
    assert "Après-midi" in cojop.title
    assert cojop.published_at.date().isoformat() == "2026-04-20"


def test_parse_named_slot_journee(monkeypatch):
    """« Journée » → 9h00 par défaut, préservé dans le titre."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_FULL))
    items = min_sports.fetch_source(_src())

    breal = next(it for it in items if "Breal" in it.title)
    assert breal.published_at.hour == 9
    assert "Journée" in breal.title


def test_description_preserves_nested_strong(monkeypatch):
    """Les `<strong>` imbriqués (noms de personnes dans la description)
    doivent apparaître en clair dans la description, pas être strippés."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_FULL))
    items = min_sports.fetch_source(_src())

    kenny = next(it for it in items if "Kenny JEAN-MARIE" in it.title)
    assert "préfigurateur" in kenny.title
    assert kenny.published_at.hour == 16
    assert kenny.published_at.minute == 15


def test_location_from_em(monkeypatch):
    """Le <em> qui suit un event devient le lieu dans raw + summary."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_FULL))
    items = min_sports.fetch_source(_src())

    brest = next(it for it in items if "Brest" in it.title)
    assert "Stade Francis Le Blé" in brest.raw["location"]
    assert "Lieu :" in brest.summary


def test_uid_is_stable_across_reparse(monkeypatch):
    """Réingérer deux fois la même page doit produire les mêmes uid
    (dérivé de week_start + day + slot + desc). Protège contre les
    re-imports en boucle qui doubleraient les items dans le store."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_FULL))
    items1 = min_sports.fetch_source(_src())
    items2 = min_sports.fetch_source(_src())

    assert [it.uid for it in items1] == [it.uid for it in items2]
    # uid est un hash court 16 chars
    assert all(len(it.uid) == 16 for it in items1)


def test_raw_fields_populated(monkeypatch):
    """Vérifie les champs diagnostic dans raw (utile pour audit)."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_FULL))
    items = min_sports.fetch_source(_src())

    cm = next(it for it in items if "Conseil des ministres" in it.title)
    assert cm.raw["path"] == "min_sports:agenda_hebdo"
    assert cm.raw["week_start"] == "2026-04-20"
    assert cm.raw["day"] == "2026-04-22"
    assert cm.raw["slot_raw"] == "10h00"


# ------------------------------------------------------------------
# Tests de résolution du slug
# ------------------------------------------------------------------

def test_resolve_from_home(monkeypatch):
    """Quand l'URL pointe vers la home, le scraper doit suivre le lien
    vers /agenda-previsionnel-de-<nom>-<id>."""
    home_html = (
        '<html><body>'
        '<a href="/la-ministre">La ministre</a>'
        '<a href="/agenda-previsionnel-de-marina-ferrari-1787">Agenda</a>'
        '</body></html>'
    )
    fetched_urls: list[str] = []

    def _fake(url: str) -> str:
        fetched_urls.append(url)
        if url.endswith("/"):
            return home_html
        return _FIXTURE_FULL

    monkeypatch.setattr(min_sports, "fetch_text", _fake)
    items = min_sports.fetch_source(_src(
        url="https://www.sports.gouv.fr/",
    ))
    assert len(items) >= 1
    # Le scraper a fetché la home PUIS la page agenda résolue
    assert any("agenda-previsionnel-de-" in u for u in fetched_urls)


def test_resolve_missing_link_returns_empty(monkeypatch):
    """Si la home n'a plus de lien vers l'agenda (régression éditoriale),
    retourner liste vide sans crasher."""
    home_html = '<html><body><p>Pas d\'agenda ici</p></body></html>'
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(home_html))
    items = min_sports.fetch_source(_src(url="https://www.sports.gouv.fr/"))
    assert items == []


def test_direct_agenda_url_skips_resolution(monkeypatch):
    """Si l'URL pointe déjà vers une page agenda, on parse directement."""
    calls: list[str] = []

    def _fake(url: str) -> str:
        calls.append(url)
        return _FIXTURE_FULL

    monkeypatch.setattr(min_sports, "fetch_text", _fake)
    min_sports.fetch_source(_src(
        url="https://www.sports.gouv.fr/agenda-previsionnel-de-marina-ferrari-1787",
    ))
    # Un seul fetch : pas de passage par la home
    assert len(calls) == 1


# ------------------------------------------------------------------
# Tests de robustesse
# ------------------------------------------------------------------

def test_no_h2_returns_empty(monkeypatch):
    """Page sans <h2> contenant « semaine du … » (cas de maintenance
    ou régression de template) → liste vide, pas de crash."""
    bogus = "<html><body><h2>Page en maintenance</h2></body></html>"
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(bogus))
    items = min_sports.fetch_source(_src(
        url="https://www.sports.gouv.fr/agenda-previsionnel-de-marina-ferrari-1787",
    ))
    assert items == []


def test_fetch_error_returns_empty(monkeypatch):
    """Erreur réseau sur le fetch de la page agenda → liste vide."""
    def _boom(url: str) -> str:
        raise TimeoutError("simulated")
    monkeypatch.setattr(min_sports, "fetch_text", _boom)
    items = min_sports.fetch_source(_src(
        url="https://www.sports.gouv.fr/agenda-previsionnel-de-marina-ferrari-1787",
    ))
    assert items == []


def test_unknown_format_returns_empty():
    """Un format non géré via min_sports.fetch_source ne crashe pas."""
    items = min_sports.fetch_source({
        "id": "x",
        "format": "min_sports_unknown",
        "url": "y",
    })
    assert items == []


# ------------------------------------------------------------------
# Tests de routage dans normalize
# ------------------------------------------------------------------

def test_dispatch_routes_min_sports_format():
    """Le dispatcher principal doit router `format=min_sports_*` vers
    `min_sports.fetch_source` (quel que soit le groupe YAML)."""
    fn = normalize._dispatch("ministeres", {
        "id": "min_sports_agenda",
        "format": "min_sports_agenda_hebdo",
    })
    assert fn is min_sports.fetch_source


def test_dispatch_preserves_html_for_min_sports_presse():
    """Les sources ministère des Sports en `format=html` (espace
    presse, actualités) doivent rester routées vers html_generic,
    pas vers min_sports (qui ne sait parser QUE l'agenda hebdo)."""
    from src.sources import html_generic
    fn = normalize._dispatch("ministeres", {
        "id": "min_sports_presse",
        "format": "html",
    })
    assert fn is html_generic.fetch_source
