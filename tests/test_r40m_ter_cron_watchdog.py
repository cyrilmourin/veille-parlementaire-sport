"""Tests R40-M-ter (2026-04-29) — cron-watchdog robuste.

Contexte : le 29-04-2026 au matin (premier jour avec le nouveau cron
principal `42 3 * * *` introduit la veille en R40-M-bis), `daily.yml`
ET `cron-watchdog.yml` ont été tous les deux silencieusement skippés
par GHA Free → site figé sur les données du 28-04. Le watchdog héritait
du bug qu'il prétendait corriger : `0 9 * * *` est sur l'heure pile
(pic de saturation Free). De plus, son cutoff `5 hours ago` (= 04:00 UTC
quand le watchdog fire à 09:00 UTC) était postérieur au cron principal
à 03:42 UTC → faux négatifs systématiques (le watchdog ne reconnaissait
plus un run à temps comme « récent » et déclenchait des dispatches en
double).

Garde-fous structurels :
  1. Aucun cron du watchdog n'est sur l'heure pile (minute 0).
  2. Le cron principal de daily.yml n'est pas non plus sur l'heure pile.
  3. Le cutoff du watchdog (03:00 UTC du jour) précède bien l'horaire
     du cron principal — sinon le watchdog ne pourrait jamais voir un
     run nominal et dispatcherait toujours.
  4. Le watchdog tourne au moins deux fois par jour (filet n°2 si le
     premier saute aussi).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


_ROOT = Path(__file__).resolve().parents[1]
_DAILY = _ROOT / ".github" / "workflows" / "daily.yml"
_WATCHDOG = _ROOT / ".github" / "workflows" / "cron-watchdog.yml"


def _load_workflow(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    # PyYAML interprète la clé YAML `on:` comme le booléen True. On la
    # ré-extrait par parsing direct du document.
    data = yaml.safe_load(text)
    if "on" not in data and True in data:
        data["on"] = data.pop(True)
    return data


def _crons(workflow: dict) -> list[str]:
    on = workflow.get("on") or {}
    schedule = on.get("schedule") or []
    return [item["cron"] for item in schedule if "cron" in item]


def _parse_cron(expr: str) -> tuple[str, str]:
    """Retourne (minute, hour) en str ; lève AssertionError si format
    inattendu (5 champs minimum séparés par des espaces)."""
    parts = expr.split()
    assert len(parts) >= 5, f"expression cron inattendue : {expr!r}"
    return parts[0], parts[1]


def test_watchdog_crons_avoid_top_of_hour():
    """Aucun cron watchdog ne doit être sur l'heure pile (`0 N * * *`)."""
    crons = _crons(_load_workflow(_WATCHDOG))
    assert crons, "cron-watchdog.yml doit avoir au moins une entrée schedule"
    for expr in crons:
        minute, _hour = _parse_cron(expr)
        assert minute != "0", (
            f"Cron watchdog `{expr}` est sur l'heure pile (minute 0) — "
            "pic de saturation GHA Free, risque de skip silencieux. "
            "Cf. R40-M-ter."
        )


def test_daily_main_cron_avoids_top_of_hour():
    """Le cron matin de daily.yml ne doit pas être sur l'heure pile.

    R40-M (`42 5`) puis R40-M-bis (`42 3`) ont déjà déplacé l'horaire
    pour cette raison. Régression test : si quelqu'un ré-aligne sur
    minute 0, on alerte.
    """
    crons = _crons(_load_workflow(_DAILY))
    assert crons, "daily.yml doit avoir au moins une entrée schedule"
    minutes = [_parse_cron(expr)[0] for expr in crons]
    assert "0" not in minutes, (
        f"Au moins un cron daily.yml est sur l'heure pile : {crons!r}. "
        "Risque de skip GHA Free. Cf. R40-M / R40-M-bis."
    )


def test_watchdog_has_at_least_two_daily_schedules():
    """Filet n°2 : le watchdog du matin peut sauter aussi (saturation,
    panne GHA…) → on veut au moins une seconde fenêtre de récupération
    dans la journée."""
    crons = _crons(_load_workflow(_WATCHDOG))
    assert len(crons) >= 2, (
        f"cron-watchdog.yml doit avoir au moins 2 schedules (matin + filet). "
        f"Trouvé : {crons!r}. Cf. R40-M-ter."
    )


def test_watchdog_cutoff_precedes_daily_morning_cron():
    """Le cutoff du watchdog doit être ANTÉRIEUR à l'horaire du cron
    principal de daily.yml. Sinon le watchdog ne peut jamais détecter
    un run à temps comme `récent` → faux négatifs systématiques (bug
    R40-M-bis non corrigé jusqu'au 29-04)."""
    daily_crons = _crons(_load_workflow(_DAILY))
    daily_minutes_hours: list[tuple[int, int]] = []
    for expr in daily_crons:
        m, h = _parse_cron(expr)
        # On ne considère que les crons quotidiens fixes (pas les jours
        # de semaine spécifiques type `30 15 * * 1-5`).
        parts = expr.split()
        if parts[4] != "*":
            continue
        try:
            daily_minutes_hours.append((int(h), int(m)))
        except ValueError:
            continue
    assert daily_minutes_hours, (
        "daily.yml doit avoir au moins un cron quotidien fixe (jour=*)."
    )
    earliest_h, earliest_m = min(daily_minutes_hours)
    earliest_minutes_of_day = earliest_h * 60 + earliest_m

    watchdog_text = _WATCHDOG.read_text(encoding="utf-8")
    cutoff_match = re.search(
        r'date -u \+%Y-%m-%dT(\d{2}):(\d{2}):\d{2}Z',
        watchdog_text,
    )
    assert cutoff_match, (
        "cron-watchdog.yml doit calculer un CUTOFF explicite via "
        "`date -u +%Y-%m-%dTHH:MM:SSZ` du jour courant. Cf. R40-M-ter."
    )
    cutoff_h = int(cutoff_match.group(1))
    cutoff_m = int(cutoff_match.group(2))
    cutoff_minutes_of_day = cutoff_h * 60 + cutoff_m

    assert cutoff_minutes_of_day < earliest_minutes_of_day, (
        f"Cutoff watchdog ({cutoff_h:02d}:{cutoff_m:02d} UTC) doit être "
        f"antérieur au cron principal le plus matinal "
        f"({earliest_h:02d}:{earliest_m:02d} UTC). Sinon un run nominal "
        "n'est jamais reconnu comme `récent`. Cf. R40-M-ter."
    )
