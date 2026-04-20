"""Tests sur la normalisation des datetimes en naïf UTC (R11f).

Régression couverte : `an_agenda` crashait avec `can't compare offset-naive
and offset-aware datetimes` parce que `parse_iso` renvoyait un datetime
tz-aware sur les timestamps AN du type `2025-11-07T21:30:00.000+01:00`,
incompatible avec le `since = _utcnow_naive() - timedelta(...)` du
filtre `since_days` côté `assemblee.fetch_source`.

Convention du projet (cf. `main.py:71` et `_utcnow_naive`) : tous les
`published_at` sont naïfs UTC. R11f impose cette normalisation à l'entrée
du pipeline plutôt que de patcher chaque call site.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.sources._common import parse_iso  # noqa: E402
from src.site_export import _parse_dt  # noqa: E402
from src.sources.senat import _parse_date_any  # noqa: E402


# ---------- parse_iso : politique unifiée naïf UTC --------------------------

@pytest.mark.parametrize("s,expected", [
    # Format AN agenda observé (R11f cause-racine) : aware avec offset +01:00
    ("2025-11-07T21:30:00.000+01:00", datetime(2025, 11, 7, 20, 30, 0)),
    # Format Z = UTC explicite
    ("2026-04-18T12:34:56Z", datetime(2026, 4, 18, 12, 34, 56)),
    # Format +00:00 = UTC explicite
    ("2026-04-18T12:34:56+00:00", datetime(2026, 4, 18, 12, 34, 56)),
    # Décalage US/Pacifique pour bien tester la conversion
    ("2026-04-18T12:00:00-08:00", datetime(2026, 4, 18, 20, 0, 0)),
    # Naïf déjà : passe-plat
    ("2026-04-18T12:34:56", datetime(2026, 4, 18, 12, 34, 56)),
    # Date simple
    ("2026-04-18", datetime(2026, 4, 18, 0, 0, 0)),
])
def test_parse_iso_returns_naive_utc(s, expected):
    """Tout input ISO valide ressort naïf UTC, peu importe la tz d'origine."""
    dt = parse_iso(s)
    assert dt is not None, f"parse_iso({s!r}) a renvoyé None"
    assert dt.tzinfo is None, f"parse_iso({s!r}) renvoie aware ({dt!r})"
    assert dt == expected, f"parse_iso({s!r}) = {dt!r}, attendu {expected!r}"


@pytest.mark.parametrize("invalid", [None, "", "   ", "pas une date", "2026-99-99"])
def test_parse_iso_rejects_invalid(invalid):
    assert parse_iso(invalid) is None


def test_parse_iso_comparable_to_utcnow_naive():
    """Le bug R11f : `parse_iso(aware) < _utcnow_naive()` levait TypeError.
    Vérifie que la comparaison passe maintenant sans exception.
    """
    aware = parse_iso("2025-11-07T21:30:00.000+01:00")
    naive_now = datetime.utcnow()
    naive_since = naive_now - timedelta(days=30)
    # Doit passer sans TypeError
    _ = aware < naive_since
    _ = aware > naive_since


# ---------- senat._parse_date_any : même normalisation ----------------------

def test_senat_parse_date_any_normalizes_aware():
    """senat._parse_date_any partageait le même bug, fixé en R11f."""
    dt = _parse_date_any("2026-04-18T10:00:00+02:00")
    assert dt is not None
    assert dt.tzinfo is None
    # 10h+02 → 8h UTC
    assert dt == datetime(2026, 4, 18, 8, 0, 0)


def test_senat_parse_date_any_french_format_unchanged():
    """Les formats DD/MM/YYYY (CSV Sénat) restent gérés."""
    dt = _parse_date_any("18/04/2026")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 4 and dt.day == 18
    assert dt.tzinfo is None


# ---------- site_export._parse_dt : pareil pour les valeurs en DB ----------

def test_site_export_parse_dt_normalizes_aware():
    """Un published_at stocké aware en DB (item ingéré pré-R11f) doit
    quand même se comparer proprement à datetime.utcnow() en aval.
    """
    dt = _parse_dt("2025-11-07T21:30:00+01:00")
    assert dt is not None
    assert dt.tzinfo is None
    assert dt == datetime(2025, 11, 7, 20, 30, 0)


def test_site_export_parse_dt_passthrough_naive_datetime():
    """Si on passe un datetime déjà naïf, retour identité."""
    naive = datetime(2026, 4, 18, 10, 0, 0)
    out = _parse_dt(naive)
    assert out is naive or out == naive
    assert out.tzinfo is None


def test_site_export_parse_dt_strips_tz_from_aware_datetime():
    """Si on passe un datetime aware, conversion UTC + strip."""
    aware = datetime(2025, 11, 7, 21, 30, 0, tzinfo=timezone(timedelta(hours=1)))
    out = _parse_dt(aware)
    assert out is not None
    assert out.tzinfo is None
    assert out == datetime(2025, 11, 7, 20, 30, 0)
