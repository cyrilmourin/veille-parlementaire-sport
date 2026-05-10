"""R41-AY (2026-05-10) — Tests scraper vidéothèque commission Sénat.

Fixtures basées sur le rendu réel observé sur
https://videos.senat.fr/commission.AFCL.p1 (audit 2026-05-10).
Tous offline : `fetch_text` monkeypatché.
"""
from __future__ import annotations

from datetime import datetime

from src.sources import senat_videos_commission as mod


# ---------------------------------------------------------------------------
# Fixtures HTML
# ---------------------------------------------------------------------------

def _card(*, video_id: str, slug: str, title: str, date_text: str,
          duration: str = "1 h 06") -> str:
    """Reproduit fidèlement une `<div class="swiper-slide">` de p1."""
    url = f"https://videos.senat.fr/video.{video_id}.{slug}"
    return f'''
    <div class="swiper-slide ">
        <div class="card card-default card-reduced">
            <figure class="card-figure">
                <img src="img/x.jpg" alt="" class="card-img">
            </figure>
            <div class="card-header">
                <div class="card-duration">{duration}</div>
                <div class="card-icon ms-auto"><i class="bi-play-fill"></i></div>
            </div>
            <div class="card-body">
                <h3 class="card-title">
                    <a href="{url}" class="stretched-link" title="{title}">
                        {title}
                    </a>
                </h3>
                <p class="card-subtitle"></p>
                <time class="card-time">{date_text}</time>
            </div>
        </div>
    </div>
    '''


def _wrap(*cards: str, footer_card: bool = True) -> str:
    """Page complète avec footer pour vérifier qu'on n'attrape pas
    les `card-slim` du bloc « Le Sénat, c'est aussi »."""
    footer = ''
    if footer_card:
        footer = '''
        <div class="page-footer">
            <div class="swiper-slide">
                <div class="card card-default card-slim h-100">
                    <div class="card-body">
                        <h3 class="card-title">
                            <a href="https://www.senat.fr/x.html" class="stretched-link" title="Rôle et fonctionnement">
                                Rôle et fonctionnement
                            </a>
                        </h3>
                    </div>
                </div>
            </div>
        </div>
        '''
    return f'<html><body><section><div class="content">{"".join(cards)}</div></section>{footer}</body></html>'


# ---------------------------------------------------------------------------
# _parse_french_date
# ---------------------------------------------------------------------------

def test_parse_french_date_full():
    assert mod._parse_french_date("Mercredi 6 mai 2026") == datetime(2026, 5, 6, 0, 0)


def test_parse_french_date_handles_august_with_or_without_accent():
    assert mod._parse_french_date("Mardi 5 août 2025") == datetime(2025, 8, 5, 0, 0)
    assert mod._parse_french_date("Mardi 5 aout 2025") == datetime(2025, 8, 5, 0, 0)


def test_parse_french_date_handles_february_without_accent():
    assert mod._parse_french_date("Lundi 12 fevrier 2024") == datetime(2024, 2, 12, 0, 0)


def test_parse_french_date_no_weekday_prefix_works():
    assert mod._parse_french_date("6 mai 2026") == datetime(2026, 5, 6, 0, 0)


def test_parse_french_date_garbage_returns_none():
    assert mod._parse_french_date("") is None
    assert mod._parse_french_date("pas de date") is None
    assert mod._parse_french_date("32 mai 2026") is None  # jour invalide
    assert mod._parse_french_date("6 brumaire 2026") is None  # mois inconnu


# ---------------------------------------------------------------------------
# _video_uid
# ---------------------------------------------------------------------------

def test_video_uid_uses_senat_id_when_extractable():
    """Deux URLs avec le même ID Sénat → même UID, peu importe le slug."""
    sid = "senat_videos_culture"
    url_a = "https://videos.senat.fr/video.5814747_69f98208892ca.crise-tv-football"
    url_b = "https://videos.senat.fr/video.5814747_69f98208892ca.different-slug"
    fb = "x"
    assert mod._video_uid(sid, url_a, fb) == mod._video_uid(sid, url_b, fb)


def test_video_uid_falls_back_when_url_unparseable():
    """URL hors format /video.ID_HASH./ → on hash la fallback_key."""
    sid = "senat_videos_culture"
    url = "https://videos.senat.fr/something-else.html"
    uid_a = mod._video_uid(sid, url, "fallback-1")
    uid_b = mod._video_uid(sid, url, "fallback-2")
    assert uid_a != uid_b  # fallback différente → uid différent


def test_video_uid_format_16_hex():
    uid = mod._video_uid("x", "https://videos.senat.fr/video.123_abc.slug", "k")
    assert len(uid) == 16
    assert all(c in "0123456789abcdef" for c in uid)


# ---------------------------------------------------------------------------
# _parse_page
# ---------------------------------------------------------------------------

def test_parse_page_extracts_tavernost_card():
    """Le cas qui motive R41-AY : extraire l'audition Tavernost."""
    html = _wrap(_card(
        video_id="5814747_69f98208892ca",
        slug="crise-des-droits-tv-du-football--nicolas-de-tavernost",
        title="Crise des droits TV du football : Nicolas de Tavernost",
        date_text="Mercredi 6 mai 2026",
        duration="1 h 06",
    ))
    events = mod._parse_page(html)
    assert len(events) == 1
    ev = events[0]
    assert ev["title"] == "Crise des droits TV du football : Nicolas de Tavernost"
    assert ev["event_dt"] == datetime(2026, 5, 6, 0, 0)
    assert ev["url"].endswith("nicolas-de-tavernost")
    assert ev["duration"] == "1 h 06"


def test_parse_page_extracts_multiple_cards():
    html = _wrap(
        _card(video_id="1_a", slug="a", title="Audition Alpha",
              date_text="Mardi 14 avril 2026", duration="1 h"),
        _card(video_id="2_b", slug="b", title="Audition Beta",
              date_text="Mercredi 8 avril 2026", duration="2 h"),
        _card(video_id="3_c", slug="c", title="Audition Gamma",
              date_text="Mardi 7 avril 2026", duration="1 h 19"),
    )
    events = mod._parse_page(html)
    assert len(events) == 3
    assert events[0]["title"] == "Audition Alpha"
    assert events[2]["event_dt"] == datetime(2026, 4, 7, 0, 0)


def test_parse_page_ignores_card_slim_footer():
    """Vérifie qu'on n'attrape pas les cards du footer ("Rôle et fonctionnement")."""
    html = _wrap(
        _card(video_id="1_a", slug="a", title="Audition X",
              date_text="Mercredi 1 mai 2026"),
        footer_card=True,  # ajoute la card-slim du footer
    )
    events = mod._parse_page(html)
    assert len(events) == 1
    assert events[0]["title"] == "Audition X"


def test_parse_page_skips_card_without_date():
    """Carte sans <time class="card-time"> exploitable → ignorée."""
    broken = '''
    <div class="swiper-slide">
        <div class="card card-default card-reduced">
            <div class="card-body">
                <h3 class="card-title">
                    <a href="https://videos.senat.fr/video.99_xx.foo"
                       class="stretched-link" title="Sans date">
                        Sans date
                    </a>
                </h3>
            </div>
        </div>
    </div>
    '''
    html = _wrap(broken, _card(
        video_id="1_a", slug="a", title="Audition OK",
        date_text="Mardi 1 avril 2026",
    ))
    events = mod._parse_page(html)
    assert len(events) == 1
    assert events[0]["title"] == "Audition OK"


def test_parse_page_skips_card_without_video_link():
    """Lien hors `/video.X./` → ignoré (filtre URL)."""
    broken = '''
    <div class="swiper-slide">
        <div class="card card-default card-reduced">
            <div class="card-body">
                <h3 class="card-title">
                    <a href="https://www.senat.fr/page.html"
                       class="stretched-link" title="Pas une vidéo">
                        Pas une vidéo
                    </a>
                </h3>
                <time class="card-time">Mercredi 6 mai 2026</time>
            </div>
        </div>
    </div>
    '''
    events = mod._parse_page(_wrap(broken))
    assert events == []


def test_parse_page_skips_title_too_short():
    html = _wrap(_card(
        video_id="1_a", slug="a", title="x",
        date_text="Mercredi 1 mai 2026",
    ))
    assert mod._parse_page(html) == []


def test_parse_page_empty_html_returns_empty():
    assert mod._parse_page("<html><body></body></html>") == []


# ---------------------------------------------------------------------------
# fetch_source (intégration offline)
# ---------------------------------------------------------------------------

def test_fetch_source_emits_items_with_organe(monkeypatch):
    """L'item produit a `raw.organe` pour le bypass R27 (cohérence R35-E/R41-AX)."""
    html = _wrap(_card(
        video_id="5814747_69f98208892ca",
        slug="crise-des-droits-tv-du-football--nicolas-de-tavernost",
        title="Crise des droits TV du football : Nicolas de Tavernost",
        date_text="Mercredi 6 mai 2026",
        duration="1 h 06",
    ))
    monkeypatch.setattr(mod, "fetch_text", lambda url: html)
    src = {
        "id": "senat_videos_culture",
        "url": "https://videos.senat.fr/commission.AFCL.p1",
        "category": "agenda",
        "commission_label": "Commission culture/éducation/communication/sport",
        "commission_organe": "PO211490",
    }
    items = mod.fetch_source(src)
    assert len(items) == 1
    it = items[0]
    assert it.source_id == "senat_videos_culture"
    assert it.category == "agenda"
    assert it.chamber == "Senat"
    assert it.title.startswith("Commission culture/éducation/communication/sport — ")
    assert "Tavernost" in it.title
    assert it.published_at == datetime(2026, 5, 6, 0, 0)
    # URL = lien vidéo direct, pas la page index
    assert it.url.endswith("nicolas-de-tavernost")
    assert it.raw["organe"] == "PO211490"
    assert it.raw["path"] == "senat:videos_commission_html"
    assert it.raw["duration"] == "1 h 06"
    assert "Vidéo Sénat" in it.summary
    assert "1 h 06" in it.summary


def test_fetch_source_uid_stable_across_runs(monkeypatch):
    """Re-fetch identique → même UID (idempotence cron)."""
    html = _wrap(_card(
        video_id="5814747_69f98208892ca", slug="x",
        title="Audition Stable", date_text="Mardi 1 avril 2026",
    ))
    monkeypatch.setattr(mod, "fetch_text", lambda url: html)
    src = {"id": "senat_videos_culture", "url": "https://example.test/"}
    a = mod.fetch_source(src)
    b = mod.fetch_source(src)
    assert a[0].uid == b[0].uid


def test_fetch_source_no_label_no_prefix(monkeypatch):
    """Sans commission_label, pas de préfixe : titre brut."""
    html = _wrap(_card(
        video_id="1_a", slug="a", title="Audition libre",
        date_text="Mercredi 1 mai 2026",
    ))
    monkeypatch.setattr(mod, "fetch_text", lambda url: html)
    items = mod.fetch_source({"id": "x", "url": "https://example.test/"})
    assert len(items) == 1
    assert items[0].title == "Audition libre"


def test_fetch_source_fetch_error_returns_empty(monkeypatch):
    """fetch_text raise → [] silencieux (pas de crash pipeline)."""
    def _raiser(url):
        raise RuntimeError("network down")
    monkeypatch.setattr(mod, "fetch_text", _raiser)
    items = mod.fetch_source({"id": "x", "url": "https://example.test/"})
    assert items == []


def test_fetch_source_empty_page_returns_empty(monkeypatch):
    """Page sans cards → [] (creux d'agenda ou format changé)."""
    monkeypatch.setattr(mod, "fetch_text", lambda url: "<html><body></body></html>")
    items = mod.fetch_source({"id": "x", "url": "https://example.test/"})
    assert items == []
