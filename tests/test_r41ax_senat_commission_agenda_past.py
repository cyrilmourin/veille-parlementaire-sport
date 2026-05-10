"""R41-AX (2026-05-10) — Tests bloc historique sur agenda commission Sénat.

Contexte : la page `/agenda-de-la-commission.html` exposait jusqu'ici
seulement le bloc « Prochaines réunions ». Cyril a constaté qu'une
audition (Tavernost, 6 mai 2026) connue côté AGLAE Sénat
(`/aglae/Instance-0-AFCL/agl06052026.html`) n'apparaissait plus dans
la veille une fois passée. R41-AX ajoute le parsing d'un bloc
historique sur la même page (« Dernières réunions », « Réunions
précédentes », « Réunions passées »), avec inversion de l'heuristique
year+1 → year-1 pour les events lointains du futur.

Tests offline : pas de réseau, regex testée sur fixtures HTML.
"""
from __future__ import annotations

from datetime import datetime

from src.sources import senat_commission_agenda as mod


# ---------------------------------------------------------------------------
# Fixtures HTML
# ---------------------------------------------------------------------------

def _li(*, day: int, month: str, title: str, salle: str, heure: str) -> str:
    return (
        f'<li class="list-group-item">'
        f'<div class="row">'
        f'<div class="col-2"><div class="d-flex flex-column">'
        f'<span class="display-4 ff-alt lh-1">{day}</span>'
        f'<span class="mt-n1 fw-semibold lh-1">{month}</span>'
        f'</div></div>'
        f'<div class="col-10 d-flex flex-column">'
        f'<h4 class="list-group-title line-clamp-3" title="{title}">{title}</h4>'
        f'<p class="list-group-subtitle">{salle}</p>'
        f'<time datetime="{heure}"><i class="bi bi-clock"></i> {heure}h</time>'
        f'</div></div></li>'
    )


def _wrap_block(h3_label: str, *lis: str) -> str:
    return (
        f'<h3 class="mt-md-1 mt-lg-2">{h3_label}</h3>'
        f'<ul class="list-group list-group-flush">'
        + "".join(lis)
        + '</ul>'
    )


def _page(*blocks: str) -> str:
    return '<html><body>' + "".join(blocks) + '</body></html>'


# ---------------------------------------------------------------------------
# _resolve_date avec prefer_past=True
# ---------------------------------------------------------------------------

def test_resolve_date_past_block_recent_future_kept_as_is():
    """Bloc historique : un 6 mai vu le 10 mai → 2026-05-06 (récent passé)."""
    now = datetime(2026, 5, 10, 9, 0)
    out = mod._resolve_date(6, 5, "10:00", now, prefer_past=True)
    assert out == datetime(2026, 5, 6, 10, 0)


def test_resolve_date_past_block_future_within_30d_kept_current_year():
    """Bloc historique : un 20 mai vu le 10 mai → 2026-05-20 (futur ≤ 30j accepté).

    On évite de basculer year-1 sur des dates très proches du présent —
    ça arrive en bordure si une réunion vient juste de passer et que la
    page la reclasse rapidement en historique.
    """
    now = datetime(2026, 5, 10, 9, 0)
    out = mod._resolve_date(20, 5, "10:00", now, prefer_past=True)
    assert out == datetime(2026, 5, 20, 10, 0)


def test_resolve_date_past_block_far_future_flips_to_year_minus_one():
    """Bloc historique : un 15 novembre vu le 10 mai → 2025-11-15.

    Sans prefer_past, la candidate (2026-11-15) serait gardée telle quelle.
    Avec prefer_past, > 30 j dans le futur ⇒ on bascule sur l'année
    précédente (typique d'un bloc « Réunions passées » qui montre
    encore des auditions de novembre dernier).
    """
    now = datetime(2026, 5, 10, 9, 0)
    out = mod._resolve_date(15, 11, "10:00", now, prefer_past=True)
    assert out == datetime(2025, 11, 15, 10, 0)


def test_resolve_date_past_block_clear_past_kept():
    """Bloc historique : 24 avril vu le 10 mai → 2026-04-24 (déjà passé)."""
    now = datetime(2026, 5, 10, 9, 0)
    out = mod._resolve_date(24, 4, "09:00", now, prefer_past=True)
    assert out == datetime(2026, 4, 24, 9, 0)


def test_resolve_date_future_block_unchanged_by_default():
    """Régression : sans prefer_past, comportement identique à R35-E."""
    now = datetime(2026, 4, 28, 10, 0)
    out = mod._resolve_date(24, 4, "09:00", now)
    assert out == datetime(2026, 4, 24, 9, 0)


# ---------------------------------------------------------------------------
# _parse_page : bloc historique seul
# ---------------------------------------------------------------------------

def test_parse_page_reads_dernieres_reunions_block():
    """Page sans 'Prochaines' mais avec 'Dernières réunions' → events parsés."""
    html = _page(_wrap_block(
        "Dernières réunions",
        _li(
            day=6, month="mai",
            title="Audition de M. Nicolas de Tavernost",
            salle="Salle Médicis",
            heure="9:00",
        ),
    ))
    now = datetime(2026, 5, 10, 9, 0)
    events = mod._parse_page(html, now=now)
    assert len(events) == 1
    assert events[0]["event_dt"] == datetime(2026, 5, 6, 9, 0)
    assert "Tavernost" in events[0]["title"]


def test_parse_page_reads_reunions_precedentes_variant():
    """Variante de libellé : 'Réunions précédentes'."""
    html = _page(_wrap_block(
        "Réunions précédentes",
        _li(day=2, month="mai", title="Audition X", salle="Salle Y", heure="14:00"),
    ))
    events = mod._parse_page(html, now=datetime(2026, 5, 10, 9, 0))
    assert len(events) == 1
    assert events[0]["event_dt"] == datetime(2026, 5, 2, 14, 0)


def test_parse_page_reads_reunions_passees_variant():
    """Variante de libellé : 'Réunions passées'."""
    html = _page(_wrap_block(
        "Réunions passées",
        _li(day=2, month="mai", title="Audition Z", salle="S", heure="14:00"),
    ))
    events = mod._parse_page(html, now=datetime(2026, 5, 10, 9, 0))
    assert len(events) == 1


# ---------------------------------------------------------------------------
# _parse_page : combinaison futur + passé
# ---------------------------------------------------------------------------

def test_parse_page_combines_future_and_past_blocks():
    """Page avec les deux blocs → tous les events ingérés, dans l'ordre.

    Ordre attendu : d'abord futur (bloc 1), puis passé (bloc 2).
    """
    html = _page(
        _wrap_block(
            "Prochaines réunions",
            _li(day=14, month="mai", title="Audition future A", salle="S1", heure="10:00"),
        ),
        _wrap_block(
            "Dernières réunions",
            _li(day=6, month="mai", title="Audition Tavernost", salle="S2", heure="9:00"),
            _li(day=29, month="avril", title="Audition ancienne", salle="S3", heure="11:00"),
        ),
    )
    now = datetime(2026, 5, 10, 9, 0)
    events = mod._parse_page(html, now=now)
    titles = [e["title"] for e in events]
    assert "Audition future A" in titles
    assert "Audition Tavernost" in titles
    assert "Audition ancienne" in titles
    assert len(events) == 3


def test_parse_page_dedups_event_present_in_both_blocks():
    """Si la page liste le même event dans les deux blocs → un seul item."""
    same_li = _li(
        day=10, month="mai", title="Audition double", salle="S", heure="9:00",
    )
    html = _page(
        _wrap_block("Prochaines réunions", same_li),
        _wrap_block("Dernières réunions", same_li),
    )
    now = datetime(2026, 5, 10, 9, 0)
    events = mod._parse_page(html, now=now)
    assert len(events) == 1


def test_parse_page_past_block_far_future_date_flipped_to_year_minus_one():
    """Bloc historique avec une date lointaine du futur → year-1.

    Cas concret : début janvier 2026, le bloc « Réunions passées »
    montre encore des auditions de novembre 2025. Sans le flip,
    elles seraient datées 2026-11-XX (futur lointain artefact).
    """
    html = _page(_wrap_block(
        "Réunions passées",
        _li(day=15, month="novembre", title="Audition automne", salle="S", heure="9:00"),
    ))
    now = datetime(2026, 1, 10, 9, 0)
    events = mod._parse_page(html, now=now)
    assert len(events) == 1
    assert events[0]["event_dt"] == datetime(2025, 11, 15, 9, 0)


# ---------------------------------------------------------------------------
# fetch_source : non-régression "Aucun événement" + bloc passé
# ---------------------------------------------------------------------------

def test_fetch_source_aucun_evenement_in_future_but_past_populated(monkeypatch):
    """Page avec 'Aucun événement' côté Prochaines mais bloc passé peuplé.

    Régression observée pendant l'implémentation : l'early-return sur la
    chaîne « Aucun événement » jetait toute la page avant même de
    parser le bloc historique. Ce test verrouille le nouveau flow.
    """
    html = (
        '<html><body>'
        '<h3>Prochaines réunions</h3>'
        "<p>Aucun événement n'est actuellement inscrit à l'agenda.</p>"
        + _wrap_block(
            "Dernières réunions",
            _li(
                day=6, month="mai",
                title="Audition Tavernost",
                salle="Salle Médicis",
                heure="9:00",
            ),
        )
        + '</body></html>'
    )
    monkeypatch.setattr(mod, "fetch_text", lambda url: html)
    import datetime as _dt
    real_datetime = _dt.datetime
    class _FrozenNow(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 5, 10, 10, 0)
    monkeypatch.setattr(mod, "datetime", _FrozenNow)
    src = {
        "id": "senat_agenda_culture",
        "url": "https://example.test/",
        "category": "agenda",
        "commission_label": "Commission culture",
        "commission_organe": "PO211490",
    }
    items = mod.fetch_source(src)
    assert len(items) == 1
    assert "Tavernost" in items[0].title
    assert items[0].published_at == datetime(2026, 5, 6, 9, 0)
    assert items[0].raw["organe"] == "PO211490"
