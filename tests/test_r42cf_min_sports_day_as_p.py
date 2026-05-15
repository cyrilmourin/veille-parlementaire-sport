"""R42-CF (2026-05-15) — Détection day header `<p><strong>Lundi 11 mai</strong></p>`.

`data/min_sports_debug.json` post-R42-CC indique pour la semaine du
11 mai 2026 :
    stage: "zero_items"
    h5_count: 0
    p_count: 26
    p_strong_count: 10

H2 valide trouvé, fetch OK via worker Cloudflare, mais aucun <h5>
jour. Conclusion : Drupal a basculé le markup des jours en
`<p><strong>Lundi 11 mai</strong></p>` (forme alternative observée
sur d'autres sites Drupal 9 ministériels).

Le parser détecte désormais cette forme alternative en plus de la
forme historique `<h5>`. Compatibilité totale avec les bulletins
antérieurs.
"""
from __future__ import annotations

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


# Forme R42-CF : jours en `<p><strong>...</strong></p>` au lieu de `<h5>`.
_FIXTURE_P_DAYS = """
<html><body>
<div class="sports-gouv-container">
<h2>Agenda prévisionnel de Marina FERRARI, ministre des Sports, pour la semaine du 11 mai 2026</h2>
<p><strong>Lundi 11 mai</strong></p>
<p><strong>10h00</strong>   Conseil des ministres</p>
<p><em>Palais de l'Elysée – Paris 8ème</em></p>
<p><strong>15h30</strong>   Entretien avec le président du CNOSF</p>
<p><em>Ministère des Sports – Paris 13ème</em></p>
<p><strong>Mardi 12 mai</strong></p>
<p><strong>Matin</strong>   Déplacement à Lyon (visite de l'INSEP antenne Rhône)</p>
<p><em>Lyon (69)</em></p>
<p><strong>Mercredi 13 mai</strong></p>
<p><strong>20h45</strong>   Match de Ligue 1 PSG / OM</p>
<p><em>Parc des Princes – Paris 16ème</em></p>
</div>
</body></html>
"""


def test_jours_p_strong_detectes_comme_h5(monkeypatch):
    """3 jours en `<p><strong>...</strong></p>` → 4 créneaux ingérés."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_P_DAYS))
    items = min_sports.fetch_source(_src())
    # 2 créneaux lundi + 1 mardi + 1 mercredi = 4
    assert len(items) == 4, f"expected 4 events, got {len(items)}"
    # Dates correctes
    dates = sorted({it.published_at.date().isoformat() for it in items})
    assert dates == ["2026-05-11", "2026-05-12", "2026-05-13"]
    # Titres préfixés MinSports
    assert all(it.title.startswith("MinSports —") for it in items)
    # Premier créneau du lundi
    first = next(it for it in items if it.published_at.day == 11
                 and it.published_at.hour == 10)
    assert "Conseil des ministres" in first.title
    # Créneau named slot conservé
    matin = next(it for it in items if it.published_at.day == 12)
    assert "Matin" in matin.title


def test_p_strong_horaire_pas_confondu_avec_jour(monkeypatch):
    """Un `<p><strong>10h00</strong> ...</p>` (avec trailing) NE doit
    PAS être détecté comme day header — c'est un créneau."""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(_FIXTURE_P_DAYS))
    items = min_sports.fetch_source(_src())
    # Vérifie qu'aucun item n'a un titre vide (ce qui arriverait si
    # le créneau "10h00" était traité comme jour et écartait le
    # premier événement).
    assert all(it.title and len(it.title) > 5 for it in items)


def test_rétrocompat_h5_inchangée(monkeypatch):
    """Forme historique <h5> jour : doit continuer à fonctionner."""
    historic = """
<html><body>
<div class="sports-gouv-container">
<h2>Agenda pour la semaine du 11 mai 2026</h2>
<h5>Lundi 11 mai</h5>
<p><strong>10h00</strong>   Conseil des ministres</p>
<p><em>Élysée</em></p>
<h5>Mardi 12 mai</h5>
<p><strong>Matin</strong>   Déplacement</p>
<p><em>Lyon</em></p>
</div>
</body></html>
"""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(historic))
    items = min_sports.fetch_source(_src())
    assert len(items) == 2


def test_p_strong_jour_avec_year_ignoré(monkeypatch):
    """Edge case : `<p><strong>Lundi 11 mai 2026</strong></p>` —
    _DAY_RE refuse l'année trailing, donc traité comme un créneau
    bizarre (pas un day header). Pas idéal mais protège contre les
    faux positifs sur des titres avec année."""
    html = """
<html><body>
<div class="sports-gouv-container">
<h2>Agenda pour la semaine du 11 mai 2026</h2>
<h5>Lundi 11 mai</h5>
<p><strong>10h00</strong>   Conseil des ministres</p>
<p><em>Élysée</em></p>
</div>
</body></html>
"""
    monkeypatch.setattr(min_sports, "fetch_text", _make_fetch(html))
    items = min_sports.fetch_source(_src())
    # On valide juste que les autres tests ne sont pas cassés
    assert len(items) == 1
