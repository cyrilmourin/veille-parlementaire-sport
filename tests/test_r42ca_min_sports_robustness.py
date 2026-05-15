"""R42-CA (2026-05-15) — Robustesse du parser min_sports_agenda.

Cyril 2026-05-15 : « il y a un agenda publié » alors que le pipeline
remonte `last_fetched: 0` pour `min_sports_agenda` depuis le 8 mai.
Aucune exception, aucun item — symptôme d'un changement éditorial silencieux
côté sports.gouv.fr.

Trois axes de robustesse :

1. **Regex H2 intervalle** : accepter « semaine du 11 au 15 mai 2026 »
   en plus de « semaine du 11 mai 2026 ». Cause la plus probable du
   passage à 0 (rééditorialisation Drupal du libellé).

2. **Fallback DOM** : si `<h5>` ne sont plus enfants directs de `h2.parent`
   (repackage Drupal en sous-div `paragraph`), basculer en
   `recursive=True` au lieu de retourner [] silencieusement.

3. **Logging diagnostic** : H2 vus loggés en WARNING, parse à 0 items
   loggé en WARNING avec compteurs. Indispensable pour audit post-run.
"""
from __future__ import annotations

import logging

from src.sources import min_sports


def _src() -> dict:
    return {
        "id": "min_sports_agenda",
        "url": "https://www.sports.gouv.fr/agenda-previsionnel-de-marina-ferrari-1787",
        "format": "min_sports_agenda_hebdo",
        "chamber": "MinSports",
        "title_prefix": "MinSports —",
        "category": "agenda",
    }


def _make_fetch(text: str):
    def _fake(url: str, **kwargs) -> str:
        return text
    return _fake


# ---------------------------------------------------------------------------
# Axe 1 — Regex H2 intervalle
# ---------------------------------------------------------------------------

_FIXTURE_INTERVALLE = """
<html><body>
<div class="sports-gouv-container">
<h2>Agenda prévisionnel de Marina FERRARI, ministre des Sports, pour la semaine du 11 au 15 mai 2026</h2>
<h5>Lundi 11 mai</h5>
<p><strong>10h00</strong>   Réunion stratégique JO 2030</p>
<p><em>Ministère des Sports – Paris</em></p>
<h5>Mardi 12 mai</h5>
<p><strong>Après-midi</strong>   Déplacement Lyon</p>
<p><em>Lyon (69)</em></p>
</div>
</body></html>
"""


def test_regex_accepte_intervalle_au(monkeypatch):
    """« semaine du 11 au 15 mai 2026 » doit être parsé comme « semaine du 11 mai 2026 »."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_INTERVALLE))
    items = min_sports.fetch_source(_src())
    assert len(items) == 2
    # week_start = lundi 11 mai
    assert items[0].published_at.date().isoformat() == "2026-05-11"
    assert items[1].published_at.date().isoformat() == "2026-05-12"


def test_regex_accepte_format_historique(monkeypatch):
    """Le format historique « semaine du 20 avril 2026 » continue de matcher."""
    html = _FIXTURE_INTERVALLE.replace(
        "pour la semaine du 11 au 15 mai 2026",
        "pour la semaine du 11 mai 2026",
    )
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(html))
    items = min_sports.fetch_source(_src())
    assert len(items) == 2


# ---------------------------------------------------------------------------
# Axe 2 — Fallback recursive=True quand H5 wrappés
# ---------------------------------------------------------------------------

_FIXTURE_WRAPPED = """
<html><body>
<div class="sports-gouv-container">
<h2>Agenda prévisionnel pour la semaine du 11 mai 2026</h2>
<div class="paragraph paragraph--type--text">
  <h5>Lundi 11 mai</h5>
  <p><strong>10h00</strong>   Conseil des ministres</p>
  <p><em>Élysée</em></p>
</div>
<div class="paragraph paragraph--type--text">
  <h5>Mardi 12 mai</h5>
  <p><strong>14h00</strong>   Entretien ANS</p>
  <p><em>Paris</em></p>
</div>
</div>
</body></html>
"""


def test_fallback_recursive_quand_h5_wrappes(monkeypatch, caplog):
    """Si h5/p sont dans des sous-divs (repackage Drupal), le fallback
    `recursive=True` doit récupérer les jours au lieu de retourner []."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_WRAPPED))
    with caplog.at_level(logging.WARNING):
        items = min_sports.fetch_source(_src())
    assert len(items) == 2
    # WARNING émis sur le fallback
    assert any(
        "fallback recursive=True" in r.message for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Axe 3 — Logging diagnostic
# ---------------------------------------------------------------------------

_FIXTURE_H2_DIFFERENT = """
<html><body>
<div class="sports-gouv-container">
<h2>Actualités du ministère</h2>
<h2>Communiqués récents</h2>
<p>Pas d'agenda cette semaine.</p>
</div>
</body></html>
"""


def test_logging_h2_vus_quand_aucun_match(monkeypatch, caplog):
    """Aucun H2 ne matche → on logge les H2 vus pour faciliter le diag."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_H2_DIFFERENT))
    with caplog.at_level(logging.WARNING):
        items = min_sports.fetch_source(_src())
    assert items == []
    log_msg = " ".join(r.message for r in caplog.records)
    assert "H2 vus" in log_msg
    assert "Actualités du ministère" in log_msg


_FIXTURE_H2_OK_MAIS_PAS_JOURS = """
<html><body>
<div class="sports-gouv-container">
<h2>Agenda prévisionnel pour la semaine du 11 mai 2026</h2>
<p>Texte d'introduction sans jours.</p>
</div>
</body></html>
"""


def test_logging_warning_quand_h2_ok_mais_zero_items(monkeypatch, caplog):
    """H2 valide mais aucun jour parsé → WARNING avec compteurs."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_H2_OK_MAIS_PAS_JOURS))
    with caplog.at_level(logging.WARNING):
        items = min_sports.fetch_source(_src())
    assert items == []
    log_msg = " ".join(r.message for r in caplog.records)
    assert "0 items malgré H2 valide" in log_msg
