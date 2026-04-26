"""R40-B (2026-04-26) — Élargissement des agendas commissions Sénat.

Ajout des 3 commissions permanentes Sénat à la couverture R35-E (qui
ne couvrait que la commission culture/éducation/communication/sport) :

- Commission des lois (PO211495) — éthique, dopage, intégrité sport pro,
  voile sportif, encadrement supporters.
- Commission des finances (PO211494) — budget MinSports, financement
  ANS, fiscalité sport, paris sportifs.
- Commission affaires étrangères, défense et forces armées (PO211491) —
  JO/JP comme outil diplomatique, géopolitique sportive, sport militaire,
  accueil grands événements.

Le bypass organe R27 étant désactivé depuis R39-K, les agendas de ces
commissions ne remontent que sur match keyword métier — pas de risque
de bruit type « commission affaires sociales » (R35-D).

Affaires sociales reste exclue (cf. R35-D, >90% off-topic).
Affaires économiques + Aménagement du territoire écartées de R40-B
(faible recoupement avec sport, à reconsidérer si angle mort observé).

Slugs URL non-standards documentés ici car le pipeline ne le devine pas :
- lois     : .../commission-des-lois/agenda-de-la-commission-1.html
- aff.étr. : .../commission-des-affaires-etrangeres-de-la-defense-et-des-forces-armees/agenda-de-la-commission-des-affaires-etrangeres.html
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from src.sources import senat_commission_agenda as mod


SOURCES_YAML = Path(__file__).resolve().parent.parent / "config" / "sources.yml"


def _load_senat_agenda_sources() -> list[dict]:
    """Récupère toutes les entrées sources de format senat_commission_agenda_html.
    YAML est structuré `<group>: {sources: [...]}` au top level."""
    with SOURCES_YAML.open() as f:
        cfg = yaml.safe_load(f)
    out: list[dict] = []
    for grp_val in cfg.values():
        if not isinstance(grp_val, dict):
            continue
        for s in grp_val.get("sources", []) or []:
            if isinstance(s, dict) and s.get("format") == "senat_commission_agenda_html":
                out.append(s)
    return out


# ---------------------------------------------------------------------------
# 1. Déclarations YAML
# ---------------------------------------------------------------------------


def test_yaml_5_sources_senat_commission_agenda():
    """1 R35-E + 3 R40-B + 1 R40-E = 5 sources actives.
    R40-E (2026-04-26) a réintégré la commission affaires sociales
    (PO211493) après que R39-K ait désactivé le bypass organe R27 — le
    blocage R35-D ne se justifiait plus."""
    sources = _load_senat_agenda_sources()
    assert len(sources) == 5


def test_yaml_r40b_three_new_ids_present():
    ids = {s["id"] for s in _load_senat_agenda_sources()}
    assert "senat_agenda_lois" in ids
    assert "senat_agenda_finances" in ids
    assert "senat_agenda_etrangeres" in ids
    # R35-E historique conservé
    assert "senat_agenda_culture" in ids


def test_yaml_r40b_codes_organes_distincts():
    """Chaque source porte un code PO unique correspondant à sa commission."""
    by_id = {s["id"]: s for s in _load_senat_agenda_sources()}
    assert by_id["senat_agenda_culture"]["commission_organe"] == "PO211490"
    assert by_id["senat_agenda_lois"]["commission_organe"] == "PO211495"
    assert by_id["senat_agenda_finances"]["commission_organe"] == "PO211494"
    assert by_id["senat_agenda_etrangeres"]["commission_organe"] == "PO211491"
    # Sanity : pas de doublon entre les 4 codes
    codes = [s["commission_organe"] for s in by_id.values()]
    assert len(codes) == len(set(codes))


def test_yaml_r40e_affaires_sociales_reintegree():
    """R40-E (2026-04-26) — réintégration de la commission affaires
    sociales (PO211493) après que R39-K ait désactivé le bypass organe
    R27. Le blocage R35-D (>90 % off-topic) ne se justifiait plus puisque
    désormais SEUL le matching keyword décide ce qui remonte. Ce test
    remplace l'ancien `test_yaml_r40b_no_affaires_sociales` qui durcissait
    R35-D — supprimé en R40-E pour permettre la réintégration."""
    by_id = {s["id"]: s for s in _load_senat_agenda_sources()}
    assert "senat_agenda_affaires_sociales" in by_id
    assert by_id["senat_agenda_affaires_sociales"]["commission_organe"] == "PO211493"
    label_low = by_id["senat_agenda_affaires_sociales"]["commission_label"].lower()
    assert "affaires sociales" in label_low


def test_yaml_r40b_urls_pointent_vers_bonne_commission():
    """Slugs URL cohérents avec la commission. Capture les fautes de
    copier-coller entre lignes YAML très similaires."""
    by_id = {s["id"]: s for s in _load_senat_agenda_sources()}
    assert "commission-des-lois" in by_id["senat_agenda_lois"]["url"]
    assert "commission-des-finances" in by_id["senat_agenda_finances"]["url"]
    assert ("commission-des-affaires-etrangeres-de-la-defense-et-des-forces-armees"
            in by_id["senat_agenda_etrangeres"]["url"])
    assert ("commission-de-la-culture-de-leducation-et-de-la-communication"
            in by_id["senat_agenda_culture"]["url"])


def test_yaml_r40b_urls_agenda_specifiques():
    """Lois et affaires étrangères ont des slugs agenda non standards
    (≠ `agenda-de-la-commission.html`). Faute de slug = 404."""
    by_id = {s["id"]: s for s in _load_senat_agenda_sources()}
    assert by_id["senat_agenda_lois"]["url"].endswith(
        "/agenda-de-la-commission-1.html")
    assert by_id["senat_agenda_etrangeres"]["url"].endswith(
        "/agenda-de-la-commission-des-affaires-etrangeres.html")
    # Standard pour culture et finances
    assert by_id["senat_agenda_culture"]["url"].endswith(
        "/agenda-de-la-commission.html")
    assert by_id["senat_agenda_finances"]["url"].endswith(
        "/agenda-de-la-commission.html")


def test_yaml_r40b_pas_de_disabled():
    """Les 3 nouvelles sources sont actives par défaut. R35-E n'utilise
    pas de flag enabled donc l'absence vaut True — on vérifie que
    personne n'a ajouté `enabled: false` par mégarde."""
    by_id = {s["id"]: s for s in _load_senat_agenda_sources()}
    for sid in ("senat_agenda_lois", "senat_agenda_finances",
                "senat_agenda_etrangeres"):
        assert by_id[sid].get("enabled", True) is not False, (
            f"{sid} ne doit pas être disabled — R40-B veut activer.")


# ---------------------------------------------------------------------------
# 2. Régression : le scraper marche sans modif sur les nouveaux PO
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


def _html_with_events(*lis: str) -> str:
    return (
        '<html><body>'
        '<h3 class="mt-md-1 mt-lg-2">Prochaines réunions</h3>'
        '<ul class="list-group list-group-flush">'
        + "".join(lis)
        + '</ul></body></html>'
    )


@pytest.mark.parametrize("sid,po,label", [
    ("senat_agenda_lois", "PO211495", "Commission des lois Sénat"),
    ("senat_agenda_finances", "PO211494", "Commission des finances Sénat"),
    ("senat_agenda_etrangeres", "PO211491", "Commission affaires étrangères Sénat"),
])
def test_fetch_source_propage_organe_et_label_pour_chaque_commission(
        monkeypatch, sid, po, label):
    """Régression : le scraper R35-E reste générique — il consomme
    `commission_label` / `commission_organe` du dict source et les
    réinjecte dans `Item.title` et `Item.raw['organe']`. Garantit que
    R40-B n'a pas eu besoin de patcher le scraper."""
    html = _html_with_events(_li(
        day=28, month="avril",
        title="Audition test",
        salle="Salle Y",
        heure="9:00",
    ))
    monkeypatch.setattr(mod, "fetch_text", lambda url: html)
    import datetime as _dt
    real_datetime = _dt.datetime
    class _FrozenNow(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 4, 24, 10, 0)
    monkeypatch.setattr(mod, "datetime", _FrozenNow)

    items = mod.fetch_source({
        "id": sid,
        "url": "https://example.test/",
        "category": "agenda",
        "commission_label": label,
        "commission_organe": po,
    })
    assert len(items) == 1
    it = items[0]
    assert it.source_id == sid
    assert it.title.startswith(f"{label} — ")
    assert "Audition test" in it.title
    assert it.raw["organe"] == po
    assert it.raw["commission"] == label
    assert it.chamber == "Senat"
