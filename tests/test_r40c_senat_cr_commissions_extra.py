"""R40-C (2026-04-26) — Élargissement des CR commissions Sénat.

Symétrie exacte avec R40-B (qui a élargi les agendas) mais côté
comptes rendus hebdomadaires. Avant : 1 seule commission Sénat couverte
côté CR (`senat_cr_culture` PO211490 / slug `culture`). Après : 4
commissions, soit toutes les commissions permanentes Sénat ayant un
recoupement plausible avec la veille sport :

- Commission des lois (PO211495 / slug `lois`)
- Commission des finances (PO211494 / slug `finances`)
- Commission affaires étrangères, défense, forces armées (PO211491 /
  slug `affaires-etrangeres`)

Affaires sociales (PO211493) reste explicitement exclue (R35-D).
Affaires économiques + Aménagement du territoire écartées (slug 404
côté listing CR + faible recoupement avec sport).

Le bypass organe R27 étant désactivé depuis R39-K, les CR ne remontent
que sur match keyword métier dans le `haystack_body` — pas de risque
de pollution type « affaires sociales ». Le titre reste « Semaine du
D MOIS YYYY » sans préfixe commission (R38-E, anti-faux-positif sport).

Le scraper `senat_cr_commissions` est générique sur `commission_label`
+ `commission_organe` ; aucune modification du module n'a été nécessaire.

Investigation associée — gap (b) groupes d'études Sénat : la page
`/groupe-etude/etu_<id>.html` ne liste QUE la composition du groupe,
pas de CR ni de réunions structurées. Pas de connecteur dédié possible
côté Sénat. Les travaux d'un GE sport remontent indirectement via les
CR de la commission de rattachement (culture pour le GE Sport) et via
les rapports d'information.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.sources import senat_cr_commissions as mod


SOURCES_YAML = Path(__file__).resolve().parent.parent / "config" / "sources.yml"


def _load_senat_cr_sources() -> list[dict]:
    """Toutes les sources de format senat_cr_commissions_html."""
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


# ---------------------------------------------------------------------------
# 1. Déclarations YAML
# ---------------------------------------------------------------------------


def test_yaml_5_sources_senat_cr_commissions():
    """1 R37-A + 3 R40-C + 1 R40-E = 5 sources actives.
    R40-E (2026-04-26) a réintégré la commission affaires sociales
    (PO211493) après que R39-K ait désactivé le bypass organe R27."""
    sources = _load_senat_cr_sources()
    assert len(sources) == 5


def test_yaml_r40c_three_new_ids_present():
    ids = {s["id"] for s in _load_senat_cr_sources()}
    assert "senat_cr_lois" in ids
    assert "senat_cr_finances" in ids
    assert "senat_cr_etrangeres" in ids
    assert "senat_cr_culture" in ids


def test_yaml_r40c_codes_organes_distincts():
    by_id = {s["id"]: s for s in _load_senat_cr_sources()}
    assert by_id["senat_cr_culture"]["commission_organe"] == "PO211490"
    assert by_id["senat_cr_lois"]["commission_organe"] == "PO211495"
    assert by_id["senat_cr_finances"]["commission_organe"] == "PO211494"
    assert by_id["senat_cr_etrangeres"]["commission_organe"] == "PO211491"
    codes = [s["commission_organe"] for s in by_id.values()]
    assert len(codes) == len(set(codes))


def test_yaml_r40e_affaires_sociales_reintegree_cr():
    """R40-E (2026-04-26) — symétrique côté CR : réintégration de la
    commission affaires sociales (PO211493). Remplace l'ancien
    `test_yaml_r40c_no_affaires_sociales` qui durcissait R35-D —
    supprimé en R40-E (R39-K rend R35-D obsolète)."""
    by_id = {s["id"]: s for s in _load_senat_cr_sources()}
    assert "senat_cr_affaires_sociales" in by_id
    assert by_id["senat_cr_affaires_sociales"]["commission_organe"] == "PO211493"
    assert by_id["senat_cr_affaires_sociales"]["url"].endswith("/affaires-sociales.html")


def test_yaml_r40c_urls_pointent_vers_compte_rendu_commissions():
    """Toutes les URLs sont des listings hebdo `/compte-rendu-commissions/<slug>.html`."""
    for s in _load_senat_cr_sources():
        assert s["url"].startswith("https://www.senat.fr/compte-rendu-commissions/"), (
            f"{s['id']} : URL inattendue {s['url']!r}"
        )
        assert s["url"].endswith(".html")


def test_yaml_r40c_slugs_listing_corrects():
    """Slug listing CR par commission. Diffère parfois du slug agenda
    R40-B : ici on utilise le slug court `affaires-etrangeres` (le slug
    long `commission-des-affaires-etrangeres-de-la-defense-et-des-forces-armees`
    n'a pas d'équivalent côté `/compte-rendu-commissions/`)."""
    by_id = {s["id"]: s for s in _load_senat_cr_sources()}
    assert by_id["senat_cr_culture"]["url"].endswith("/culture.html")
    assert by_id["senat_cr_lois"]["url"].endswith("/lois.html")
    assert by_id["senat_cr_finances"]["url"].endswith("/finances.html")
    assert by_id["senat_cr_etrangeres"]["url"].endswith("/affaires-etrangeres.html")


def test_yaml_r40c_max_new_et_body_max_chars():
    """Limites par source pour borner le coût HTTP + DB. Doivent être
    présentes sur les 3 nouvelles entrées comme sur l'historique."""
    for s in _load_senat_cr_sources():
        assert "max_new_per_run" in s, f"{s['id']} : max_new_per_run manquant"
        assert "body_max_chars" in s, f"{s['id']} : body_max_chars manquant"
        assert isinstance(s["max_new_per_run"], int) and s["max_new_per_run"] > 0
        assert isinstance(s["body_max_chars"], int) and s["body_max_chars"] >= 1000


def test_yaml_r40c_pas_de_disabled():
    by_id = {s["id"]: s for s in _load_senat_cr_sources()}
    for sid in ("senat_cr_lois", "senat_cr_finances", "senat_cr_etrangeres"):
        assert by_id[sid].get("enabled", True) is not False, (
            f"{sid} ne doit pas être disabled — R40-C veut activer.")


# ---------------------------------------------------------------------------
# 2. Régression : le scraper marche sans modif sur les nouveaux PO
# ---------------------------------------------------------------------------


_LISTING_HTML = (
    '<html><body>'
    '<h3 id=curses>'
    '<a class="link" href="/compte-rendu-commissions/20260413/{short}.html">'
    'Semaine du 13 avril 2026</a></h3>'
    '<h3 id=curses>'
    '<a class="link" href="/compte-rendu-commissions/20260406/{short}.html">'
    'Semaine du 6 avril 2026</a></h3>'
    '</body></html>'
)

_WEEK_HTML = (
    '<html><body><main>'
    '<nav>Voir le fil d\'Ariane Accueil Commissions Comptes rendus</nav>'
    '<h2>COMPTES RENDUS DE LA COMMISSION DES LOIS</h2>'
    '<p>Mardi 14 avril 2026 — Audition de M. Untel sur le sport pro et le dopage. '
    'Examen du PJL relatif à l\'intégrité des compétitions sportives.</p>'
    '</main></body></html>'
)


@pytest.mark.parametrize("sid,short,po,label", [
    ("senat_cr_lois", "lois", "PO211495", "Commission des lois Sénat"),
    ("senat_cr_finances", "fin", "PO211494", "Commission des finances Sénat"),
    ("senat_cr_etrangeres", "etr", "PO211491", "Commission affaires étrangères Sénat"),
])
def test_fetch_source_propage_organe_et_label_pour_chaque_commission(
        monkeypatch, sid, short, po, label):
    """Régression : le scraper R37-A reste générique, R40-C n'a pas
    eu besoin de patcher le code. Vérifie que `raw.organe`,
    `raw.commission` et `haystack_body` sont bien posés."""

    def _fake_fetch(url):
        if url.endswith(".html") and "/compte-rendu-commissions/" in url:
            if url.endswith(f"/{short}.html") and "20260413" not in url and "20260406" not in url:
                # listing
                return _LISTING_HTML.replace("{short}", short)
            if "20260413" in url or "20260406" in url:
                # CR hebdo
                return _WEEK_HTML
            return _LISTING_HTML.replace("{short}", short)
        return ""

    monkeypatch.setattr(mod, "fetch_text", _fake_fetch)
    items = mod.fetch_source({
        "id": sid, "category": "comptes_rendus",
        "url": f"https://www.senat.fr/compte-rendu-commissions/{short}.html",
        "commission_label": label, "commission_organe": po,
        "max_new_per_run": 5, "body_max_chars": 5000,
    })
    assert len(items) == 2  # 2 semaines listées
    for it in items:
        assert it.source_id == sid
        assert it.category == "comptes_rendus"
        assert it.chamber == "Senat"
        assert it.title.startswith("Semaine du")
        # Préfixe commission NON injecté dans title (R38-E)
        assert label not in it.title
        # Mais exposé en raw.commission pour template/affichage
        assert it.raw["commission"] == label
        assert it.raw["organe"] == po
        # haystack_body alimenté pour le matcher mots-clés
        body = it.raw.get("haystack_body", "")
        assert isinstance(body, str) and len(body) > 50
        assert "sport" in body.lower() or "dopage" in body.lower()
