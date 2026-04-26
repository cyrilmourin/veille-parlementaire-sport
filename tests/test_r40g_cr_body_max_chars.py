"""R40-G (2026-04-26) — Limite extraction CR PDF/HTML 10k → 200k chars.

Contexte : signalé par Cyril 2026-04-26 depuis une session de la veille
Lidl où 5/9 CR de test rataient le matching keyword à cause de la
troncature à 10 000 caractères dans `_extract_pdf_text`. Sur les CR
longs (60-180k chars), les keywords cibles apparaissent souvent
au-delà du 10k-ème caractère, surtout quand une commission examine
plusieurs sujets dans la même séance.

Symétrie côté veille sport :
- AN  : `_extract_pdf_text` en `src/sources/an_cr_commissions.py`
- Sénat : `body_max_chars` en YAML + default dans
  `src/sources/senat_cr_commissions.py`

Trade-off : 200k chars = ~100 pages PDF stripées, couvre >95 % des CR
sport-relevants (Commission culture/sport notamment, qui examine
souvent audiovisuel + école + ESR + sport + JOP en une séance). Coût
mémoire ~200k × 32 CR/run = ~6 Mo, négligeable.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.sources import an_cr_commissions as an_mod
from src.sources import senat_cr_commissions as sen_mod
from src.sources import senat as senat_plen_mod
from src.sources import assemblee as an_plen_mod


SOURCES_YAML = Path(__file__).resolve().parent.parent / "config" / "sources.yml"


# ---------------------------------------------------------------------------
# 1. Defaults dans le code
# ---------------------------------------------------------------------------


def test_an_extract_pdf_text_default_max_chars_200k():
    """Le default de `_extract_pdf_text` est désormais 200 000 (×10 vs R39)."""
    import inspect
    sig = inspect.signature(an_mod._extract_pdf_text)
    assert sig.parameters["max_chars"].default == 200000


def test_senat_cr_default_body_max_200k():
    """Le default de `body_max_chars` côté Sénat est aussi à 200 000.
    Vérifié indirectement via fetch_source : sans `body_max_chars` dans
    le src, on prend 200k. Smoke check sur un body fictif > 10k."""
    # Body de ~30k chars de bruit + keyword à la position ~30k (au-delà
    # de l'ancienne limite 10k mais sous la nouvelle limite 200k).
    filler = "X " * 15000  # 30k chars
    long_body_with_match = filler + " mot-cle-au-milieu-tres-loin " + filler
    listing = (
        '<html><body>'
        '<h3 id=curses><a class="link" '
        'href="/compte-rendu-commissions/20260413/cult.html">'
        'Semaine du 13 avril 2026</a></h3>'
        '</body></html>'
    )
    week_html = f"<html><body><main>{long_body_with_match}</main></body></html>"

    def fake_fetch(url):
        if url.endswith("cult.html") and "20260413" in url:
            return week_html
        return listing

    import unittest.mock as mock
    with mock.patch.object(sen_mod, "fetch_text", side_effect=fake_fetch):
        items = sen_mod.fetch_source({
            "id": "senat_cr_culture",
            "url": "https://example.test/cr-culture.html",
            "category": "comptes_rendus",
            "commission_label": "Test",
            "commission_organe": "PO000",
            "max_new_per_run": 1,
            # ⚠ pas de `body_max_chars` → doit prendre le default
        })
    assert len(items) == 1
    body = items[0].raw["haystack_body"]
    # Le body doit dépasser 10k (preuve que le default n'est plus 10k)
    assert len(body) > 50000, f"body trop court : {len(body)} chars"
    # Le keyword au milieu doit être présent
    assert "mot-cle-au-milieu-tres-loin" in body


# ---------------------------------------------------------------------------
# 2. YAML — toutes les sources senat_cr_commissions_html sont à 200k
# ---------------------------------------------------------------------------


def _load_senat_cr_sources() -> list[dict]:
    with SOURCES_YAML.open() as f:
        cfg = yaml.safe_load(f)
    out: list[dict] = []
    for grp_val in cfg.values():
        if not isinstance(grp_val, dict):
            continue
        for s in grp_val.get("sources", []) or []:
            if isinstance(s, dict) and s.get("format") == "senat_cr_commissions_html":
                out.append(s)
    return out


def test_yaml_toutes_sources_cr_a_200k():
    """Les 5 sources `senat_cr_*` (culture + lois + finances + etrangeres
    + affaires_sociales) doivent avoir `body_max_chars: 200000`.
    Régression du R40-G : éviter qu'une nouvelle source soit ajoutée
    avec l'ancien default 10k par habitude copier-coller."""
    sources = _load_senat_cr_sources()
    assert len(sources) >= 5  # 1 R37-A + 3 R40-C + 1 R40-E
    for s in sources:
        bm = s.get("body_max_chars")
        assert bm == 200000, (
            f"{s['id']} : body_max_chars={bm} (attendu 200000). "
            "Si on baisse volontairement, mettre un commentaire en YAML.")


# ---------------------------------------------------------------------------
# 3. Régression côté AN — l'extraction tronque bien à max_chars
# ---------------------------------------------------------------------------


def test_an_extract_respecte_max_chars_explicite():
    """Si l'appelant force `max_chars=5000`, on tronque à 5000."""
    # On ne peut pas générer un vrai PDF facilement ; on utilise un
    # FakePdfReader pour valider la borne supérieure de la boucle.
    class _FakePage:
        def __init__(self, txt: str):
            self._txt = txt

        def extract_text(self):
            return self._txt

    class _FakeReader:
        def __init__(self, *args, **kwargs):
            # 200 pages × 1000 chars = 200 000 chars de matière
            self.pages = [_FakePage("a" * 1000) for _ in range(200)]

    import sys
    fake_pypdf = type(sys)("pypdf")
    fake_pypdf.PdfReader = _FakeReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out_5k = an_mod._extract_pdf_text(b"unused", max_chars=5000)
        out_200k = an_mod._extract_pdf_text(b"unused", max_chars=200000)
        out_default = an_mod._extract_pdf_text(b"unused")
    finally:
        del sys.modules["pypdf"]

    # max_chars=5000 → 5k chars max
    assert len(out_5k) <= 5000
    # max_chars=200000 → on doit sortir entre 50k et 200k (les 200 pages
    # contiennent 200k, tronqué à 200k)
    assert 50000 <= len(out_200k) <= 200000
    # default : 200k
    assert 50000 <= len(out_default) <= 200000


def test_an_extract_pdf_text_default_couvre_au_dela_de_10k():
    """Régression directe : avec le default, on doit pouvoir capter un
    keyword à la position 50 000."""
    class _FakePage:
        def __init__(self, txt: str):
            self._txt = txt

        def extract_text(self):
            return self._txt

    class _FakeReader:
        def __init__(self, *args, **kwargs):
            # Page 1 : remplissage 30k chars sans le keyword
            # Page 2 : 10k chars contenant le keyword cible
            # Page 3+ : remplissage
            filler = "lorem ipsum " * 2500  # ~30k chars
            keyword_page = ("Examen du PJL relatif au sport "
                            "professionnel et à la lutte contre le dopage. "
                            * 200)  # ~10k chars contenant "sport" et "dopage"
            self.pages = [
                _FakePage(filler),
                _FakePage(keyword_page),
                _FakePage(filler),
            ]

    import sys
    fake_pypdf = type(sys)("pypdf")
    fake_pypdf.PdfReader = _FakeReader
    sys.modules["pypdf"] = fake_pypdf
    try:
        out = an_mod._extract_pdf_text(b"unused")
    finally:
        del sys.modules["pypdf"]

    # Avec l'ancien default 10k, "sport" en page 2 (offset ~30k) était
    # tronqué hors haystack. Avec 200k, il doit y être.
    assert "sport" in out
    assert "dopage" in out
    assert len(out) > 30000, (
        f"body trop court ({len(out)}) — la limite n'a pas été remontée")


# ---------------------------------------------------------------------------
# 4. CR plénières (Sénat senat_debats/senat_cri + AN an_syceron) :
#    haystack_body 200k désormais exposé. Avant R40-G, NI summary 2000c
#    NI haystack_body → matcher ne voyait que les premiers 2000c du CR
#    plénier de 200-400k chars. Bug pire que le 10k des commissions.
# ---------------------------------------------------------------------------


def test_senat_plenary_haystack_body_present_dans_raw():
    """Avant R40-G, `_fetch_debats_zip` n'exposait pas `haystack_body`
    dans le raw — seulement summary[:2000]. Vérifie que le champ est
    bien posé maintenant. Test indirect via lecture de la source : on
    grep le code pour s'assurer que la clé est présente."""
    src_path = senat_plen_mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src_code = f.read()
    # La clé doit être assignée dans le raw avec la limite 200k
    assert '"haystack_body": text[:200000]' in src_code, (
        "senat.py:_fetch_debats_zip doit exposer haystack_body[:200000] "
        "(R40-G)")


def test_an_syceron_haystack_body_present_dans_raw():
    """Idem côté AN syceron (assemblee.py:_normalize_syceron)."""
    src_path = an_plen_mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src_code = f.read()
    assert '"haystack_body": text[:200000]' in src_code, (
        "assemblee.py:_normalize_syceron doit exposer haystack_body[:200000] "
        "(R40-G)")
