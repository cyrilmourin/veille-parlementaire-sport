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
    """counts reflète le total réel.

    R42-CV (2026-05-15) : avant, `amdt_seance` était capé à 200 et le
    test vérifiait `len(payload["amdt_seance"]) == 200`. Cap remonté à
    5000 pour aligner badge onglet et donut total (cf. test
    `test_amdt_commission_pas_tronque_a_200` dans
    `test_r42cv_special_ppl_fixes.py`). 250 amdt → 250 dans le payload.
    """
    huge = [_row(category="amendements",
                 raw={"texte_ref": AN_TEXTE_REF})
            for _ in range(250)]
    buckets = collect_special_ppl(huge)
    payload = build_payload(buckets)
    assert payload["counts"]["amdt_seance"] == 250
    # R42-CV : plus de slice à 200, tous les amdt remontent
    assert len(payload["amdt_seance"]) == 250


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


# ---------------------------------------------------------------------------
# R41-Q : extract nettoyé + tri par article
# ---------------------------------------------------------------------------


def test_extract_strip_dossier_prefix():
    """R41-Q : préfixe 'Dossier : ... — ' retiré (titre dosleg parent
    redondant sur tous les amdt PPL)."""
    rows = [_row(
        category="amendements",
        title="Amdt n°AC1",
        raw={
            "texte_ref": AN_TEXTE_REF,
            "haystack_body": (
                "Dossier : Proposition de loi relative à l'organisation "
                "et au financement du sport professionnel — Le présent "
                "amendement vise à clarifier le rôle des fédérations."
            ),
        },
    )]
    payload = build_payload(collect_special_ppl(rows))
    extract = payload["amdt_commission"][0]["extract"]
    assert "Dossier" not in extract
    assert extract.startswith("Le présent amendement")


def test_extract_strip_metadata_tail():
    """R41-Q : queue '— Auteur : ... — Statut : ...' retirée."""
    rows = [_row(
        category="amendements",
        title="Amdt n°AC1",
        raw={
            "texte_ref": AN_TEXTE_REF,
            "haystack_body": (
                "Le présent amendement vise à clarifier. — Auteur : M. X "
                "— Statut : déposé — Article : ARTICLE 5"
            ),
        },
    )]
    payload = build_payload(collect_special_ppl(rows))
    extract = payload["amdt_commission"][0]["extract"]
    assert "Auteur" not in extract
    assert "Statut" not in extract
    assert "Article :" not in extract
    assert extract.startswith("Le présent amendement")


def test_extract_strip_html_tags():
    """R41-Q : balises XHTML du dispositif AN strippées."""
    rows = [_row(
        category="amendements",
        title="Amdt n°AC1",
        raw={
            "texte_ref": AN_TEXTE_REF,
            "haystack_body": (
                "<p>Le présent amendement vise à <i>clarifier</i> "
                "le rôle&nbsp;des fédérations.</p>"
            ),
        },
    )]
    payload = build_payload(collect_special_ppl(rows))
    extract = payload["amdt_commission"][0]["extract"]
    assert "<" not in extract
    assert ">" not in extract
    assert "&nbsp;" not in extract
    assert "clarifier" in extract


def test_payload_article_extrait_du_titre():
    """R41-Q : le champ 'article' est extrait du titre amdt."""
    rows = [_row(
        category="amendements",
        title="Amdt n°AC118 · art. ARTICLE 5 · sur PPL",
        raw={"texte_ref": AN_TEXTE_REF},
    )]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["amdt_commission"][0]["article"] == "ARTICLE 5"


def test_payload_article_vide_si_pas_dans_titre():
    rows = [_row(
        category="amendements",
        title="Amdt n°AC118",
        raw={"texte_ref": AN_TEXTE_REF},
    )]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["amdt_commission"][0]["article"] == ""


def test_payload_amdt_commission_by_article_groupe_et_trie():
    """R41-Q : groupage par article + tri (Article 1ER → 2 → 2 BIS → 3)."""
    rows = [
        _row(category="amendements", title="Amdt n°AC10 · art. ARTICLE 3 · x",
             raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="amendements", title="Amdt n°AC11 · art. ARTICLE 1ER · x",
             raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="amendements", title="Amdt n°AC12 · art. ARTICLE 2 BIS · x",
             raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="amendements", title="Amdt n°AC13 · art. ARTICLE 2 · x",
             raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="amendements", title="Amdt n°AC14 · art. ARTICLE 1ER · y",
             raw={"texte_ref": AN_TEXTE_REF}),
    ]
    payload = build_payload(collect_special_ppl(rows))
    groups = payload["amdt_commission_by_article"]
    article_order = [g["article"] for g in groups]
    assert article_order == ["ARTICLE 1ER", "ARTICLE 2", "ARTICLE 2 BIS", "ARTICLE 3"]
    # Article 1ER doit avoir 2 amdt
    assert len(groups[0]["items"]) == 2


def test_payload_amdt_seance_by_article_existe():
    """Le payload séance a aussi sa version groupée."""
    rows = [_row(category="amendements", title="Amdt n°5 · art. ARTICLE 1ER · x",
                 raw={"texte_ref": AN_TEXTE_REF})]
    payload = build_payload(collect_special_ppl(rows))
    assert "amdt_seance_by_article" in payload
    assert len(payload["amdt_seance_by_article"]) == 1


# ---------------------------------------------------------------------------
# R41-R : URL Sénat malformée → bascule vers URL canonique
# ---------------------------------------------------------------------------


def test_url_senat_malformee_remplacee_par_canonique():
    """R41-R : URL Sénat dosleg non-canonique (ex. 's92930456' qui
    renvoie vers texte épargne) bascule vers URL_SENAT_DOSSIER."""
    from src.special_ppl import URL_SENAT_DOSSIER
    rows = [_row(
        category="dossiers_legislatifs",
        title=("Proposition de loi relative à l'organisation, à la "
               "gestion et au financement du sport professionnel"),
        chamber="Senat",
        url="http://www.senat.fr/dossier-legislatif/s92930456.html",
    )]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["dosleg"][0]["url"] == URL_SENAT_DOSSIER


def test_url_senat_canonique_preservee():
    """L'URL Sénat dosleg au format canonique (pplXX-YYY.html) est
    préservée."""
    rows = [_row(
        category="dossiers_legislatifs",
        title=("Proposition de loi relative à l'organisation, à la "
               "gestion et au financement du sport professionnel"),
        chamber="Senat",
        url="https://www.senat.fr/dossier-legislatif/ppl24-456.html",
    )]
    payload = build_payload(collect_special_ppl(rows))
    assert "ppl24-456" in payload["dosleg"][0]["url"]
    # Pas de bascule erronée
    assert payload["dosleg"][0]["url"].endswith("ppl24-456.html")


def test_meeting_kind_seance_via_organe_PO838901():
    """R41-T : URL /organes/PO838901 → Séance Plénière."""
    from src.special_ppl import _detect_meeting_kind
    r = {"category": "agenda",
         "url": "https://www.assemblee-nationale.fr/dyn/17/organes/PO838901",
         "title": "Discussion (n° 1560)"}
    assert _detect_meeting_kind(r) == "Séance publique"


def test_meeting_kind_commission_via_organe_PO419604():
    """R41-T : URL /organes/PO419604 (commission CCE) → Commission."""
    from src.special_ppl import _detect_meeting_kind
    r = {"category": "agenda",
         "url": "https://www.assemblee-nationale.fr/dyn/17/organes/PO419604",
         "title": "Examen de la PPL (n° 1560)"}
    assert _detect_meeting_kind(r) == "Commission"


def test_meeting_kind_via_titre_sans_url_organe():
    """R41-T : fallback heuristique titre quand URL pas /organes/."""
    from src.special_ppl import _detect_meeting_kind
    r1 = {"category": "agenda", "url": "/items/agenda/",
          "title": "Discussion de la proposition de loi"}
    assert _detect_meeting_kind(r1) == "Séance publique"
    r2 = {"category": "agenda", "url": "/items/agenda/",
          "title": "Examen de la proposition de loi"}
    assert _detect_meeting_kind(r2) == "Commission"
    r3 = {"category": "agenda", "url": "/items/agenda/",
          "title": "Désignation du rapporteur"}
    assert _detect_meeting_kind(r3) == "Commission"


def test_meeting_kind_vide_pour_non_agenda():
    from src.special_ppl import _detect_meeting_kind
    r = {"category": "amendements", "url": "x", "title": "y"}
    assert _detect_meeting_kind(r) == ""


def test_payload_meeting_kind_dans_agenda():
    """L'agenda dans le payload contient le meeting_kind calculé."""
    rows = [_row(
        category="agenda",
        title="Examen de la proposition de loi (n° 1560)",
        url="https://www.assemblee-nationale.fr/dyn/17/organes/PO419604",
    )]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["agenda"][0]["meeting_kind"] == "Commission"


def test_meta_rapporteurs_4_par_ordre_alphabetique_nom():
    """R41-T : 4 rapporteurs dans le payload meta, triés par NOM."""
    payload = build_payload(collect_special_ppl([]))
    rapps = payload["meta"]["rapporteurs"]
    assert len(rapps) == 4
    noms = [r["nom"] for r in rapps]
    assert noms == sorted(noms, key=lambda x: x.upper())
    # Champs requis
    for r in rapps:
        assert r["prenom"] and r["nom"] and r["groupe"]
        assert r["fiche_url"].startswith("https://www.assemblee-nationale.fr")
        assert r["photo_url"].startswith("https://www2.assemblee-nationale.fr")


def test_url_an_dosleg_preservee():
    """L'URL AN dosleg (rerouté par R41-K) est préservée telle quelle."""
    rows = [_row(
        category="dossiers_legislatifs",
        title=("Proposition de loi relative à l'organisation, à la "
               "gestion et au financement du sport professionnel"),
        chamber="AN",
        url="https://www.assemblee-nationale.fr/dyn/17/textes/l17b1560_proposition-loi",
    )]
    payload = build_payload(collect_special_ppl(rows))
    assert "l17b1560" in payload["dosleg"][0]["url"]


# ---------------------------------------------------------------------------
# R41-W : tri articles (APRÈS après ARTICLE), sort fallback statut
# ---------------------------------------------------------------------------


def test_article_apres_passe_apres_article_principal():
    """R41-W : ARTICLE 1ER C doit passer AVANT « APRÈS L'ARTICLE 1ER C »."""
    rows = [
        _row(category="amendements",
             title="Amdt n°AC5 · art. APRÈS L'ARTICLE 1ER C, insérer l'article suivant: · sur PPL",
             raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="amendements",
             title="Amdt n°AC10 · art. ARTICLE 1ER C · sur PPL",
             raw={"texte_ref": AN_TEXTE_REF}),
    ]
    payload = build_payload(collect_special_ppl(rows))
    groups = payload["amdt_commission_by_article"]
    article_order = [g["article"] for g in groups]
    # ARTICLE 1ER C en 1er, APRÈS L'ARTICLE 1ER C ensuite
    assert article_order[0] == "ARTICLE 1ER C"
    assert article_order[1].startswith("APRÈS L'ARTICLE 1ER C")


def test_article_sub_letter_ordering():
    """R41-W : ARTICLE 1ER → 1ER A → 1ER AA → 1ER B → 1ER C → 2."""
    rows = [
        _row(category="amendements", title="Amdt n°AC1 · art. ARTICLE 1ER C · x",
             raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="amendements", title="Amdt n°AC2 · art. ARTICLE 1ER A · x",
             raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="amendements", title="Amdt n°AC3 · art. ARTICLE 2 · x",
             raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="amendements", title="Amdt n°AC4 · art. ARTICLE 1ER · x",
             raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="amendements", title="Amdt n°AC5 · art. ARTICLE 1ER B · x",
             raw={"texte_ref": AN_TEXTE_REF}),
    ]
    payload = build_payload(collect_special_ppl(rows))
    order = [g["article"] for g in payload["amdt_commission_by_article"]]
    assert order == [
        "ARTICLE 1ER", "ARTICLE 1ER A", "ARTICLE 1ER B", "ARTICLE 1ER C",
        "ARTICLE 2",
    ]


def test_article_bis_apres_article_principal():
    """ARTICLE 2 doit passer avant ARTICLE 2 BIS."""
    rows = [
        _row(category="amendements", title="Amdt n°AC1 · art. ARTICLE 2 BIS · x",
             raw={"texte_ref": AN_TEXTE_REF}),
        _row(category="amendements", title="Amdt n°AC2 · art. ARTICLE 2 · x",
             raw={"texte_ref": AN_TEXTE_REF}),
    ]
    payload = build_payload(collect_special_ppl(rows))
    order = [g["article"] for g in payload["amdt_commission_by_article"]]
    assert order == ["ARTICLE 2", "ARTICLE 2 BIS"]


def test_sort_fallback_statut_en_traitement():
    """R41-W : amdt sans sort → 'En traitement' (depuis statut ou par défaut)."""
    rows = [
        _row(category="amendements", title="Amdt n°AC1",
             raw={"texte_ref": AN_TEXTE_REF, "sort": "", "statut": ""}),
    ]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["amdt_commission"][0]["sort"] == "En traitement"


def test_sort_fallback_via_statut_explicite():
    """R41-W : si raw.sort vide et raw.statut='En traitement' → sort='En traitement'."""
    rows = [
        _row(category="amendements", title="Amdt n°AC1",
             raw={"texte_ref": AN_TEXTE_REF, "sort": "",
                  "statut": "En traitement"}),
    ]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["amdt_commission"][0]["sort"] == "En traitement"


def test_sort_prend_priorite_sur_statut():
    """R41-W : si raw.sort posé, c'est lui qui sort (pas le statut)."""
    rows = [
        _row(category="amendements", title="Amdt n°AC1",
             raw={"texte_ref": AN_TEXTE_REF, "sort": "Adopté",
                  "statut": "En traitement"}),
    ]
    payload = build_payload(collect_special_ppl(rows))
    assert payload["amdt_commission"][0]["sort"] == "Adopté"


def test_meta_url_amdt_liste_an_expose():
    """R41-W : URL liste amdt AN exposée pour le bouton liasse."""
    payload = build_payload(collect_special_ppl([]))
    url = payload["meta"]["url_amdt_liste_an"]
    assert "DLR5L17N51732" in url
    assert "examen=EXANR5L17PO419604B1560P0D1" in url


# ---------------------------------------------------------------------------
# R41-X : extraction articles texte AN
# ---------------------------------------------------------------------------


def test_fetch_an_text_articles_offline(monkeypatch):
    """R41-X : fetch articles AN — test offline avec HTML stub."""
    from src.special_ppl import fetch_an_text_articles

    stub_html = (
        '<html><body>'
        '<p class="assnat9ArticleNum">Article 1<span>er</span> A (nouveau)</p>'
        '<p class="assnatLoiTexte">Le code du sport est ainsi modifié.</p>'
        '<p class="assnatLoiTexte">Un alinéa est inséré.</p>'
        '<p class="assnat9ArticleNum">Article 2 bis</p>'
        '<p class="assnatLoiTexte">Le présent article concerne les ligues.</p>'
        '</body></html>'
    )

    class _StubResp:
        text = stub_html
        def raise_for_status(self): pass

    class _StubClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url): return _StubResp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _StubClient)
    arts = fetch_an_text_articles()
    # Normalisation labels : « 1 er A » → « 1ER A », parenthèses strippées
    assert "ARTICLE 1ER A" in arts
    assert "ARTICLE 2 BIS" in arts
    # Les paragraphes de l'article 1ER A sont concaténés (2 <p>)
    assert "Le code du sport" in arts["ARTICLE 1ER A"]
    assert "Un alinéa" in arts["ARTICLE 1ER A"]
    # Le 2 bis ne contient pas le texte de l'article 1ER A
    assert "Le code du sport" not in arts["ARTICLE 2 BIS"]
    assert "ligues" in arts["ARTICLE 2 BIS"]


def test_fetch_an_text_articles_resilient_au_reseau(monkeypatch):
    """R41-X : si fetch échoue, retourne {} (pas d'exception)."""
    from src.special_ppl import fetch_an_text_articles

    class _ErrClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url): raise RuntimeError("network down")

    import httpx
    monkeypatch.setattr(httpx, "Client", _ErrClient)
    assert fetch_an_text_articles() == {}


def test_export_end_to_end(tmp_path, monkeypatch):
    # R41-X : on neutralise l'appel réseau fetch_an_text_articles dans
    # le test e2e (offline) pour ne pas dépendre du site AN.
    from src import special_ppl as mod
    monkeypatch.setattr(mod, "fetch_an_text_articles", lambda: {})
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
