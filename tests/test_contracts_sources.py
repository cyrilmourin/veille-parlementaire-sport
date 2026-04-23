"""R31 (2026-04-24) — Contrats de source (shape tests, zéro réseau).

Audit §4.1 : chaque connecteur de source produit des Items pydantic
qui doivent respecter un contrat de forme strict. Ce fichier alimente
les parsers avec des payloads factices représentatifs et vérifie que
les Items produits satisfont aux invariants :

- `source_id` non vide et égal au `id` de la source YAML
- `uid` non vide (sinon dédup par hash_key échoue silencieusement)
- `category` ∈ Literal Category (pydantic le valide déjà, mais on s'en
  assure au niveau des formats exotiques)
- `chamber` ∈ ensemble connu ou None (pas de chaîne aléatoire — le
  template de page utilise `chamber` pour le logo)
- `published_at` est un datetime naïf (convention du projet depuis
  R11f — tout l'arithmétique de fenêtre est en UTC naïf)
- `title` non vide

Objectif : si un parseur livre un Item dégénéré (uid manquant, tz-aware,
title vide), on le sait avant la prod plutôt qu'après le signalement.

R22c et R22d auraient été détectés dès la CI suivante (sources tombées
à 0 items par refonte d'URL) si ces shape tests existaient.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.models import Item
from src.sources import html_generic, senat


# ---------------------------------------------------------------------------
# Helpers : invariants du contrat Item
# ---------------------------------------------------------------------------


# Ensemble des chambres autorisées (tolérant : on accepte `None` pour les
# sources non-institutionnelles). Cette liste doit être élargie ici en
# même temps qu'un connecteur l'émet.
ALLOWED_CHAMBERS = {
    None, "",
    "AN", "Senat",
    "Elysee", "Matignon",
    "MinSports", "MinArmees", "MinInterieur", "MinJustice",
    "MinEduc", "MinESR", "MinSante", "MinTravail", "MinEco",
    "MinEnergie", "MinCulture", "MinAffetr", "MEAE", "MinAgri",
    "MinEnvironnement", "MinCollectivites", "MinOutreMer",
    "MinFemmes", "MinVille", "MinLogement", "MinTransports",
    "JORF",
    "ANS", "INSEP", "INJEP", "AFLD",
    "CNOSF", "FranceParalympique", "FDSF",
    "ARCOM", "ANJ", "HATVP", "CADA", "HCERES",
    "ConseilEtat", "ConseilConstitutionnel", "CourComptes",
    "AutoriteConcurrence", "CourCassation",
    "IGESR",
}

KNOWN_CATEGORIES = {
    "dossiers_legislatifs", "jorf", "amendements", "questions",
    "comptes_rendus", "publications", "nominations", "agenda",
    "communiques",
}


def assert_item_contract(item: Item, *, src_id: str | None = None) -> None:
    """Valide tous les invariants du contrat Item pour un parseur.

    Séparée dans une fonction pour être ré-utilisée à chaque nouveau
    test de format (coût nul, lisibilité).
    """
    # Identité
    assert item.source_id, "source_id doit être non vide"
    if src_id is not None:
        assert item.source_id == src_id, (
            f"source_id mismatch : attendu {src_id!r}, vu {item.source_id!r}"
        )
    assert item.uid, f"uid doit être non vide (source {item.source_id})"
    # Classement
    assert item.category in KNOWN_CATEGORIES, (
        f"category inconnue : {item.category!r} (source {item.source_id})"
    )
    assert item.chamber in ALLOWED_CHAMBERS, (
        f"chamber inconnue : {item.chamber!r} (source {item.source_id}) — "
        "si c'est volontaire, ajouter à ALLOWED_CHAMBERS"
    )
    # Contenu
    assert item.title, f"title vide pour {item.source_id}::{item.uid}"
    assert isinstance(item.title, str)
    assert len(item.title) <= 500, "title anormalement long (> 500 chars)"
    # URL peut être vide sur certains formats (ex. CSV Sénat sans URL directe)
    # mais si présente, doit être HTTP(S).
    if item.url:
        assert item.url.startswith(("http://", "https://")), (
            f"URL non-HTTP(S) : {item.url!r} (source {item.source_id})"
        )
    # Convention naïve UTC du projet (R11f). tz-aware casse `_parse_dt` à
    # l'export et bug régulièrement l'agenda.
    if item.published_at is not None:
        assert isinstance(item.published_at, datetime)
        assert item.published_at.tzinfo is None, (
            f"published_at tz-aware interdit : {item.published_at!r} "
            f"(source {item.source_id}) — convention naïf UTC R11f"
        )
    # raw doit rester un dict (jamais None), sinon `raw.get(...)` plante
    # dans les fixups export.
    assert isinstance(item.raw, dict)


# ---------------------------------------------------------------------------
# Contrat : parser RSS Sénat (_normalize_rss)
# ---------------------------------------------------------------------------


def test_contract_senat_rss_minimal():
    """RSS Sénat minimal → Item au contrat respecté (source_id, uid, chamber)."""
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<rss version="2.0"><channel><title>T</title>'
        b'<item><guid>http://senat.fr/leg/pjl25-733.html</guid>'
        b'<link>http://senat.fr/leg/pjl25-733.html</link>'
        b'<title>Projet de loi sport</title>'
        b'<pubDate>Mon, 21 Apr 2026 09:00:00 GMT</pubDate>'
        b'<description>Un resume.</description>'
        b'</item></channel></rss>'
    )
    src = {"id": "senat_theme_sport_rss", "category": "dossiers_legislatifs"}
    items = senat._normalize_rss(src, xml)
    assert len(items) == 1
    assert_item_contract(items[0], src_id="senat_theme_sport_rss")
    assert items[0].chamber == "Senat"
    # pubDate doit être décodée en datetime naïf
    assert items[0].published_at == datetime(2026, 4, 21, 9, 0, 0)


def test_contract_senat_rss_rejects_item_without_guid_or_link():
    """Garde-fou : un `<item>` sans `<guid>` ni `<link>` doit être filtré
    (sinon uid vide → hash_key collision → dédup cassée)."""
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<rss version="2.0"><channel><title>T</title>'
        b'<item><title>Sans identifiant</title></item>'
        b'</channel></rss>'
    )
    src = {"id": "senat_rss", "category": "communiques"}
    items = senat._normalize_rss(src, xml)
    assert items == []


# ---------------------------------------------------------------------------
# Contrat : parser html_generic (fetch_source via monkeypatch)
# ---------------------------------------------------------------------------


def test_contract_html_generic_minimal_listing(monkeypatch):
    """html_generic parse un listing HTML minimal → Items au contrat."""
    html_page = """
    <html><head><title>Test</title></head>
    <body>
      <ul>
        <li>
          <a href="/communique-1.html">Titre du communiqué</a>
          <time datetime="2026-04-15">15/04/2026</time>
        </li>
      </ul>
    </body></html>
    """.encode("utf-8")

    def fake_fetch(url, **_kw):
        return html_page

    # Monkeypatch du fetch pour éviter le réseau
    monkeypatch.setattr(html_generic, "fetch_bytes", fake_fetch)

    src = {
        "id": "anj_communiques",
        "category": "communiques",
        "url": "https://anj.fr/communiques-de-presse/",
        "url_pattern": "/communique",
    }
    items = html_generic.fetch_source(src)
    assert items, "fetch_source doit produire au moins 1 item"
    for it in items:
        assert_item_contract(it, src_id="anj_communiques")


def test_contract_html_generic_tolerates_empty_html(monkeypatch):
    """Page HTML vide ou dépourvue de liens → 0 items, pas de crash."""
    monkeypatch.setattr(
        html_generic, "fetch_bytes",
        lambda url, **_kw: b"<html><body></body></html>",
    )
    src = {
        "id": "random_empty",
        "category": "communiques",
        "url": "https://example.fr/",
    }
    items = html_generic.fetch_source(src)
    assert items == []


# ---------------------------------------------------------------------------
# Contrat : invariants par construction d'Item pydantic
# ---------------------------------------------------------------------------


def test_contract_item_pydantic_rejects_unknown_category():
    """Pydantic v2 doit rejeter une category hors Literal (filet de sécu
    au cas où un parseur tenterait d'inventer une catégorie)."""
    with pytest.raises(Exception):
        Item(
            source_id="foo", uid="1",
            category="inexistante",  # type: ignore[arg-type]
            title="T", url="https://x",
        )


def test_contract_item_accepts_tz_aware_but_we_forbid_it():
    """Pydantic laisse passer un datetime tz-aware — c'est notre contrat
    qui l'interdit (convention R11f). Ce test documente la séparation :
    le modèle tolère, mais `assert_item_contract` rejette."""
    import datetime as dt
    # Ceci passe pydantic
    tz_item = Item(
        source_id="x", uid="1", category="communiques",
        title="T", url="https://x",
        published_at=dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc),
    )
    # Mais notre contrat métier le rejette
    with pytest.raises(AssertionError, match="tz-aware"):
        assert_item_contract(tz_item, src_id="x")


def test_contract_hash_key_uses_source_id_and_uid():
    """`hash_key` est la clé de dédup en DB. Si `uid` vide, toute la
    source collisionne sur un seul bucket → perte de données silencieuse."""
    it = Item(
        source_id="senat_questions", uid="1054S",
        category="questions", title="Q", url="",
    )
    assert it.hash_key == "senat_questions::1054S"
    # Deux items même uid / même source → même hash_key (dédup marche)
    it2 = Item(
        source_id="senat_questions", uid="1054S",
        category="questions", title="Q2", url="",
    )
    assert it.hash_key == it2.hash_key
    # Uid différent → hash différent
    it3 = Item(
        source_id="senat_questions", uid="1055S",
        category="questions", title="Q3", url="",
    )
    assert it.hash_key != it3.hash_key


# ---------------------------------------------------------------------------
# Contrat agrégé : les ALLOWED_CHAMBERS couvrent tous les chamber émis
# par les parseurs (via grep source, vérifié au runtime)
# ---------------------------------------------------------------------------


def test_contract_chambers_allowed_list_is_complete():
    """Sanity check : les chambers effectivement émises par les parsers
    doivent toutes être dans ALLOWED_CHAMBERS. Ce test échouerait si on
    ajoutait un connecteur avec une nouvelle valeur sans la déclarer ici
    (rappel explicite pour l'ajouter)."""
    import re
    from pathlib import Path
    src_dir = Path(__file__).resolve().parents[1] / "src" / "sources"
    emitted = set()
    pat = re.compile(r'chamber\s*=\s*["\']([^"\']+)["\']')
    for py in src_dir.glob("*.py"):
        txt = py.read_text(encoding="utf-8", errors="ignore")
        for m in pat.finditer(txt):
            emitted.add(m.group(1))
    missing = emitted - ALLOWED_CHAMBERS
    assert not missing, (
        f"Nouveaux chambers émis non déclarés dans ALLOWED_CHAMBERS : {missing} — "
        "ajouter au set en tête de fichier"
    )
