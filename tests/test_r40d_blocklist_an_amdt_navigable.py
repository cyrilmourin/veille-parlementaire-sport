"""R40-D (2026-04-26) — Garde-fou blocklist : URL navigable AN amendements.

Bug initial (R39-O) : le HANDOFF documentait l'ajout de 2 amendements
PJL n°2632 en blocklist via leur URL navigable
`https://www.assemblee-nationale.fr/dyn/17/amendements/2632/CION-DVP/CD495`,
mais le pipeline stocke en DB la forme TECHNIQUE
`https://www.assemblee-nationale.fr/dyn/17/amendements/<UID_TECHNIQUE>`
(cf. `assemblee.py:_normalize_amendement` ligne 799 :
`url=f"https://www.assemblee-nationale.fr/dyn/17/amendements/{uid_for_url}"`
où `uid_for_url = uid_tech or num`).

Conséquence : les 2 entrées URL en blocklist n'ont jamais matché. Cyril
a constaté en prod le 2026-04-26 que les deux amendements étaient encore
visibles sur https://veille.sideline-conseil.fr/items/amendements/.

Fix R40-D :
1. Convertir les 2 entrées blocklist au format `uid: an_amendements::<UID>`
   (UID techniques récupérés via la page navigable AN → liens XML/JSON).
2. Garde-fou côté code : `_load_blocklist` détecte le pattern URL navigable
   `/amendements/<n>/CION-XXX/CD<n>` et logge un WARNING explicite +
   ignore l'entrée — au lieu de l'ajouter silencieusement à `blocked_urls`
   qui ne matchera jamais.
3. Doc en tête de `config/blocklist.yml` mise à jour pour expliquer le
   piège et recommander la forme `uid:`.
"""
from __future__ import annotations

import logging

from src.site_export import (
    _AN_AMDT_NAVIGABLE_RE,
    _filter_blocklist,
    _load_blocklist,
)


# ---------------------------------------------------------------------------
# 1. Détection du pattern URL navigable
# ---------------------------------------------------------------------------


def test_navigable_pattern_match_cion_dvp():
    """Forme navigable typique : /amendements/<TEXTE>/CION-<XXX>/<CD<NUM>>"""
    u = "https://www.assemblee-nationale.fr/dyn/17/amendements/2632/CION-DVP/CD495"
    assert _AN_AMDT_NAVIGABLE_RE.search(u) is not None


def test_navigable_pattern_match_autres_commissions():
    """Le pattern doit aussi matcher les autres préfixes commission AN."""
    for sigle in ("CION-LOIS", "CION-FIN", "CION-AFFETR", "CION-AFFCUL",
                  "CION-DEF"):
        u = f"https://www.assemblee-nationale.fr/dyn/17/amendements/1234/{sigle}/CD42"
        assert _AN_AMDT_NAVIGABLE_RE.search(u) is not None, f"{sigle} pas matché"


def test_navigable_pattern_no_match_uid_technique():
    """La forme TECHNIQUE (effectivement stockée en DB) ne doit PAS matcher."""
    u = ("https://www.assemblee-nationale.fr/dyn/17/amendements/"
         "AMANR5L17PO419865B2632P0D1N000495")
    assert _AN_AMDT_NAVIGABLE_RE.search(u) is None


def test_navigable_pattern_no_match_url_random():
    """Pas de faux positif sur une URL non-amendement."""
    for u in (
        "https://www.assemblee-nationale.fr/dyn/17/dossiers/jop_alpes_2030",
        "https://www.senat.fr/leg/pjl24-630.html",
        "https://example.com/foo/CION-DVP/bar",
    ):
        assert _AN_AMDT_NAVIGABLE_RE.search(u) is None, f"faux positif : {u}"


# ---------------------------------------------------------------------------
# 2. _load_blocklist ignore les URLs navigables et retourne les UIDs
# ---------------------------------------------------------------------------


def test_load_blocklist_lit_les_uids_du_yaml_actuel():
    """blocklist.yml actuel doit exposer les 2 amendements PJL n°2632 via
    UIDs techniques (pas via URL navigable, pour qu'ils soient effectivement
    filtrés)."""
    blocked_urls, blocked_uids = _load_blocklist()
    assert ("an_amendements::AMANR5L17PO419865B2632P0D1N000495"
            in blocked_uids)
    assert ("an_amendements::AMANR5L17PO419865B2632P0D1N000492"
            in blocked_uids)


def test_load_blocklist_url_navigable_ignoree_avec_warning(monkeypatch, tmp_path,
                                                            caplog):
    """Si quelqu'un remet une URL navigable en blocklist, elle doit être
    ignorée silencieusement (PAS ajoutée à `blocked_urls`) et un warning
    doit être loggé pour signaler le bug."""
    yml = tmp_path / "blocklist.yml"
    yml.write_text(
        "blocklist:\n"
        "  - url: https://www.assemblee-nationale.fr/dyn/17/amendements/2632/CION-DVP/CD495\n"
        "    reason: URL navigable (devrait être ignorée)\n"
        "  - url: https://www.senat.fr/leg/pjl24-630.html\n"
        "    reason: URL Sénat (devrait être conservée)\n",
        encoding="utf-8",
    )
    import src.site_export as mod
    monkeypatch.setattr(mod, "_BLOCKLIST_PATH", yml)

    with caplog.at_level(logging.WARNING):
        blocked_urls, blocked_uids = mod._load_blocklist()

    canon_navigable = ("www.assemblee-nationale.fr/dyn/17/amendements/"
                       "2632/cion-dvp/cd495")
    assert canon_navigable not in blocked_urls
    assert "www.senat.fr/leg/pjl24-630.html" in blocked_urls
    assert blocked_uids == set()
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "format navigable" in msgs
    assert "AMANR5L17" in msgs


def test_load_blocklist_uid_technique_passe_meme_si_url_navigable_skippee(
        monkeypatch, tmp_path):
    """Régression : un mix d'entrées (1 URL navigable buguée + 1 UID propre)
    doit retourner l'UID intact même si l'URL est skippée."""
    yml = tmp_path / "blocklist.yml"
    yml.write_text(
        "blocklist:\n"
        "  - url: https://www.assemblee-nationale.fr/dyn/17/amendements/2632/CION-DVP/CD495\n"
        "    reason: navigable\n"
        "  - uid: an_amendements::AMANR5L17PO419865B2632P0D1N000495\n"
        "    reason: technique\n",
        encoding="utf-8",
    )
    import src.site_export as mod
    monkeypatch.setattr(mod, "_BLOCKLIST_PATH", yml)
    blocked_urls, blocked_uids = mod._load_blocklist()
    assert blocked_urls == set()
    assert (blocked_uids
            == {"an_amendements::AMANR5L17PO419865B2632P0D1N000495"})


# ---------------------------------------------------------------------------
# 3. _filter_blocklist : régression bout-en-bout sur les rows DB
# ---------------------------------------------------------------------------


def _row(*, sid: str, uid: str, url: str, title: str = "Amdt test") -> dict:
    return {
        "source_id": sid,
        "uid": uid,
        "url": url,
        "title": title,
        "category": "amendements",
        "chamber": "AN",
        "raw": {},
    }


def test_filter_blocklist_drop_amdt_par_uid_technique(monkeypatch, tmp_path):
    """Bug réel : avec une entrée `uid:` correcte, l'amendement doit être
    filtré même si son URL en DB est la forme technique inconnue du yaml."""
    yml = tmp_path / "blocklist.yml"
    yml.write_text(
        "blocklist:\n"
        "  - uid: an_amendements::AMANR5L17PO419865B2632P0D1N000495\n"
        "    reason: faux positif\n",
        encoding="utf-8",
    )
    import src.site_export as mod
    monkeypatch.setattr(mod, "_BLOCKLIST_PATH", yml)

    rows = [
        _row(sid="an_amendements",
             uid="AMANR5L17PO419865B2632P0D1N000495",
             url="https://www.assemblee-nationale.fr/dyn/17/amendements/AMANR5L17PO419865B2632P0D1N000495",
             title="Amdt CD495 PJL agricoles (à exclure)"),
        _row(sid="an_amendements",
             uid="AMANR5L17PO419865B2632P0D1N000999",
             url="https://www.assemblee-nationale.fr/dyn/17/amendements/AMANR5L17PO419865B2632P0D1N000999",
             title="Amdt CD999 PJL agricoles (à conserver)"),
    ]
    out = mod._filter_blocklist(rows)
    titles = [r["title"] for r in out]
    assert "Amdt CD495 PJL agricoles (à exclure)" not in titles
    assert "Amdt CD999 PJL agricoles (à conserver)" in titles


def test_filter_blocklist_navigable_url_ne_filtre_rien(monkeypatch, tmp_path):
    """Régression du bug R39-O : une entrée URL au format navigable ne doit
    JAMAIS filtrer l'amendement correspondant en DB (parce qu'on aurait
    cru qu'elle filtre, alors qu'en réalité elle ne match jamais).
    Ce test verrouille le comportement attendu : l'entrée est ignorée et
    l'amendement passe → le mainteneur saura qu'il faut utiliser `uid:`."""
    yml = tmp_path / "blocklist.yml"
    yml.write_text(
        "blocklist:\n"
        "  - url: https://www.assemblee-nationale.fr/dyn/17/amendements/2632/CION-DVP/CD495\n"
        "    reason: forme navigable buguée\n",
        encoding="utf-8",
    )
    import src.site_export as mod
    monkeypatch.setattr(mod, "_BLOCKLIST_PATH", yml)

    rows = [
        _row(sid="an_amendements",
             uid="AMANR5L17PO419865B2632P0D1N000495",
             url="https://www.assemblee-nationale.fr/dyn/17/amendements/AMANR5L17PO419865B2632P0D1N000495",
             title="Amdt CD495 toujours visible"),
    ]
    out = mod._filter_blocklist(rows)
    assert len(out) == 1
    assert out[0]["title"] == "Amdt CD495 toujours visible"
