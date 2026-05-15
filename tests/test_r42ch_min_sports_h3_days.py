"""R42-CH (2026-05-15) — Fix structurel min_sports_agenda.

Diagnostic (suite à R42-CA→CG) : sports.gouv.fr a refondu le markup
de l'agenda ministre. Les en-têtes de jours sont passés de `<h5>` à
`<h3>` (semaine du 11 mai 2026, page sauvegardée par Cyril dans
`tests/Agenda prévisionnel de Marina Ferrari _ sports.gouv.fr.html`).

Structure réelle observée :
  <div class="sports-gouv-container">
    <h2>...semaine du 11 mai 2026</h2>
    <h3>Lundi 11 mai</h3>
    <p><strong>10h00</strong>  Description...</p>
    <p>Lieu — Information presse diffusée</p>
    <h3>Mardi 12 mai</h3>
    ...
  </div>

Avant ce fix : `find_all(["h5", "p"], recursive=False)` ne trouvait
aucun <h5> → 0 item depuis le 2026-05-08. Symptôme :
`min_sports_agenda` avec `last_fetched: 0` dans pipeline_health.json.

Fix : étendre la collecte aux balises `h3`, `h4`, `h5` (tolérance
forward-compatible) ET valider chaque header via `_parse_day_header`
avant de l'utiliser comme jour (les <h3> parasites = ignorés).

Ce fichier teste le parsing sur le HTML réel sauvegardé localement.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_FIXTURE_PATH = Path(__file__).parent / "fixture_min_sports_agenda_2026-05-11.html"

_SRC = {"id": "min_sports_agenda", "category": "agenda"}
_AGENDA_URL = "https://www.sports.gouv.fr/agenda-previsionnel-de-marina-ferrari-1787"


def _call(html: str):
    from src.sources.min_sports import _parse_agenda_html
    return _parse_agenda_html(
        html=html,
        src=_SRC,
        agenda_url=_AGENDA_URL,
        sid="min_sports_agenda",
        cat="agenda",
        chamber="MinSports",
        title_prefix="MinSports",
    )


@pytest.fixture(scope="module")
def real_html() -> str:
    if not _FIXTURE_PATH.exists():
        pytest.skip(
            f"Fixture HTML absente ({_FIXTURE_PATH.name}). "
            "Cyril doit l'enregistrer avant de lancer ce test."
        )
    return _FIXTURE_PATH.read_text(encoding="utf-8", errors="replace")


def test_fixture_loads_and_has_expected_markers(real_html):
    """Garde-fou : la fixture sauvegardée par Cyril contient bien
    les marqueurs attendus (h2 semaine, h3 jours)."""
    assert "semaine du 11" in real_html.lower()
    assert "Lundi 11 mai" in real_html
    assert "Mardi 12 mai" in real_html
    # Confirme la cause racine : plus de <h5>, mais bien des <h3>
    assert "<h5>Lundi" not in real_html
    assert "<h3>" in real_html


def test_extract_events_returns_items(real_html):
    """Le scraper doit produire au moins 5 events sur la semaine."""
    items = _call(real_html)
    assert len(items) >= 5, (
        f"Attendu >= 5 items, obtenu {len(items)}. "
        "Probable bug de parsing des <h3> jours."
    )


def test_extracts_known_event_lundi(real_html):
    """Lundi 11 mai 10h00 : « Conseil des ministres de la Jeunesse… »"""
    items = _call(real_html)
    titles = [it.title for it in items]
    matched = [
        t for t in titles
        if "10h00" in t and "Conseil des ministres" in t
        and ("Jeunesse" in t or "jeunesse" in t)
    ]
    assert matched, (
        f"Event Lundi 10h00 Conseil ministres Jeunesse non extrait. "
        f"Titres trouvés (5 premiers) : {titles[:5]}"
    )


def test_extracts_event_with_location(real_html):
    """Le lieu (« Bruxelles (Belgique) ») doit être capté dans le summary."""
    items = _call(real_html)
    bruxelles_items = [it for it in items if "Bruxelles" in (it.summary or "")]
    assert bruxelles_items, "Aucun item avec lieu Bruxelles dans summary"


def test_dates_in_week_starting_monday_11_may(real_html):
    """Tous les items doivent avoir leur published_at dans la semaine
    du 11 mai 2026 (lundi 11 → dimanche 17)."""
    from datetime import datetime
    items = _call(real_html)
    week_start = datetime(2026, 5, 11)
    week_end = datetime(2026, 5, 17, 23, 59)
    for it in items:
        assert it.published_at is not None, f"published_at manquant: {it.title}"
        assert week_start <= it.published_at <= week_end, (
            f"Date hors semaine 11-17 mai : {it.published_at} pour {it.title}"
        )


def test_no_phantom_day_from_parasite_h3(real_html):
    """Les <h3> parasites (navigation, autres sections) ne doivent pas
    être interprétés comme des jours (sinon on aurait des items à des
    dates absurdes)."""
    items = _call(real_html)
    # Pas plus de 30 events sur une semaine — sinon c'est qu'un h3
    # parasite a fait sauter le bucket et tout le bas de page a été
    # pris pour des events.
    assert len(items) < 30, (
        f"Trop d'items extraits ({len(items)}) — probable h3 parasite "
        "interprété comme jour. Filtre _parse_day_header insuffisant."
    )


# ---------------------------------------------------------------------------
# Rétrocompat : si sports.gouv.fr rebascule sur <h5>, on doit toujours parser.
# Cyril 2026-05-15 : « je veux qu'il parse H3 ou H5 ».
# ---------------------------------------------------------------------------

_LEGACY_H5_HTML = """
<html><body>
  <div class="sports-gouv-container">
    <h2>Agenda prévisionnel de la ministre pour la semaine du 11 mai 2026</h2>
    <h5>Lundi 11 mai</h5>
    <p><strong>10h00</strong>  Conseil des ministres de la Jeunesse de l'UE</p>
    <p><em>Bruxelles (Belgique)</em></p>
    <h5>Mardi 12 mai</h5>
    <p><strong>15h00</strong>  Conseil des ministres des Sports de l'UE</p>
    <p><em>Bruxelles (Belgique)</em></p>
  </div>
</body></html>
"""


def test_parses_legacy_h5_format():
    """Le parseur accepte toujours le format <h5> historique (au cas où
    sports.gouv.fr rebascule, ou pour parser d'anciennes pages cachées)."""
    items = _call(_LEGACY_H5_HTML)
    assert len(items) == 2, f"Attendu 2 events H5, obtenu {len(items)}"
    titles = [it.title for it in items]
    assert any("Conseil des ministres de la Jeunesse" in t for t in titles)
    assert any("Conseil des ministres des Sports" in t for t in titles)


def test_parses_mixed_h3_and_h5():
    """Edge case : page avec mélange de <h3> et <h5> (improbable mais
    le parser doit tenir). Garde-fou pour ne pas régresser sur un seul
    des deux formats."""
    mixed = """
    <html><body>
      <div class="sports-gouv-container">
        <h2>Agenda prévisionnel pour la semaine du 11 mai 2026</h2>
        <h3>Lundi 11 mai</h3>
        <p><strong>10h00</strong>  Event A</p>
        <h5>Mardi 12 mai</h5>
        <p><strong>15h00</strong>  Event B</p>
      </div>
    </body></html>
    """
    items = _call(mixed)
    assert len(items) == 2, f"Mélange H3+H5 doit produire 2 items, obtenu {len(items)}"
    by_title = {it.title for it in items}
    assert any("Event A" in t for t in by_title)
    assert any("Event B" in t for t in by_title)
