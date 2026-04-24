"""R35-B (2026-04-24) — Tests du scraper CR commissions AN.

Cas Cyril : cion-cedu réunion 58 (2026-04-22), « Table ronde sur la
gouvernance des autres sports que le football ». Le titre agenda ne
matche pas ; le PDF oui.

Tous les tests sont offline — `_fetch_silent` et `_extract_pdf_text`
sont monkeypatchés pour injecter du HTML / du texte fictifs.
"""
from __future__ import annotations

from datetime import datetime

from src.sources import an_cr_commissions as mod


def _stub_fetch_silent(responses: dict[str, tuple[int, bytes]]):
    """Fabrique un _fetch_silent qui renvoie une table d'URLs → (status, body)."""
    def _fake(url: str, timeout: float = 20.0):
        return responses.get(url, (404, b""))
    return _fake


def _html_ok(title: str = "Compte rendu de réunion n° 58 - Commission des affaires culturelles et de l'éducation - Session 2025 – 2026 - 17e législature - Assemblée nationale") -> bytes:
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body>réunion du mercredi 22 avril 2026</body></html>"
    ).encode("utf-8")


def test_session_code_from_date_post_october():
    """Octobre 2025 → session 2025-2026 (code '2526')."""
    assert mod._session_code(datetime(2025, 10, 15)) == "2526"
    assert mod._session_code(datetime(2026, 4, 22)) == "2526"


def test_session_code_from_date_pre_october():
    """Septembre 2026 → encore session 2025-2026."""
    assert mod._session_code(datetime(2026, 9, 30)) == "2526"
    # Janvier 2027 → session 2026-2027
    assert mod._session_code(datetime(2027, 1, 5)) == "2627"


def test_parse_title_splits_an_title():
    """Extraction du titre AN type : garde les 2 premières sections."""
    html = (
        "<title>Compte rendu de réunion n° 58 - Commission des affaires "
        "culturelles et de l'éducation - Session 2025 – 2026 - 17e "
        "législature - Assemblée nationale</title>"
    )
    t = mod._parse_title(html, "Commission des affaires culturelles", 58)
    assert t.startswith("Compte rendu de réunion n° 58")
    assert "Commission des affaires culturelles" in t
    # Ne doit pas contenir les sections "Session" ni "législature"
    assert "Session" not in t
    assert "législature" not in t


def test_parse_title_fallback_when_no_title_tag():
    """Pas de <title> → fallback sur commission_label + n°."""
    t = mod._parse_title("<html>no title</html>", "Commission X", 42)
    assert "Commission X" in t
    assert "42" in t


def test_parse_date_fr():
    """Extraction date française '22 avril 2026'."""
    d = mod._parse_date("La réunion du mercredi 22 avril 2026 a été...")
    assert d == datetime(2026, 4, 22)


def test_parse_date_none_on_junk():
    assert mod._parse_date("rien de daté") is None
    assert mod._parse_date("") is None


def test_fetch_cr_returns_none_on_404(monkeypatch):
    """Si la page HTML renvoie 404, _fetch_cr retourne None (CR pas publié)."""
    monkeypatch.setattr(mod, "_fetch_silent", _stub_fetch_silent({}))
    # aussi : no pdf extractor appelé (html 404 → shortcut)
    result = mod._fetch_cr("cion-cedu", "2526", 99, "Commission X")
    assert result is None


def test_fetch_cr_builds_item_with_body(monkeypatch):
    """HTML 200 + PDF 200 → Item avec haystack_body rempli."""
    html_url = (
        "https://www.assemblee-nationale.fr/dyn/17/comptes-rendus/"
        "cion-cedu/l17cion-cedu2526058_compte-rendu"
    )
    pdf_url = html_url + ".pdf"
    fake_pdf = b"FAKE_PDF_BYTES"
    monkeypatch.setattr(
        mod, "_fetch_silent",
        _stub_fetch_silent({
            html_url: (200, _html_ok()),
            pdf_url: (200, fake_pdf),
        }),
    )
    # Court-circuit pypdf : simule l'extraction
    monkeypatch.setattr(
        mod, "_extract_pdf_text",
        lambda b, max_chars=10000: (
            "La commission auditionne sur la gouvernance des autres sports "
            "que le football. Table ronde — Philippe Bana, Fédération..."
        ),
    )
    it = mod._fetch_cr(
        "cion-cedu", "2526", 58,
        "Commission des affaires culturelles et de l'éducation",
    )
    assert it is not None
    assert it.source_id == "an_cr_commissions"
    assert it.category == "comptes_rendus"
    assert it.chamber == "AN"
    assert it.uid == "an-cr-cion-cedu-2526-058"
    assert it.url == html_url
    # haystack_body doit contenir les termes clés
    hs = it.raw.get("haystack_body", "")
    assert "sports" in hs
    assert "gouvernance" in hs
    # date parsée depuis le HTML
    assert it.published_at == datetime(2026, 4, 22)


def test_fetch_cr_html_200_pdf_404_keeps_item(monkeypatch):
    """HTML existe mais PDF en 404 : on garde un item (titre seul)."""
    html_url = (
        "https://www.assemblee-nationale.fr/dyn/17/comptes-rendus/"
        "cion-cedu/l17cion-cedu2526058_compte-rendu"
    )
    monkeypatch.setattr(
        mod, "_fetch_silent",
        _stub_fetch_silent({html_url: (200, _html_ok())}),
    )
    monkeypatch.setattr(mod, "_extract_pdf_text", lambda b, max_chars=10000: "")
    it = mod._fetch_cr("cion-cedu", "2526", 58, "Commission X")
    assert it is not None
    # haystack_body vide mais item présent
    assert it.raw.get("haystack_body", "") == ""


def test_fetch_source_increments_state(monkeypatch, tmp_path):
    """Après un run qui trouve 2 CR récents, le state persiste last_num au
    plus grand num trouvé ET la liste scanned.

    R37-B : scan descendant depuis max_num. On fabrique un cas où max_num=4,
    items publiés aux num 2 et 3 → scan 4(miss), 3(hit), 2(hit), 1(miss,
    puis stop si miss_tolerance atteint à un moment). Le state enregistre
    last_num=3 (le plus grand trouvé) et scanned=[2,3].
    """
    state_file = tmp_path / "an_cr_state.json"
    monkeypatch.setattr(mod, "STATE_PATH", state_file)

    def fake_fetch_cr(slug, session, num, label):
        if slug != "cion-cedu":
            return None
        if num in (2, 3):
            from src.models import Item
            return Item(
                source_id="an_cr_commissions",
                uid=f"an-cr-{slug}-{session}-{num:03d}",
                category="comptes_rendus",
                chamber="AN",
                title=f"CR {num}",
                url=f"http://ex/{num}",
                published_at=datetime(2026, 4, 22),
                summary="x",
                raw={"haystack_body": "body", "slug": slug,
                     "session": session, "num": num},
            )
        return None

    monkeypatch.setattr(mod, "_fetch_cr", fake_fetch_cr)

    src = {
        "id": "an_cr_commissions",
        "commissions": {"cion-cedu": "CCE"},
        "max_new_per_run": 10,
        "miss_tolerance": 3,
        "max_num": 4,
        "session": "2526",
    }
    items = mod.fetch_source(src)
    assert len(items) == 2
    # State persisté
    import json
    st = json.loads(state_file.read_text(encoding="utf-8"))
    assert st["2526"]["cion-cedu"]["last_num"] == 3
    assert st["2526"]["cion-cedu"]["scanned"] == [2, 3]


def test_fetch_source_resumes_from_state(monkeypatch, tmp_path):
    """Un run qui démarre avec scanned=[2,3] ne retente pas 2 ni 3.

    R38-J : scan en deux phases. En phase 1 (avant 1er hit), on tolère
    tous les misses jusqu'à trouver un CR ou épuiser le range — sans
    ça le scraper ne pouvait pas atteindre les n° éloignés de max_num
    (cas cion-cedu n°58 avec max_num=99).
    """
    state_file = tmp_path / "an_cr_state.json"
    state_file.write_text(
        '{"2526": {"cion-cedu": {"last_num": 3, "scanned": [2, 3]}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "STATE_PATH", state_file)

    tried_nums: list[int] = []

    def fake_fetch_cr(slug, session, num, label):
        tried_nums.append(num)
        return None  # aucune nouveauté au-delà

    monkeypatch.setattr(mod, "_fetch_cr", fake_fetch_cr)

    src = {
        "id": "an_cr_commissions",
        "commissions": {"cion-cedu": "CCE"},
        "max_new_per_run": 10,
        "miss_tolerance": 3,
        "max_num": 8,
        "session": "2526",
    }
    items = mod.fetch_source(src)
    assert items == []
    # Phase 1 : scan descendant de 8, pas de stop sur miss tant qu'aucun
    # CR trouvé. On tente 8, 7, 6, 5, 4 (tous miss), puis skip 3 et 2
    # (déjà dans `scanned`), puis tente 1 (miss). Aucun hit → on est
    # resté en phase 1 tout du long et on va jusqu'à num=0.
    assert tried_nums == [8, 7, 6, 5, 4, 1]


def test_fetch_source_reaches_distant_cr_in_phase1(monkeypatch, tmp_path):
    """R38-J : phase 1 doit traverser une longue zone de 404 pour
    atteindre un CR éloigné de max_num. Cas concret : cion-cedu session
    2526 dont le CR le plus haut est n°58 alors que max_num=99. Avec
    l'ancienne logique (miss_tolerance=3 dès le démarrage), le scan
    s'arrêtait à 96 et manquait le 58."""
    state_file = tmp_path / "an_cr_state.json"
    monkeypatch.setattr(mod, "STATE_PATH", state_file)

    HIGH_NUM = 8   # équivalent scaled du n°58 réel
    MAX_NUM = 20   # équivalent scaled du max_num=99

    def fake_fetch_cr(slug, session, num, label):
        if num > HIGH_NUM:
            return None  # tous les n° > 8 sont 404
        if num in (HIGH_NUM, HIGH_NUM - 1, HIGH_NUM - 2):
            from src.models import Item
            return Item(
                source_id="an_cr_commissions",
                uid=f"an-cr-{slug}-{session}-{num:03d}",
                category="comptes_rendus",
                chamber="AN",
                title=f"CR {num}",
                url=f"http://ex/{num}",
                published_at=datetime(2026, 4, 22),
                summary="x",
                raw={"haystack_body": "body", "slug": slug,
                     "session": session, "num": num},
            )
        return None

    monkeypatch.setattr(mod, "_fetch_cr", fake_fetch_cr)

    src = {
        "id": "an_cr_commissions",
        "commissions": {"cion-cedu": "CCE"},
        "max_new_per_run": 10,
        "miss_tolerance": 3,
        "max_num": MAX_NUM,
        "session": "2526",
    }
    items = mod.fetch_source(src)
    # Les 3 CR existants (6, 7, 8) sont tous ingérés — phase 1 a
    # traversé la zone 20..9 sans s'arrêter sur miss.
    assert len(items) == 3
    nums = sorted(int(it.raw["num"]) for it in items)
    assert nums == [HIGH_NUM - 2, HIGH_NUM - 1, HIGH_NUM]


def test_fetch_source_commissions_as_list(monkeypatch, tmp_path):
    """Config `commissions: [slug1, slug2]` accepté (label = slug)."""
    monkeypatch.setattr(mod, "STATE_PATH", tmp_path / "x.json")

    called = []

    def fake(slug, session, num, label):
        called.append((slug, label))
        return None

    monkeypatch.setattr(mod, "_fetch_cr", fake)
    mod.fetch_source({
        "commissions": ["cion-cedu", "cion-soc"],
        "max_new_per_run": 1,
        "miss_tolerance": 1,
        "session": "2526",
    })
    # Chaque slug a été appelé
    slugs = {c[0] for c in called}
    assert slugs == {"cion-cedu", "cion-soc"}
    # Le label défaut = slug quand on passe une liste
    for slug, label in called:
        assert slug == label


def test_extract_pdf_text_empty_bytes_returns_empty():
    """Bytes non-PDF → chaîne vide, pas d'exception."""
    assert mod._extract_pdf_text(b"") == ""
    assert mod._extract_pdf_text(b"not a pdf") == ""
