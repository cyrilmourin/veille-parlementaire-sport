"""R41-M (2026-05-07) — Module dédié PPL Sport professionnel.

Tests :
1. row_matches_special_ppl : détection multi-critères (texte_ref / URL / titre)
2. collect_special_ppl : tri par bucket (commission vs séance)
3. build_payload : structure du JSON exposé à Hugo
4. write_data_file + write_page_stub : génération des fichiers
"""
from __future__ import annotations

import json
from pathlib import Path

from src.special_ppl import (
    AN_TEXTE_REF,
    PPL_KEY,
    PPL_TITLE,
    URL_AN_TEXTE,
    build_payload,
    collect_special_ppl,
    export,
    row_matches_special_ppl,
    write_data_file,
    write_page_stub,
)


def _row(**kw):
    base = {
        "title": "Item",
        "url": "",
        "category": "amendements",
        "chamber": "AN",
        "published_at": "2026-05-12T09:00:00",
        "raw": {},
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# row_matches_special_ppl
# ---------------------------------------------------------------------------


def test_match_via_texte_ref_an():
    r = _row(raw={"texte_ref": AN_TEXTE_REF})
    assert row_matches_special_ppl(r) is True


def test_match_via_texte_ref_senat():
    r = _row(raw={"texte_ref": "PIONSNR5S459BTA0137"})
    assert row_matches_special_ppl(r) is True


def test_match_via_dossier_id():
    r = _row(raw={"dossier_id": AN_TEXTE_REF})
    assert row_matches_special_ppl(r) is True


def test_match_via_url_an_textes():
    r = _row(url="https://www.assemblee-nationale.fr/dyn/17/textes/l17b1560_proposition-loi")
    assert row_matches_special_ppl(r) is True


def test_match_via_url_senat_ppl24_456():
    r = _row(url="https://www.senat.fr/dossier-legislatif/ppl24-456.html")
    assert row_matches_special_ppl(r) is True


def test_match_via_titre_complet():
    """Titre contenant tous les mots requis."""
    r = _row(title="Proposition de loi relative à l'organisation, à la "
                   "gestion et au financement du sport professionnel")
    assert row_matches_special_ppl(r) is True


def test_match_via_n_1560_dans_titre_agenda():
    r = _row(title="Examen de la proposition de loi (n° 1560) ...")
    assert row_matches_special_ppl(r) is True


def test_no_match_titre_sans_mots_requis():
    """Titre avec sport mais pas tous les mots requis."""
    r = _row(title="Le sport amateur en France")
    assert row_matches_special_ppl(r) is False


def test_no_match_autre_dossier():
    r = _row(raw={"texte_ref": "PIONANR5L17B9999"})
    assert row_matches_special_ppl(r) is False


# ---------------------------------------------------------------------------
# collect_special_ppl — buckets
# ---------------------------------------------------------------------------


def test_collect_amdt_commission_via_prefixe_lettre_titre():
    """R41-P : titre « Amdt n°AC118 » → bucket amdt_commission
    (le préfixe alphabétique signale toujours une commission AN)."""
    rows = [
        _row(category="amendements",
             raw={"texte_ref": AN_TEXTE_REF},
             title="Amdt n°AC118 · art. ARTICLE 5 · sur PPL sport pro",
             url="https://www.assemblee-nationale.fr/dyn/17/amendements/AMANR5L17PO419604B1560P0D1N000118"),
    ]
    out = collect_special_ppl(rows)
    assert len(out["amdt_commission"]) == 1
    assert len(out["amdt_seance"]) == 0


def test_collect_amdt_seance_si_numero_pur():
    """R41-P : titre « Amdt n°118 » (numéro pur) → bucket séance."""
    rows = [
        _row(category="amendements",
             raw={"texte_ref": AN_TEXTE_REF},
             title="Amdt n°118 · art. ARTICLE 5 · sur PPL sport pro",
             url="https://www.assemblee-nationale.fr/dyn/17/amendements/AMANR5L17PO710764B1560P0D1N000118"),
    ]
    out = collect_special_ppl(rows)
    assert len(out["amdt_seance"]) == 1
    assert len(out["amdt_commission"]) == 0


def test_collect_amdt_commission_via_stage_si_pas_de_titre():
    """Fallback : raw.stage='commission' → bucket commission."""
    rows = [
        _row(category="amendements",
             raw={"texte_ref": AN_TEXTE_REF, "stage": "commission"},
             title="",
             url="http://example/x"),
    ]
    out = collect_special_ppl(rows)
    assert len(out["amdt_commission"]) == 1


def test_collect_dosleg_et_agenda():
    rows = [
        _row(category="dossiers_legislatifs",
             title="Proposition de loi relative à l'organisation, à la "
                   "gestion et au financement du sport professionnel"),
        _row(category="agenda",
             title="Examen de la PPL (n° 1560)"),
    ]
    out = collect_special_ppl(rows)
    assert len(out["dosleg"]) == 1
    assert len(out["agenda"]) == 1


def test_collect_ignore_items_non_lies():
    rows = [
        _row(title="Sport amateur en France", category="communiques"),
        _row(raw={"texte_ref": "PIONANR5L17B9999"}, category="amendements"),
    ]
    out = collect_special_ppl(rows)
    assert all(len(v) == 0 for v in out.values())


def test_collect_tri_date_desc_par_bucket():
    rows = [
        _row(category="amendements", raw={"texte_ref": AN_TEXTE_REF},
             published_at="2026-05-10T00:00:00"),
        _row(category="amendements", raw={"texte_ref": AN_TEXTE_REF},
             published_at="2026-05-15T00:00:00"),
        _row(category="amendements", raw={"texte_ref": AN_TEXTE_REF},
             published_at="2026-05-12T00:00:00"),
    ]
    out = collect_special_ppl(rows)
    bucket = out["amdt_seance"]
    dates = [r["published_at"] for r in bucket]
    assert dates == sorted(dates, reverse=True)


# ---------------------------------------------------------------------------
# build_payload
# ---------------------------------------------------------------------------


def test_build_payload_meta():
    payload = build_payload({"dosleg": [], "agenda": [],
                             "amdt_commission": [], "amdt_seance": [],
                             "comptes_rendus": [], "communiques": [],
                             "questions": []})
    assert payload["meta"]["key"] == PPL_KEY
    assert payload["meta"]["title"] == PPL_TITLE
    assert payload["meta"]["url_an_texte"] == URL_AN_TEXTE
    assert "generated_at" in payload["meta"]


def test_build_payload_counts_avant_slice():
    """counts reflète le total réel, pas le total slice."""
    huge = [_row(category="amendements",
                 raw={"texte_ref": AN_TEXTE_REF})
            for _ in range(250)]
    buckets = collect_special_ppl(huge)
    payload = build_payload(buckets)
    assert payload["counts"]["amdt_seance"] == 250
    # Mais le bucket payload est slicé à la limite
    assert len(payload["amdt_seance"]) == 200


def test_build_payload_row_field_subset():
    """Le payload row n'expose que des champs sûrs (pas de raw complet)."""
    rows = [_row(category="amendements", raw={
        "texte_ref": AN_TEXTE_REF, "auteur": "M. X", "groupe": "ABC",
        "stage": "1ère lecture", "secret_internal": "DO NOT EXPORT",
    }, title="Amdt n°1", url="http://example/1")]
    buckets = collect_special_ppl(rows)
    payload = build_payload(buckets)
    item = payload["amdt_seance"][0]
    assert item["auteur"] == "M. X"
    assert item["groupe"] == "ABC"
    assert "secret_internal" not in item


# ---------------------------------------------------------------------------
# write_data_file + write_page_stub + export
# ---------------------------------------------------------------------------


def test_write_data_file(tmp_path):
    payload = {"meta": {"key": "test"}, "counts": {}}
    write_data_file(tmp_path, payload)
    p = tmp_path / "special_ppl.json"
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["meta"]["key"] == "test"


def test_write_page_stub(tmp_path):
    write_page_stub(tmp_path)
    p = tmp_path / "ppl-sport-professionnel.md"
    assert p.exists()
    text = p.read_text()
    assert "type: page" in text
    assert "layout: ppl-sport-pro" in text
    assert PPL_TITLE in text


def test_payload_extract_400_chars_strip_titre():
    """R41-P : extract = haystack_body sans le titre, ≤ 400 chars."""
    title = "Amdt n°AC118 · art. ARTICLE 5"
    body = title + " : Le présent amendement vise à clarifier le rôle des fédérations sportives dans la régulation du sport professionnel. " * 10
    rows = [_row(category="amendements", title=title,
                 raw={"texte_ref": AN_TEXTE_REF, "haystack_body": body})]
    payload = build_payload(collect_special_ppl(rows))
    item = payload["amdt_commission"][0]
    assert item["extract"]
    assert len(item["extract"]) <= 401  # 400 + ellipsis
    # Le titre n'est PAS dans l'extract (strippé)
    assert not item["extract"].startswith(title)
    # Le contenu réel commence par "Le présent amendement"
    assert item["extract"].startswith("Le présent amendement")


def test_payload_sort_expose():
    """R41-P : raw.sort exposé dans le payload pour le filtre UI."""
    rows = [_row(category="amendements", title="Amdt n°AC1",
                 raw={"texte_ref": AN_TEXTE_REF, "sort": "Adopté"})]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["amdt_commission"][0]["sort"] == "Adopté"


def test_payload_url_agenda_organe_remplacee_par_interne():
    """R41-P : URLs agenda /dyn/17/organes/PO… → /items/agenda/."""
    rows = [_row(category="agenda",
                 title="Examen de la PPL (n° 1560)",
                 url="https://www.assemblee-nationale.fr/dyn/17/organes/PO419604")]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["agenda"][0]["url"] == "/items/agenda/"


def test_payload_url_amdt_preservee():
    """Les URLs amendements ne sont PAS réécrites (seulement agenda)."""
    url = "https://www.assemblee-nationale.fr/dyn/17/amendements/AMANR5L17B1560X"
    rows = [_row(category="amendements", title="Amdt n°AC1",
                 raw={"texte_ref": AN_TEXTE_REF}, url=url)]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["amdt_commission"][0]["url"] == url


def test_export_end_to_end(tmp_path):
    rows = [
        _row(category="amendements", raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="agenda", title="Examen PPL (n° 1560)"),
        _row(title="Sport amateur"),  # not matched, ignored
    ]
    payload = export(rows, tmp_path)
    assert (tmp_path / "data" / "special_ppl.json").exists()
    assert (tmp_path / "content" / "ppl-sport-professionnel.md").exists()
    assert payload["counts"]["amdt_seance"] == 1
    assert payload["counts"]["agenda"] == 1
