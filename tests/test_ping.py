"""Tests end-to-end du mode ping (R24).

Stratégie : on monte une vraie DB SQLite temporaire via src.store.Store,
on y insère des items via Item+upsert_many (pour coller au vrai format de
matched_keywords JSON-string), puis on appelle ping.run_ping avec un
send_email_fn mocké qui capture (html, subject, to) sans toucher au SMTP.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src import ping, ping_state
from src.models import Item
from src.store import Store


# ---------- Fixtures ----------

@pytest.fixture
def tmp_db(tmp_path):
    """DB SQLite vide, chemin prêt à être passé à ping.run_ping."""
    return tmp_path / "veille.sqlite3"


@pytest.fixture
def tmp_state(tmp_path):
    """Chemin state.json inexistant au départ (baseline vide)."""
    return tmp_path / "ping_state.json"


class FakeMailer:
    """send_email_fn mockable. Enregistre chaque appel et renvoie `ok`."""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, html: str, subject: str, to: str) -> bool:
        self.calls.append((html, subject, to))
        return self.ok


def _make_item(
    source_id: str,
    uid: str,
    category: str,
    title: str,
    *,
    matched: list[str] | None = None,
    chamber: str | None = None,
    url: str = "",
) -> Item:
    return Item(
        source_id=source_id,
        uid=uid,
        category=category,
        chamber=chamber,
        title=title,
        url=url or f"https://example.test/{source_id}/{uid}",
        summary="",
        matched_keywords=matched if matched is not None else ["sport"],
    )


def _seed(db_path: Path, items: list[Item]) -> None:
    """Insère des items dans la DB via upsert_many (garantit le vrai format)."""
    store = Store(db_path)
    store.upsert_many(items)
    store.close()


def _write_state(path: Path, pinged: dict[str, list[str]]) -> None:
    ping_state.save(path, last_run_at=datetime(2026, 4, 23, 4, 0, tzinfo=timezone.utc),
                    pinged_uids=pinged)


# ---------- DB absente ----------

def test_ping_returns_10_when_db_missing(tmp_path, tmp_state):
    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_path / "absent.sqlite3",
        state_path=tmp_state,
        site_url="https://site.test",
        to="me@test",
        send_email_fn=mailer,
    )
    assert code == 10
    assert mailer.calls == []


# ---------- Cas 1 : pas de nouveautés ----------

def test_ping_silent_when_no_new_items(tmp_db, tmp_state):
    """DB contient 2 items matchés, state connaît déjà les 2 → silence."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Amendement sport 1"),
        _make_item("an_am", "AM2", "amendements", "Amendement sport 2"),
    ])
    _write_state(tmp_state, {"amendements": ["an_am::AM1", "an_am::AM2"]})

    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 0
    assert mailer.calls == []


def test_ping_silent_when_db_empty(tmp_db, tmp_state):
    """DB vide mais existe, state vide → silence."""
    Store(tmp_db).close()
    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 0
    assert mailer.calls == []


def test_ping_silent_when_only_unmatched_items(tmp_db, tmp_state):
    """DB contient des items non-matchés → silence."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Hors périmètre", matched=[]),
    ])
    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 0
    assert mailer.calls == []


# ---------- Cas 2 : nouveautés dans 1 catégorie ----------

def test_ping_sends_email_when_new_item_appears(tmp_db, tmp_state):
    """State connaît AM1 ; DB contient AM1 + AM2 → notification pour AM2."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Ancien amendement", chamber="AN"),
        _make_item("an_am", "AM2", "amendements", "NOUVEAU amendement", chamber="AN"),
    ])
    _write_state(tmp_state, {"amendements": ["an_am::AM1"]})

    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 0
    assert len(mailer.calls) == 1
    html, subject, to = mailer.calls[0]
    assert to == "me@test"
    assert "NOUVEAU amendement" in html
    assert "Ancien amendement" not in html
    assert "nouvelle occurrence" in subject.lower() or "occurrence" in subject.lower()


def test_ping_subject_contains_item_count(tmp_db, tmp_state):
    """Sujet de l'email doit annoncer le nombre d'items neufs."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "A", chamber="AN"),
        _make_item("an_am", "AM2", "amendements", "B", chamber="AN"),
        _make_item("an_am", "AM3", "amendements", "C", chamber="AN"),
    ])
    _write_state(tmp_state, {"amendements": []})
    mailer = FakeMailer()
    ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert len(mailer.calls) == 1
    _, subject, _ = mailer.calls[0]
    assert "3" in subject


# ---------- Cas 3 : nouveautés multi-catégories ----------

def test_ping_multi_category_renders_each_bucket(tmp_db, tmp_state):
    """Nouveautés dans 3 catégories → chaque section apparaît dans l'email."""
    _seed(tmp_db, [
        _make_item("an_dossier", "DL1", "dossiers_legislatifs",
                   "Dossier foot amateur", chamber="AN"),
        _make_item("an_am", "AM1", "amendements",
                   "Amdt sport outre-mer", chamber="AN"),
        _make_item("senat_questions", "Q1", "questions",
                   "Question JO Paris 2024", chamber="Sénat"),
    ])
    # Baseline vide → tout est neuf.
    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 0
    html = mailer.calls[0][0]
    assert "Dossier foot amateur" in html
    assert "Amdt sport outre-mer" in html
    assert "Question JO Paris 2024" in html
    assert "Dossiers législatifs" in html
    assert "Amendements" in html
    assert "Questions" in html


def test_ping_filters_non_priority_categories(tmp_db, tmp_state):
    """Un nouvel item dans `agenda` ou `jorf` ne doit PAS déclencher d'email."""
    _seed(tmp_db, [
        _make_item("an_agenda", "AG1", "agenda", "Agenda sport", chamber="AN"),
        _make_item("jorf", "J1", "jorf", "JORF sport", chamber=None),
        _make_item("communiques", "C1", "communiques", "Publi sport", chamber=None),
        _make_item("nominations", "N1", "nominations", "Nomination", chamber=None),
    ])
    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 0
    assert mailer.calls == []


def test_ping_mixes_priority_and_non_priority_only_sends_priority(tmp_db, tmp_state):
    """Mix : nouvel item dans `agenda` (ignoré) + `amendements` (notifié)."""
    _seed(tmp_db, [
        _make_item("an_agenda", "AG1", "agenda", "Ignoré", chamber="AN"),
        _make_item("an_am", "AM1", "amendements", "Notifié", chamber="AN"),
    ])
    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 0
    html = mailer.calls[0][0]
    assert "Notifié" in html
    assert "Ignoré" not in html


# ---------- Cas 4 : MAJ ping_state après envoi ----------

def test_ping_updates_state_after_successful_send(tmp_db, tmp_state):
    """Après envoi, les nouveaux UIDs sont mergés dans pinged_uids."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Déjà connu", chamber="AN"),
        _make_item("an_am", "AM2", "amendements", "Nouveau", chamber="AN"),
    ])
    _write_state(tmp_state, {"amendements": ["an_am::AM1"]})

    mailer = FakeMailer(ok=True)
    ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    state = ping_state.load(tmp_state)
    # AM1 + AM2 doivent être dans le set (union baseline + diff).
    assert set(state["pinged_uids"]["amendements"]) == {"an_am::AM1", "an_am::AM2"}
    assert state["last_ping_at"] is not None


def test_ping_does_not_update_state_when_smtp_fails(tmp_db, tmp_state):
    """SMTP non configuré (mailer renvoie False) → state inchangé, exit 2."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Nouveau", chamber="AN"),
    ])
    _write_state(tmp_state, {"amendements": []})

    mailer = FakeMailer(ok=False)
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 2
    state = ping_state.load(tmp_state)
    # AM1 NE doit PAS avoir été ajouté — le prochain ping retentera.
    assert state["pinged_uids"].get("amendements", []) == []


def test_ping_does_not_update_state_when_send_disabled(tmp_db, tmp_state):
    """send=False → on détecte les nouveautés mais on ne touche ni SMTP ni state."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Nouveau", chamber="AN"),
    ])
    _write_state(tmp_state, {"amendements": []})
    mailer = FakeMailer()

    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send=False,
        send_email_fn=mailer,
    )
    assert code == 0
    assert mailer.calls == []
    state = ping_state.load(tmp_state)
    assert state["pinged_uids"].get("amendements", []) == []


def test_ping_no_state_update_when_no_new_items(tmp_db, tmp_state):
    """Silence → state inchangé (pas de réécriture inutile)."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Connu", chamber="AN"),
    ])
    _write_state(tmp_state, {"amendements": ["an_am::AM1"]})
    mtime_before = tmp_state.stat().st_mtime

    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 0
    assert tmp_state.stat().st_mtime == mtime_before


# ---------- Cas 5 : baseline état corrompu / absent ----------

def test_ping_treats_missing_state_as_empty_baseline(tmp_db, tmp_state):
    """Si ping_state.json n'existe pas, baseline vide → tout est nouveau."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Premier ping", chamber="AN"),
    ])
    assert not tmp_state.exists()
    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 0
    assert len(mailer.calls) == 1
    # State créé après envoi.
    state = ping_state.load(tmp_state)
    assert "an_am::AM1" in state["pinged_uids"]["amendements"]


def test_ping_treats_corrupt_state_as_empty_baseline(tmp_db, tmp_state):
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Post-corruption", chamber="AN"),
    ])
    tmp_state.write_text("{not-valid-json", encoding="utf-8")
    mailer = FakeMailer()
    code = ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    assert code == 0
    assert len(mailer.calls) == 1


# ---------- Cas 6 : HTML template ----------

def test_ping_html_contains_chamber_badge(tmp_db, tmp_state):
    """Le badge AN / Sénat doit apparaître dans l'HTML."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Item AN", chamber="AN"),
        _make_item("senat_am", "AM2", "amendements", "Item Sénat", chamber="Sénat"),
    ])
    mailer = FakeMailer()
    ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    html = mailer.calls[0][0]
    # Les couleurs de badge doivent être présentes (AN bleu, Sénat vert).
    assert "#20acd9" in html
    assert "#62c925" in html


def test_ping_html_contains_site_url_in_footer(tmp_db, tmp_state):
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "x", chamber="AN"),
    ])
    mailer = FakeMailer()
    ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://custom.site.test", to="me@test",
        send_email_fn=mailer,
    )
    html = mailer.calls[0][0]
    assert "https://custom.site.test" in html


def test_ping_html_links_to_item_url(tmp_db, tmp_state):
    """L'HTML doit faire pointer chaque titre vers url de l'item."""
    _seed(tmp_db, [
        _make_item("an_am", "AM1", "amendements", "Cible",
                   chamber="AN", url="https://an.fr/amdt/42"),
    ])
    mailer = FakeMailer()
    ping.run_ping(
        db_path=tmp_db, state_path=tmp_state,
        site_url="https://site.test", to="me@test",
        send_email_fn=mailer,
    )
    html = mailer.calls[0][0]
    assert 'href="https://an.fr/amdt/42"' in html


# ---------- build_ping_html direct ----------

def test_build_ping_html_empty_diff_returns_zero_total():
    html, total = ping.build_ping_html({}, {}, "https://site.test")
    assert total == 0
    assert "<table" in html


def test_build_ping_html_preserves_category_order():
    """Les sections apparaissent dans l'ordre PING_CATEGORIES
    (dossiers → amendements → questions → CR)."""
    diff = {
        "questions": ["src::q1"],
        "dossiers_legislatifs": ["src::d1"],
        "amendements": ["src::a1"],
    }
    rows_by_hash = {
        "src::q1": {"title": "Q1", "url": "u1", "chamber": "AN", "published_at": ""},
        "src::d1": {"title": "D1", "url": "u2", "chamber": "AN", "published_at": ""},
        "src::a1": {"title": "A1", "url": "u3", "chamber": "AN", "published_at": ""},
    }
    html, total = ping.build_ping_html(diff, rows_by_hash, "https://s.test")
    assert total == 3
    # Dossiers avant Amendements avant Questions dans le HTML.
    i_dossier = html.find("Dossiers législatifs")
    i_amendements = html.find("Amendements")
    i_questions = html.find("Questions")
    assert -1 < i_dossier < i_amendements < i_questions


def test_build_ping_html_escapes_missing_title():
    diff = {"amendements": ["src::a1"]}
    rows_by_hash = {
        "src::a1": {"title": "", "url": "", "chamber": "", "published_at": ""},
    }
    html, total = ping.build_ping_html(diff, rows_by_hash, "https://s.test")
    assert total == 1
    assert "(sans titre)" in html


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
