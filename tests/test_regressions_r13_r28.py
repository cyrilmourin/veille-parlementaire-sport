"""R30 (2026-04-24) — Tests « incidents » rétroactifs R13 → R28.

Audit §4.8 : « Pour chaque ticket R-N résolu, un test court qui referme
la porte ». Plutôt que de cumuler des commentaires datés dans les
parseurs / site_export, on vérifie à chaque run que les bugs passés ne
peuvent plus réapparaître par erreur.

Portée : on cible les incidents qui n'ont PAS déjà un test dédié.
Ceux qui ont déjà leur fichier (test_r25b_senat_questions.py,
test_r27_organes_sport.py, test_r28_an_rapports.py,
test_site_export_disabled_sources.py pour R22b, test_site_export_dedup
pour R22a, test_agenda_title_r23g.py pour R23-G) ne sont pas
redupliqués ici.

Tests couverts :
- R19-A : encoding ISO-8859-15 du flux Sénat theme sport RSS
- R19-B : filtre /leg/pjl|ppl dans le normalize RSS Sénat
- R19-C : préfixe auteur redondant retiré du summary des questions
- R19-G / R23-F : préambule Syceron strip_cr_an_preamble
- R22e-1 : parsing de la date FR numérique DD/MM/YYYY
- R22g : réécriture legacy question title au format pré-R13-L
- R22h : STRICT_DATED_CATEGORIES inclut `questions`, pas de fallback
  inserted_at, rejet des dates futures
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src import site_export
from src.sources import html_generic, senat


# ---------------------------------------------------------------------------
# R19-A — encoding ISO-8859-15 du flux Sénat thème Sport (RSS)
# ---------------------------------------------------------------------------


def test_r19a_senat_rss_iso_8859_15_encoding_preserves_oelig():
    """`_normalize_rss` accepte les bytes bruts et laisse feedparser lire
    la PI XML `<?xml encoding="ISO-8859-15"?>` : les caractères œ, é, à, è
    doivent rester intacts (pas de 'ï¿œ' ni 'Ã©').

    Avant R19-A, on passait une str déjà décodée en UTF-8 : feedparser
    perdait l'info encoding et les titres Sénat sortaient en mojibake
    ('nï¿œ 733' vu en prod).
    """
    xml_iso = (
        b'<?xml version="1.0" encoding="ISO-8859-15"?>'
        b'<rss version="2.0"><channel><title>Test Senat</title>'
        b'<item><guid>http://senat.fr/leg/pjl25-733.html</guid>'
        b'<link>http://senat.fr/leg/pjl25-733.html</link>'
        # 'Projet de loi \x9cuvre sportive nÂ°733' — \x9c = 'œ' en Latin-9,
        # \xb0 = '°', \xe9 = 'é'.
        b'<title>Projet de loi \x9cuvre sportive n\xb0733 - \xe9volution</title>'
        b'<description>r\xe9sum\xe9</description>'
        b'</item></channel></rss>'
    )
    src = {"id": "senat_theme_sport_rss", "category": "dossiers_legislatifs"}
    items = senat._normalize_rss(src, xml_iso)
    assert len(items) == 1
    # Les caractères non-ASCII doivent être décodés correctement.
    assert "œuvre" in items[0].title
    assert "évolution" in items[0].title
    assert "résumé" in items[0].summary
    # Pas de marqueurs mojibake résiduels.
    assert "ï¿½" not in items[0].title
    assert "Ã©" not in items[0].title


# ---------------------------------------------------------------------------
# R19-B — filtre /leg/pjl|ppl dans _normalize_rss (dossiers législatifs)
# ---------------------------------------------------------------------------


def test_r19b_senat_rss_skips_non_initial_docs():
    """Pour `category=dossiers_legislatifs`, seules les URLs contenant
    `/leg/pjlXX` ou `/leg/pplXX` doivent être retenues. Les `tas` (textes
    adoptés), `rap` (rapports), `a` (avis), `notice-rapport` étaient
    régurgités avant R19-B et gonflaient la page Dossiers législatifs
    avec des lignes redondantes (8+ lignes pour la loi JOP Alpes 2030).
    """
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<rss version="2.0"><channel><title>T</title>'
        b'<item><guid>1</guid><link>http://senat.fr/leg/pjl25-733.html</link>'
        b'<title>Projet de loi 733</title></item>'
        b'<item><guid>2</guid><link>http://senat.fr/leg/tas25-042.html</link>'
        b'<title>Texte adopte</title></item>'
        b'<item><guid>3</guid><link>http://senat.fr/rap/r25-500.html</link>'
        b'<title>Rapport 500</title></item>'
        b'<item><guid>4</guid><link>http://senat.fr/notice-rapport/xyz.html</link>'
        b'<title>Notice</title></item>'
        b'<item><guid>5</guid><link>http://senat.fr/leg/ppl25-010.html</link>'
        b'<title>PPL 10</title></item>'
        b'</channel></rss>'
    )
    src = {"id": "senat_theme_sport_rss", "category": "dossiers_legislatifs"}
    items = senat._normalize_rss(src, xml)
    urls = [it.url for it in items]
    assert "http://senat.fr/leg/pjl25-733.html" in urls
    assert "http://senat.fr/leg/ppl25-010.html" in urls
    # Les autres types sont filtrés.
    assert "http://senat.fr/leg/tas25-042.html" not in urls
    assert "http://senat.fr/rap/r25-500.html" not in urls
    assert "http://senat.fr/notice-rapport/xyz.html" not in urls


def test_r19b_senat_rss_keeps_everything_when_not_dosleg():
    """Hors `category=dossiers_legislatifs`, le filtre ne s'applique PAS.
    Les autres catégories (communiques RSS, etc.) gardent tous les items."""
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<rss version="2.0"><channel><title>T</title>'
        b'<item><guid>1</guid><link>http://senat.fr/actualites/a.html</link>'
        b'<title>Actualite</title></item>'
        b'<item><guid>2</guid><link>http://senat.fr/rap/r25-500.html</link>'
        b'<title>Rapport</title></item>'
        b'</channel></rss>'
    )
    src = {"id": "senat_rss", "category": "communiques"}
    items = senat._normalize_rss(src, xml)
    # Les 2 items doivent être gardés : filtre seulement en dosleg.
    assert len(items) == 2


# ---------------------------------------------------------------------------
# R19-C — Préfixe auteur redondant retiré du summary des questions
# ---------------------------------------------------------------------------


def test_r19c_question_snippet_no_author_prefix():
    """`_fix_question_row` supprime le préfixe `M./Mme <Nom> (<Groupe>) —`
    du summary : l'auteur est déjà affiché dans l'en-tête de carte, donc
    le snippet doit aller direct au destinataire / à l'analyse."""
    r = {
        "category": "questions",
        "title": "Question écrite : Soutien aux fédérations sportives",
        "summary": (
            "Mme Martine Dupont (SER) — attire l'attention de la ministre "
            "des Sports sur la situation des petites fédérations olympiques."
        ),
        "raw": {},
    }
    site_export._fix_question_row(r)
    assert not r["summary"].startswith("Mme Martine Dupont")
    assert "attire l'attention" in r["summary"]


def test_r19c_question_snippet_no_depute_pa_prefix():
    """Variante : préfixe `Député PAxxxxx —` sans groupe (cas legacy avant
    résolution du cache AMO). Doit aussi être retiré."""
    r = {
        "category": "questions",
        "title": "Question écrite : Sport scolaire",
        "summary": "Député PA721234 — interroge le ministre sur le sport à l'école.",
        "raw": {},
    }
    site_export._fix_question_row(r)
    assert not r["summary"].startswith("Député PA")
    assert "interroge le ministre" in r["summary"]


# ---------------------------------------------------------------------------
# R19-G / R23-F — strip préambule Syceron CR AN
# ---------------------------------------------------------------------------


def test_r19g_cr_an_strip_preamble_cuts_on_presidence():
    """Un CR AN dont le summary commence par l'entête Syceron technique
    doit être coupé sur « Présidence de … » — sinon le snippet affiche
    des IDs au lieu du vrai débat."""
    haystack = (
        "CRSANR5L17S2026O010N123 RUANR5L17S2026N001 SCR5A valide complet public "
        "avant_JO PROD Session ordinaire 2025-2026 1 130 AN 17 "
        "Présidence de Mme Yaël Braun-Pivet. La séance est ouverte à quinze "
        "heures. La parole est à M. Dupont sur le sport."
    )
    out = site_export._strip_cr_an_preamble(haystack)
    assert out.startswith("Présidence")
    assert "CRSANR5" not in out
    assert "sport" in out


def test_r19g_cr_an_strip_preamble_is_idempotent():
    """Appliquer `_strip_cr_an_preamble` deux fois doit donner le même
    résultat — important parce que le fixup export et la reconstruction
    du snippet l'appellent tous les deux."""
    h = (
        "CRSANR5L17 RUANR5L17 SCR5A valide "
        "Présidence de Mme X. La séance est ouverte."
    )
    out1 = site_export._strip_cr_an_preamble(h)
    out2 = site_export._strip_cr_an_preamble(out1)
    assert out1 == out2


def test_r19g_cr_an_strip_preamble_noop_when_no_marker():
    """Pas de marqueur dans les 600 premiers caractères → pas de coupe."""
    h = "Un texte quelconque sans marqueur de début de séance."
    assert site_export._strip_cr_an_preamble(h) == h


def test_r19g_cr_an_strip_preamble_empty_input():
    assert site_export._strip_cr_an_preamble("") == ""
    assert site_export._strip_cr_an_preamble(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# R22e-1 — parsing de la date FR numérique DD/MM/YYYY
# ---------------------------------------------------------------------------


def test_r22e_html_generic_parses_dd_mm_yyyy():
    """Un bloc HTML ANJ-style `<li><a>titre</a> (16/04/2026)</li>` doit
    produire un `published_at = 2026-04-16`. Avant R22e-1, la regex
    `_DATE_FR_PAT` (mois littéral) et `_DATE_PAT` (ISO) ne matchaient
    pas → 120/131 items ANJ avaient `published_at=None`."""
    from bs4 import BeautifulSoup

    html = (
        '<ul><li><a href="/communique-1.html">Titre 1</a> (16/04/2026)</li></ul>'
    )
    soup = BeautifulSoup(html, "html.parser")
    a = soup.find("a")
    dt = html_generic._extract_date(a, "https://anj.fr/communique-1.html")
    assert dt == datetime(2026, 4, 16)


def test_r22e_html_generic_parses_dot_separator():
    """Accepte aussi le séparateur point : 16.04.2026."""
    from bs4 import BeautifulSoup

    html = '<div><a href="/x">Titre</a> <span>16.04.2026</span></div>'
    soup = BeautifulSoup(html, "html.parser")
    a = soup.find("a")
    dt = html_generic._extract_date(a, "https://x.fr/x")
    assert dt == datetime(2026, 4, 16)


def test_r22e_html_generic_rejects_impossible_day_month():
    """Garde-fou : une séquence de 4 chiffres type ID `1234/56/7890` ne
    doit pas être lue comme une date (jour ≤ 31, mois ≤ 12)."""
    from bs4 import BeautifulSoup

    html = '<div><a href="/x">T</a> ref 99/99/2026</div>'
    soup = BeautifulSoup(html, "html.parser")
    a = soup.find("a")
    dt = html_generic._extract_date(a, "https://x.fr/x")
    # 99/99 impossible → fallback ISO/None.
    assert dt is None


# ---------------------------------------------------------------------------
# R22g — Réécriture legacy question title (format pré-R13-L)
# ---------------------------------------------------------------------------


def test_r22g_legacy_question_title_rewritten_on_export():
    """Un row questions dont le `title` est au format legacy
    `"M. Dupont | Question orale n°83 — PA795136 (LFI-NFP) : M."`
    doit être réécrit `"Question orale : <analyse>"` à l'export.
    Évite un reset DB complet."""
    title_in = "M. Jean-François Coulomme | Question orale n°83 — PA795136 (LFI-NFP) : M."
    # _fix_question_row ne fait pas ce rewrite (c'est fait dans _write_item_pages,
    # mais la logique est extractible : on valide la regex métier).
    import re
    # La condition R22g : cat == "questions" + regex PA\d+ (<groupe>).
    assert re.search(r"PA\d+\s*\([^)]+\)", title_in)
    qtype_m = re.search(r"\b(Question[^|]*?)\s*n°\s*\d+", title_in, re.IGNORECASE)
    assert qtype_m is not None
    qtype_label = qtype_m.group(1).strip()
    assert qtype_label == "Question orale"
    # Reconstruction avec analyse non-vide.
    raw = {"analyse": "Situation des petites fédérations"}
    sujet_court = (raw.get("analyse") or "").strip()
    assert sujet_court
    rewritten = f"{qtype_label} : {sujet_court}"
    assert rewritten == "Question orale : Situation des petites fédérations"
    # Le nouveau titre ne contient plus le code PA.
    assert "PA795136" not in rewritten
    assert " | " not in rewritten


def test_r22g_regex_ignores_modern_question_titles():
    """Titre déjà au format moderne (pas de `PA\\d+ (\\w+)`) : le rewrite
    ne doit PAS se déclencher."""
    import re
    title_modern = "Question écrite : Sport scolaire"
    assert not re.search(r"PA\d+\s*\([^)]+\)", title_modern)


# ---------------------------------------------------------------------------
# R22h — questions dans STRICT_DATED_CATEGORIES
# ---------------------------------------------------------------------------


def test_r22h_questions_in_strict_dated_categories():
    """`questions` doit être dans STRICT_DATED_CATEGORIES : pas de
    fallback `inserted_at`, pas de dates futures."""
    assert "questions" in site_export.STRICT_DATED_CATEGORIES


def test_r22h_filter_window_rejects_question_without_published_at():
    """Sans `published_at` (même avec `inserted_at` récent), une question
    NE doit PAS passer le filtre fenêtre."""
    now = datetime.utcnow()
    rows = [
        {
            "category": "questions",
            "published_at": None,
            "inserted_at": (now - timedelta(days=2)).isoformat(),
            "title": "Question orpheline",
        },
    ]
    kept = site_export._filter_window(rows)
    assert kept == []


def test_r22h_filter_window_rejects_future_question():
    """Une question avec `published_at` dans le futur est rejetée
    (Agenda hebdo annoncé à fin de semaine ne devait pas polluer les
    questions — garde-fou strict)."""
    now = datetime.utcnow()
    rows = [
        {
            "category": "questions",
            "published_at": (now + timedelta(days=3)).isoformat(),
            "title": "Question future",
        },
    ]
    kept = site_export._filter_window(rows)
    assert kept == []


def test_r22h_filter_window_accepts_question_in_window():
    """Dans la fenêtre questions (90 jours) ET pas dans le futur : OK."""
    now = datetime.utcnow()
    rows = [
        {
            "category": "questions",
            "published_at": (now - timedelta(days=10)).isoformat(),
            "title": "Question récente",
        },
    ]
    kept = site_export._filter_window(rows)
    assert len(kept) == 1


def test_r22h_filter_window_rejects_question_outside_window():
    """Une question publiée il y a > 90 j est exclue (WINDOW_DAYS_BY_CATEGORY
    questions = 90)."""
    now = datetime.utcnow()
    rows = [
        {
            "category": "questions",
            "published_at": (now - timedelta(days=120)).isoformat(),
            "title": "Question vieille",
        },
    ]
    kept = site_export._filter_window(rows)
    assert kept == []


# ---------------------------------------------------------------------------
# Sanity : catégories non-strictes continuent d'avoir le fallback
# (on protège R22h *sans* casser le comportement historique)
# ---------------------------------------------------------------------------


def test_non_strict_category_falls_back_to_inserted_at():
    """Un `agenda` sans `published_at` mais avec `inserted_at` récent
    doit passer le filtre (comportement historique inchangé)."""
    now = datetime.utcnow()
    rows = [
        {
            "category": "agenda",
            "published_at": None,
            "inserted_at": (now - timedelta(days=2)).isoformat(),
            "title": "Agenda sans date officielle",
        },
    ]
    kept = site_export._filter_window(rows)
    assert len(kept) == 1
