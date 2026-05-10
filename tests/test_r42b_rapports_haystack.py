"""Tests R42-B (2026-05-10) — Rapports parlementaires : fetch corps PDF/HTML
+ haystack_body 50k chars + fenêtre temps 24 mois.

Couvre :
- `WINDOW_DAYS_BY_SOURCE_ID["an_rapports"] == 730` et idem senat_rapports.
- `assemblee_rapports._fetch_pdf_haystack` : graceful degradation
  (URL vide, fetch KO, pypdf KO).
- `senat._build_rap_mono_url` : conversion notice → mono pour les slugs
  modernes (r24-006), no-op pour les anciens / atypiques.
- `senat._fetch_senat_rap_haystack` : graceful degradation symétrique.
- Intégration `assemblee_rapports.fetch_source` : `raw.haystack_body`
  alimenté quand fetch PDF marche, vide sinon — pas de crash.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Racine repo au sys.path pour `import src.*`
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


# ----------------------------------------------------------------- WINDOW
def test_window_an_rapports_is_730_days():
    """R42-B : fenêtre an_rapports doit être 730j (2 ans), abaissée
    depuis 1095j (3 ans) en compromis avec la profondeur PDF."""
    from src.site_export import WINDOW_DAYS_BY_SOURCE_ID
    assert WINDOW_DAYS_BY_SOURCE_ID.get("an_rapports") == 730


def test_window_senat_rapports_is_730_days():
    """R42-B : fenêtre senat_rapports doit être 730j (idem AN)."""
    from src.site_export import WINDOW_DAYS_BY_SOURCE_ID
    assert WINDOW_DAYS_BY_SOURCE_ID.get("senat_rapports") == 730


# ------------------------------------------------ AN _fetch_pdf_haystack
def test_an_fetch_pdf_haystack_empty_url_returns_empty():
    """URL PDF vide → "" (pas de fetch inutile, pas de crash)."""
    from src.sources.assemblee_rapports import _fetch_pdf_haystack
    assert _fetch_pdf_haystack("") == ""
    assert _fetch_pdf_haystack(None) == ""  # type: ignore[arg-type]


def test_an_fetch_pdf_haystack_fetch_ko_returns_empty(monkeypatch):
    """Si fetch_bytes lève → "" (graceful degradation)."""
    from src.sources import assemblee_rapports

    def fake_fetch(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(assemblee_rapports, "fetch_bytes", fake_fetch)
    assert assemblee_rapports._fetch_pdf_haystack("https://example/r.pdf") == ""


def test_an_fetch_pdf_haystack_extract_ko_returns_empty(monkeypatch):
    """Si extraction PDF lève → "" (graceful degradation, pypdf KO)."""
    from src.sources import assemblee_rapports

    def fake_fetch(url):
        return b"%PDF-1.4 corrupted"

    def fake_extract(pdf_bytes, max_chars=50000):
        raise RuntimeError("pypdf invalid PDF")

    monkeypatch.setattr(assemblee_rapports, "fetch_bytes", fake_fetch)
    monkeypatch.setattr(assemblee_rapports, "_extract_pdf_text", fake_extract)
    assert assemblee_rapports._fetch_pdf_haystack("https://example/r.pdf") == ""


def test_an_fetch_pdf_haystack_truncates_to_max_chars(monkeypatch):
    """Si extract retourne > max_chars, le helper laisse pypdf
    tronquer (max_chars passé)."""
    from src.sources import assemblee_rapports

    def fake_fetch(url):
        return b"%PDF-1.4 valid"

    captured = {}

    def fake_extract(pdf_bytes, max_chars=50000):
        captured["max_chars"] = max_chars
        return "x" * max_chars  # pypdf retourne pile max_chars

    monkeypatch.setattr(assemblee_rapports, "fetch_bytes", fake_fetch)
    monkeypatch.setattr(assemblee_rapports, "_extract_pdf_text", fake_extract)
    out = assemblee_rapports._fetch_pdf_haystack("https://e/r.pdf", max_chars=50000)
    assert len(out) == 50000
    assert captured["max_chars"] == 50000


# ------------------------------------------ Sénat _build_rap_mono_url
def test_senat_build_mono_url_modern_slug():
    """`/notice-rapport/2024/r24-006-notice.html` → mono URL canonique."""
    from src.sources.senat import _build_rap_mono_url
    out = _build_rap_mono_url(
        "https://www.senat.fr/notice-rapport/2024/r24-006-notice.html"
    )
    assert out == "https://www.senat.fr/rap/r24-006/r24-006_mono.html"


def test_senat_build_mono_url_modern_slug_http():
    """Tolère le scheme http (le CSV expose http://www.senat.fr…)."""
    from src.sources.senat import _build_rap_mono_url
    out = _build_rap_mono_url(
        "http://www.senat.fr/notice-rapport/2024/r24-006-notice.html"
    )
    assert out == "https://www.senat.fr/rap/r24-006/r24-006_mono.html"


def test_senat_build_mono_url_session_2025():
    """Slug `r25-150` (rapport 2025) doit aussi être pris en charge."""
    from src.sources.senat import _build_rap_mono_url
    out = _build_rap_mono_url(
        "https://www.senat.fr/notice-rapport/2025/r25-150-notice.html"
    )
    assert out == "https://www.senat.fr/rap/r25-150/r25-150_mono.html"


def test_senat_build_mono_url_old_format_returns_empty():
    """URL atypique (rapport pré-2000 ou format obsolète) → no-op."""
    from src.sources.senat import _build_rap_mono_url
    # Pas de notice-rapport dans l'URL
    assert _build_rap_mono_url("https://www.senat.fr/rap/r99-100.html") == ""
    # Format avec underscore au lieu de tiret (atypique)
    assert _build_rap_mono_url("https://www.senat.fr/notice/r24_006.html") == ""


def test_senat_build_mono_url_empty_returns_empty():
    """URL vide / None → "" (pas de crash)."""
    from src.sources.senat import _build_rap_mono_url
    assert _build_rap_mono_url("") == ""
    assert _build_rap_mono_url(None) == ""  # type: ignore[arg-type]


# ----------------------------------- Sénat _fetch_senat_rap_haystack
def test_senat_fetch_haystack_empty_notice_returns_empty():
    """URL notice vide → "" (skip immédiat, pas de fetch)."""
    from src.sources.senat import _fetch_senat_rap_haystack
    assert _fetch_senat_rap_haystack("") == ""


def test_senat_fetch_haystack_old_format_returns_empty():
    """Slug ancien (URL pas reconnue par regex) → no fetch, "" retour."""
    from src.sources.senat import _fetch_senat_rap_haystack
    out = _fetch_senat_rap_haystack("https://www.senat.fr/rap/old.html")
    assert out == ""


def test_senat_fetch_haystack_fetch_ko_returns_empty(monkeypatch):
    """Si fetch_text lève → "" (graceful degradation)."""
    from src.sources import senat as senat_mod

    def fake_fetch(url):
        raise RuntimeError("404 not found")

    monkeypatch.setattr(senat_mod, "fetch_text", fake_fetch)
    out = senat_mod._fetch_senat_rap_haystack(
        "https://www.senat.fr/notice-rapport/2024/r24-006-notice.html"
    )
    assert out == ""


def test_senat_fetch_haystack_extracts_main_content(monkeypatch):
    """Si HTML contient un <main>, on en extrait le texte (pas la nav)."""
    from src.sources import senat as senat_mod

    fake_html = """
    <html><body>
      <nav>Navigation Sénat | Accueil | Travaux</nav>
      <main>
        <h1>Rapport sur le sport professionnel</h1>
        <p>Le sport professionnel français est en mutation profonde.
           Pass'Sport, ANS, dopage, fédérations sportives…</p>
      </main>
      <footer>Footer Sénat</footer>
    </body></html>
    """

    def fake_fetch(url):
        return fake_html

    monkeypatch.setattr(senat_mod, "fetch_text", fake_fetch)
    out = senat_mod._fetch_senat_rap_haystack(
        "https://www.senat.fr/notice-rapport/2024/r24-006-notice.html",
        max_chars=10000,
    )
    assert "sport professionnel" in out.lower()
    assert "Pass'Sport" in out
    # La nav et le footer ne doivent pas être inclus.
    assert "Navigation Sénat" not in out
    assert "Footer Sénat" not in out


def test_senat_fetch_haystack_truncates(monkeypatch):
    """`max_chars` est respecté."""
    from src.sources import senat as senat_mod

    fake_html = "<html><body><main>" + ("a" * 100000) + "</main></body></html>"

    def fake_fetch(url):
        return fake_html

    monkeypatch.setattr(senat_mod, "fetch_text", fake_fetch)
    out = senat_mod._fetch_senat_rap_haystack(
        "https://www.senat.fr/notice-rapport/2024/r24-006-notice.html",
        max_chars=5000,
    )
    assert len(out) == 5000


# ----------------------------------- Intégration AN fetch_source
def test_an_fetch_source_pose_haystack_body_quand_pdf_present(monkeypatch):
    """fetch_source extrait le corps PDF dans `raw.haystack_body` quand
    `url_pdf` est présent et fetch OK. Sans crash si fetch KO."""
    from src.sources import assemblee_rapports

    fake_listing_html = """
    <html><body>
      <li data-id="OMC_RAPPANR5L17B9999">
        <h3>Rapport sur le sport professionnel</h3>
        <p>Résumé court du rapport.</p>
        <span class="heure">Mis en ligne lundi 5 mai 2026 à 10h00</span>
        <a href="/dyn/17/dossiers/sport_pro">Dossier législatif</a>
        <a href="/pdf/rapports/r9999.pdf">Document</a>
      </li>
    </body></html>
    """

    pdf_corpus = (
        "Sommaire — partie I : situation actuelle du sport professionnel… "
        "Pass'Sport, fédérations, ANS, dopage, intégrité, paris sportifs…"
    )

    def fake_fetch_listing(url):
        return fake_listing_html.encode("utf-8")

    def fake_fetch_pdf(url):
        # url == /pdf/rapports/r9999.pdf → on retourne un blob PDF factice
        if url.endswith(".pdf"):
            return b"%PDF-1.4 fake"
        return fake_listing_html.encode("utf-8")

    def fake_extract(pdf_bytes, max_chars=50000):
        return pdf_corpus[:max_chars]

    # On mocke fetch_bytes au niveau du module assemblee_rapports
    monkeypatch.setattr(assemblee_rapports, "fetch_bytes", fake_fetch_pdf)
    monkeypatch.setattr(assemblee_rapports, "_extract_pdf_text", fake_extract)

    src = {
        "id": "an_rapports",
        "category": "communiques",
        "url": "https://www2.assemblee-nationale.fr/documents/liste?type=rapports&legis=17",
    }
    items = assemblee_rapports.fetch_source(src)
    assert len(items) == 1
    it = items[0]
    assert it.title.startswith("Rapport sur le sport")
    # haystack_body alimenté avec le corpus PDF.
    assert it.raw.get("haystack_body", "").startswith("Sommaire")
    assert "Pass'Sport" in it.raw["haystack_body"]


def test_an_fetch_source_no_crash_si_pdf_ko(monkeypatch):
    """Si le fetch PDF échoue, l'item est créé quand même avec
    `raw.haystack_body == ""` (graceful degradation, comportement R28)."""
    from src.sources import assemblee_rapports

    fake_listing_html = """
    <html><body>
      <li data-id="OMC_RAPPANR5L17B7777">
        <h3>Rapport ordinaire</h3>
        <p>Résumé.</p>
        <span class="heure">Mis en ligne mardi 6 mai 2026 à 14h00</span>
        <a href="/dyn/17/dossiers/X">Dossier législatif</a>
        <a href="/pdf/rapports/r7777.pdf">Document</a>
      </li>
    </body></html>
    """

    def fake_fetch_listing_or_pdf(url):
        if url.endswith(".pdf"):
            raise RuntimeError("404 PDF disparu")
        return fake_listing_html.encode("utf-8")

    monkeypatch.setattr(assemblee_rapports, "fetch_bytes",
                        fake_fetch_listing_or_pdf)

    src = {
        "id": "an_rapports",
        "category": "communiques",
        "url": "https://www2.assemblee-nationale.fr/documents/liste?type=rapports&legis=17",
    }
    items = assemblee_rapports.fetch_source(src)
    assert len(items) == 1
    it = items[0]
    # haystack_body est vide (graceful) — l'item reste créé.
    assert it.raw.get("haystack_body", "") == ""
    assert it.title.startswith("Rapport ordinaire")


if __name__ == "__main__":
    test_window_an_rapports_is_730_days()
    test_window_senat_rapports_is_730_days()
    test_an_fetch_pdf_haystack_empty_url_returns_empty()
    test_senat_build_mono_url_modern_slug()
    test_senat_build_mono_url_old_format_returns_empty()
    print("Tous les tests R42-B passent.")
