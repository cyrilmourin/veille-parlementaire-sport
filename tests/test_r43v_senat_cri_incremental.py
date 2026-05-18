"""Tests R43-V (2026-05-18) — `senat_cri` / `senat_debats` incrémental.

Constat performance (revue des logs des 15 derniers runs daily, 18/05) :
`senat_cri` prend 8-12 min sur les 20-25 min de pipeline → de loin le
poste dominant. Cause : décodage + regex strip HTML + html.unescape +
extract_cr_theme sur 2810 fichiers HTML/XML à chaque run, alors que la
quasi-totalité de ces fichiers est déjà en DB (UID déterministe).

Fix : state file `data/<sid>_state.json` qui mémorise les `name` (zip
member path) déjà parsés. Au run N, on skip le décodage pour les `name`
connus et on retourne uniquement les NEW items au matcher.

Comportement attendu :
1. Premier run (state vide) → tout est parsé, état sauvegardé
2. Run N (state contient les 2810 noms d'hier) → seuls les nouveaux
   (~1-10/jour) sont parsés, retour = liste réduite, mais le state se
   renouvelle avec tous les noms vus pendant CE run
3. État se renouvelle automatiquement : noms sortis de la fenêtre 15j
   disparaissent du nouveau snapshot (auto-éviction)
4. RUN_MODE=full → state ignoré, re-parse complet (cas reset)

Gain attendu : ~5-8 min/run en régime nominal (1-10 nouveaux à décoder
vs 2810). Le téléchargement du zip (537 Mo) reste, on n'évite que le
décodage des entrées déjà connues.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest


def _make_zip_in_memory(entries: list[tuple[str, datetime, bytes]]) -> bytes:
    """Helper : crée un ZIP en mémoire avec des entrées (name, date, content)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, dt, content in entries:
            zi = zipfile.ZipInfo(
                filename=name,
                date_time=(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second),
            )
            zf.writestr(zi, content)
    return buf.getvalue()


def _make_cri_entry(date_str: str, suffix: str = "") -> tuple[str, datetime, bytes]:
    """Helper : crée une entrée zip ressemblant à un CRI Sénat."""
    dt = datetime.strptime(date_str, "%Y%m%d")
    name = f"cri/d{date_str}{suffix}.html"
    content = b"<p>Compte rendu test sport</p>"
    return name, dt, content


@pytest.fixture
def offline_zip(monkeypatch):
    """Mock `fetch_bytes_heavy` pour servir un ZIP local au handler."""
    from src.sources import senat

    # 3 entrées récentes dans la fenêtre 15j (datées de J-1, J-2, J-3)
    today = datetime.utcnow()
    entries = [
        _make_cri_entry((today - timedelta(days=1)).strftime("%Y%m%d"), "a"),
        _make_cri_entry((today - timedelta(days=2)).strftime("%Y%m%d"), "b"),
        _make_cri_entry((today - timedelta(days=3)).strftime("%Y%m%d"), "c"),
    ]
    zip_bytes = _make_zip_in_memory(entries)

    def fake_fetch(url, **kwargs):
        return zip_bytes

    monkeypatch.setattr(senat, "fetch_bytes_heavy", fake_fetch)
    return entries


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_r43v_first_run_parses_all_and_writes_state(
    in_tmp_cwd, offline_zip, monkeypatch
):
    """Premier run : state vide → toutes les entrées sont parsées et le
    state est écrit avec tous les noms vus."""
    from src.sources import senat

    # RUN_MODE nominal (15j window)
    monkeypatch.delenv("RUN_MODE", raising=False)
    src = {"id": "senat_cri", "category": "comptes_rendus",
           "url": "https://example/cri.zip"}
    items = senat._fetch_debats_zip(src)
    # 3 entrées → 3 items
    assert len(items) == 3, f"Attendu 3 items, obtenu {len(items)}"
    # State écrit
    state_path = Path("data/senat_cri_state.json")
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["schema_version"] == 1
    assert state["source_id"] == "senat_cri"
    assert state["count"] == 3
    assert len(state["processed_members"]) == 3
    # Les 3 noms sont bien dans le state
    for name, _, _ in offline_zip:
        assert name in state["processed_members"]


def test_r43v_second_run_skips_known_members(
    in_tmp_cwd, offline_zip, monkeypatch
):
    """Run N : 2 des 3 noms sont dans le state → seuls les NEW
    (1 entrée) sont parsés. Le state se renouvelle avec les 3 noms vus."""
    from src.sources import senat

    monkeypatch.delenv("RUN_MODE", raising=False)
    # Pré-existe : 2 sur 3 déjà processed
    state_path = Path("data/senat_cri_state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "source_id": "senat_cri",
        "processed_members": [
            offline_zip[0][0],  # déjà connu
            offline_zip[1][0],  # déjà connu
        ],
    }))
    src = {"id": "senat_cri", "category": "comptes_rendus",
           "url": "https://example/cri.zip"}
    items = senat._fetch_debats_zip(src)
    # Seul le 3e (offline_zip[2]) est NEW
    assert len(items) == 1, f"Attendu 1 item NEW, obtenu {len(items)}"
    # State renouvelé : les 3 noms vus ce run
    state = json.loads(state_path.read_text())
    assert state["count"] == 3
    assert sorted(state["processed_members"]) == sorted(
        [e[0] for e in offline_zip]
    )


def test_r43v_full_mode_ignores_state(
    in_tmp_cwd, offline_zip, monkeypatch
):
    """RUN_MODE=full doit forcer un re-parse complet en ignorant le state.
    Garantit que `scripts/reset_category.py --yes` puis un run produira
    bien les items, même si le state pré-existait."""
    from src.sources import senat

    monkeypatch.setenv("RUN_MODE", "full")
    # State avec les 3 noms déjà connus
    state_path = Path("data/senat_cri_state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "source_id": "senat_cri",
        "processed_members": [e[0] for e in offline_zip],
    }))
    src = {"id": "senat_cri", "category": "comptes_rendus",
           "url": "https://example/cri.zip"}
    items = senat._fetch_debats_zip(src)
    # En full mode, state ignoré → tous re-parsés
    assert len(items) == 3, f"En RUN_MODE=full, attendu 3 items, obtenu {len(items)}"


def test_r43v_state_renews_outside_window(
    in_tmp_cwd, monkeypatch
):
    """Le state se renouvelle à chaque run : un nom vu pendant un run
    précédent mais sorti de la fenêtre 15j n'apparaît plus dans le
    nouveau snapshot (auto-éviction). Garantit que le state ne grossit
    pas indéfiniment."""
    from src.sources import senat

    monkeypatch.delenv("RUN_MODE", raising=False)
    today = datetime.utcnow()
    # ZIP avec 1 entrée récente + 1 entrée TRÈS ancienne (hors fenêtre 15j)
    entries = [
        _make_cri_entry((today - timedelta(days=2)).strftime("%Y%m%d"), "x"),
        _make_cri_entry((today - timedelta(days=60)).strftime("%Y%m%d"), "y"),
    ]
    zip_bytes = _make_zip_in_memory(entries)
    monkeypatch.setattr(senat, "fetch_bytes_heavy", lambda url, **k: zip_bytes)
    # State pré-existant avec un vieux nom (hors fenêtre actuelle)
    state_path = Path("data/senat_cri_state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "source_id": "senat_cri",
        "processed_members": ["cri/d20240101x.html"],  # très vieux
    }))
    src = {"id": "senat_cri", "category": "comptes_rendus",
           "url": "https://example/cri.zip"}
    senat._fetch_debats_zip(src)
    # Nouveau state : ne contient que le nom vu pendant CE run (fenêtre 15j)
    state = json.loads(state_path.read_text())
    # Le vieux nom n'est plus dans le state (n'a pas été rencontré ce run)
    assert "cri/d20240101x.html" not in state["processed_members"]
    # Le récent l'est
    assert entries[0][0] in state["processed_members"]
    # Le très vieux (hors fenêtre 15j) n'a même pas été vu → pas dans state
    assert entries[1][0] not in state["processed_members"]


def test_r43v_state_corrompu_redemarre_a_zero(
    in_tmp_cwd, offline_zip, monkeypatch
):
    """State JSON illisible → on traite comme un reset (re-parse tout
    au prochain run), pas de crash."""
    from src.sources import senat

    monkeypatch.delenv("RUN_MODE", raising=False)
    state_path = Path("data/senat_cri_state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("not valid json{{")
    src = {"id": "senat_cri", "category": "comptes_rendus",
           "url": "https://example/cri.zip"}
    items = senat._fetch_debats_zip(src)
    # State corrompu = reset → 3 items parsés
    assert len(items) == 3
    # Le state est réécrit proprement
    state = json.loads(state_path.read_text())
    assert state["count"] == 3


def test_r43v_reset_category_purges_senat_state_files(tmp_path, monkeypatch):
    """Garde-fou : `scripts/reset_category.py comptes_rendus` doit purger
    aussi `senat_cri_state.json` et `senat_debats_state.json`.
    Test directement la fonction interne `_purge_incremental_state` car
    la commande complète touche la DB."""
    import importlib.util
    import sys as _sys

    monkeypatch.chdir(tmp_path)
    # Crée les 3 state files
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "an_cr_state.json").write_text("{}")
    (data_dir / "senat_cri_state.json").write_text("{}")
    (data_dir / "senat_debats_state.json").write_text("{}")
    # Importe le module reset_category en patchant ROOT
    spec = importlib.util.spec_from_file_location(
        "reset_category_mod",
        Path(__file__).parent.parent / "scripts" / "reset_category.py",
    )
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["reset_category_mod"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    # Purge globale comptes_rendus (sans source_id)
    mod._purge_incremental_state("comptes_rendus", None)
    # Les 3 state files doivent être supprimés
    assert not (data_dir / "an_cr_state.json").exists()
    assert not (data_dir / "senat_cri_state.json").exists()
    assert not (data_dir / "senat_debats_state.json").exists()
