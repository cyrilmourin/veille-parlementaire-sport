"""R43-C (2026-05-17) — Fix CNOSF (tri lastmod desc) + CPSF (dates relatives).

Cyril : « cet article dans l'actualité du CNOSF n'est pas passé »
(Journée Europe 2026, 7 mai), idem CPSF (discriminations sport, 17 mai).

Diagnostic :
- CNOSF : sitemap dans ordre Drupal non chronologique (article récent
  en position 1149/1150). fetch_meta limité aux 60 premiers → l'article
  reste avec summary vide → matcher ne voit que le slug → drop.
  Fix : tri par lastmod DESC avant fetch_meta.
- CPSF : page affiche les dates en relatif (« Il y a 7 heures »).
  `_extract_date` n'avait pas de branche pour ce format → published_at
  vide → droppé par STRICT_DATED_CATEGORIES.
  Fix : 5e cascade « dates relatives » (heures/jours/semaines/mois/hier).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from src.sources.html_generic import _extract_date


def _make_anchor(html: str, url: str):
    soup = BeautifulSoup(html, "html.parser")
    a = soup.find("a")
    return a


def test_extract_date_il_y_a_heures():
    """« Il y a 7 heures » → now - 7h."""
    a = _make_anchor(
        '<div><time class="date">Il y a 7 heures</time>'
        '<a href="/x">Titre</a></div>',
        "https://example/x",
    )
    dt = _extract_date(a, "https://example/x")
    assert dt is not None
    delta = datetime.now() - dt
    # Tolère ±1 minute de drift
    assert timedelta(hours=6, minutes=59) <= delta <= timedelta(hours=7, minutes=1)


def test_extract_date_il_y_a_jours():
    a = _make_anchor(
        '<article><time>Il y a 3 jours</time>'
        '<a href="/y">Titre</a></article>',
        "https://example/y",
    )
    dt = _extract_date(a, "https://example/y")
    assert dt is not None
    delta = datetime.now() - dt
    assert timedelta(days=2, hours=23) <= delta <= timedelta(days=3, hours=1)


def test_extract_date_il_y_a_une_heure():
    """Variante « une » (sans chiffre)."""
    a = _make_anchor(
        '<div><time>Il y a une heure</time>'
        '<a href="/z">Titre</a></div>',
        "https://example/z",
    )
    dt = _extract_date(a, "https://example/z")
    assert dt is not None
    delta = datetime.now() - dt
    assert delta < timedelta(hours=1, minutes=2)


def test_extract_date_hier():
    a = _make_anchor(
        '<div><span>Hier</span><a href="/h">Titre</a></div>',
        "https://example/h",
    )
    dt = _extract_date(a, "https://example/h")
    assert dt is not None
    # Hier minuit
    yesterday = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    assert abs((dt - yesterday).total_seconds()) < 60


def test_extract_date_aujourdhui():
    a = _make_anchor(
        '<div><span>Aujourd\'hui</span><a href="/a">Titre</a></div>',
        "https://example/a",
    )
    dt = _extract_date(a, "https://example/a")
    assert dt is not None
    # Aujourd'hui minuit
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    assert abs((dt - today).total_seconds()) < 60


def test_extract_date_il_y_a_mois():
    a = _make_anchor(
        '<article><time>Il y a 2 mois</time>'
        '<a href="/m">Titre</a></article>',
        "https://example/m",
    )
    dt = _extract_date(a, "https://example/m")
    assert dt is not None
    delta = datetime.now() - dt
    # Approximation 30 jours / mois → 60 jours ±1
    assert timedelta(days=59) <= delta <= timedelta(days=61)


def test_extract_date_format_iso_priorite_sur_relative():
    """Si <time datetime=...> ISO est présent, il a la priorité (cascade 1)."""
    a = _make_anchor(
        '<div><time datetime="2026-03-15">Il y a 60 jours</time>'
        '<a href="/p">Titre</a></div>',
        "https://example/p",
    )
    dt = _extract_date(a, "https://example/p")
    assert dt == datetime(2026, 3, 15)


def test_cpsf_real_case_il_y_a_7_heures():
    """Cas concret CPSF observé le 17/05/2026."""
    a = _make_anchor(
        '<div class="post"><time class="date">Il y a 7 heures</time>'
        '<h3 class="title"><a href="https://france-paralympique.fr/'
        'actualite/discriminations-sport-cpsf/">'
        'Contre les discriminations dans le sport, le CPSF agit</a></h3></div>',
        "https://france-paralympique.fr/actualite/discriminations-sport-cpsf/",
    )
    dt = _extract_date(
        a,
        "https://france-paralympique.fr/actualite/discriminations-sport-cpsf/",
    )
    assert dt is not None, "L'article CPSF doit avoir une date extraite"
    # ~ il y a 7 heures
    delta = datetime.now() - dt
    assert timedelta(hours=6) <= delta <= timedelta(hours=8)
