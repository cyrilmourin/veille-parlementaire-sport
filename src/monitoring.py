"""R29 (2026-04-24) — Monitoring santé du pipeline.

Objectif : détecter les sources qui **tombent en panne** (pas juste les
sources silencieusement à 0 — c'est normal sur notre scope réduit, cf.
échange Cyril 2026-04-24). Trois signaux seuls méritent d'alerter :

1. **ERR_PERSIST** — la source renvoie une erreur réseau (4xx/5xx,
   timeout, DNS) pendant N runs consécutifs. Seuil : 3 runs.
2. **FORMAT_DRIFT** — la source renvoyait ≥ 5 items au run précédent et
   0 aujourd'hui SANS erreur réseau (le HTTP répond, le parser sort
   vide). Signal fort d'un changement de HTML/XML côté source qui
   casse le parser.
3. **FEED_STALE** — la date max de l'item le plus récent de la source
   dépasse 60 jours ALORS QU'AU RUN PRÉCÉDENT elle était < 60 j. Signal
   que le feed s'est figé (CMS côté source qui a arrêté de publier).

L'état est persisté dans `data/pipeline_health.json` (versionné comme
`ping_state.json`) et committé en fin de workflow GHA. À chaque run, on
lit l'état J-1, on calcule l'état J, on émet les alertes en comparant
les deux.

Conséquences :
- Le digest email quotidien intègre un bloc « Santé du pipeline » —
  vide si rien à signaler (pas de spam quotidien « tout va bien »),
  visible seulement quand au moins une source est cassée.
- Les logs de `src/main.py run` émettent aussi les alertes en WARNING
  pour qu'elles soient visibles dans le run GHA.

Ce module n'émet PAS d'alerte si :
- Une source est désactivée (`enabled: false`) — traité comme "pas de
  signal à surveiller".
- Une source est toujours à 0 depuis son ajout (jamais eu ≥ 5 items) —
  c'est le scope réduit assumé, pas une panne.
- Le fichier `pipeline_health.json` n'existe pas encore (1er run) —
  on initialise l'état sans émettre d'alerte (pas de J-1 pour comparer).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Seuils — valeurs conservatrices pour éviter le bruit. Modifier ici si
# un run J-1 s'avère trop faux-positif (et noter dans HANDOFF).
THRESHOLD_ERR_PERSIST_RUNS = 3
THRESHOLD_FORMAT_DRIFT_MIN_PREV_COUNT = 5
THRESHOLD_FEED_STALE_DAYS = 60


@dataclass
class Alert:
    """Une alerte unique sur une source."""
    kind: str            # "ERR_PERSIST" | "FORMAT_DRIFT" | "FEED_STALE"
    source_id: str
    message: str         # phrase prête à afficher dans le digest


def _parse_iso_naive(s: str | None) -> datetime | None:
    """Parse un ISO 8601 (avec ou sans tz) et retourne un datetime naïf UTC.

    Convention projet : tous les timestamps sont stockés en naïf UTC
    (cf. `_parse_iso_naive` dans `keywords.py` / `site_export.py`).
    """
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _max_published_at(items: Iterable) -> datetime | None:
    """Max(published_at) parmi les items d'une source (ignore les None)."""
    max_dt: datetime | None = None
    for it in items:
        pub = getattr(it, "published_at", None)
        if pub is None:
            continue
        # Les items du pipeline ont déjà un published_at naïf UTC
        # (convention `_parse_iso_naive` de R11f). On accepte tout de même
        # une str au cas où (robustesse).
        if isinstance(pub, str):
            pub = _parse_iso_naive(pub)
        if pub is None:
            continue
        if pub.tzinfo is not None:
            pub = pub.astimezone(timezone.utc).replace(tzinfo=None)
        if max_dt is None or pub > max_dt:
            max_dt = pub
    return max_dt


def load_state(path: str | Path) -> dict:
    """Charge l'état précédent. Retourne dict vide si absent ou illisible."""
    p = Path(path)
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "sources": {}, "last_run_at": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("pipeline_health.json illisible (%s) — reset état", e)
        return {"schema_version": SCHEMA_VERSION, "sources": {}, "last_run_at": None}
    if not isinstance(data, dict) or "sources" not in data:
        return {"schema_version": SCHEMA_VERSION, "sources": {}, "last_run_at": None}
    return data


def save_state(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _items_by_source(items: Iterable) -> dict[str, list]:
    buckets: dict[str, list] = {}
    for it in items:
        sid = getattr(it, "source_id", None) or ""
        if not sid:
            continue
        buckets.setdefault(sid, []).append(it)
    return buckets


def compute_state_and_alerts(
    previous_state: dict,
    fetch_stats: dict,
    items: Iterable,
    now: datetime | None = None,
) -> tuple[dict, list[Alert]]:
    """Calcule l'état J + les alertes. Fonction pure, testable isolément.

    :param previous_state: résultat de `load_state(...)` du run J-1.
    :param fetch_stats: `{source_id: {"fetched": int, "error": str|None}}`
        tel que retourné par `normalize.run_all`.
    :param items: itérable des Item du run J (pour calculer le
        max(published_at) par source).
    :param now: horodatage du run (defaults à `_now_utc_naive`).

    Retourne `(new_state, alerts)` — `new_state` est à persister via
    `save_state`, `alerts` est la liste à afficher dans le digest.
    """
    now = now or _now_utc_naive()
    prev_sources: dict = (previous_state or {}).get("sources") or {}
    new_sources: dict = {}
    alerts: list[Alert] = []

    items_by_sid = _items_by_source(items)

    for sid, stats in fetch_stats.items():
        fetched: int = int(stats.get("fetched") or 0)
        error: str | None = stats.get("error")
        prev: dict = prev_sources.get(sid) or {}
        prev_fetched: int = int(prev.get("last_fetched") or 0)
        prev_errors: int = int(prev.get("consecutive_errors") or 0)
        prev_max_pub = _parse_iso_naive(prev.get("last_max_published_at"))
        prev_had_err = bool(prev.get("last_error"))

        # État J pour cette source
        max_pub = _max_published_at(items_by_sid.get(sid, []))
        if max_pub is None and not error:
            # Pas de nouveaux items aujourd'hui — on conserve le max(J-1)
            # pour que FEED_STALE puisse continuer à le comparer.
            max_pub = prev_max_pub

        if error:
            consec_err = prev_errors + 1
            last_ok_at = prev.get("last_ok_at")
        else:
            consec_err = 0
            last_ok_at = now.isoformat(timespec="seconds")

        # Drapeau « déjà alerté pour feed figé » — porté d'un run à l'autre
        # pour éviter de spammer quotidiennement quand un feed reste figé.
        # Reset à False dès que la fraîcheur repasse sous le seuil.
        prev_stale_alerted = bool(prev.get("stale_alerted"))

        new_entry = {
            "last_fetched": fetched,
            "last_error": error,
            "last_ok_at": last_ok_at,
            "consecutive_errors": consec_err,
            "last_max_published_at": (
                max_pub.isoformat(timespec="seconds") if max_pub else None
            ),
            "stale_alerted": prev_stale_alerted,
        }
        new_sources[sid] = new_entry

        # Alerte 1 — ERR_PERSIST : N runs consécutifs en erreur.
        # On déclenche exactement au passage du seuil (ni avant, ni après)
        # pour ne pas spammer le digest quotidien.
        if consec_err == THRESHOLD_ERR_PERSIST_RUNS:
            alerts.append(Alert(
                kind="ERR_PERSIST",
                source_id=sid,
                message=(
                    f"{sid} en erreur depuis {consec_err} runs consécutifs "
                    f"— dernière erreur : {error}"
                ),
            ))

        # Alerte 2 — FORMAT_DRIFT : le parser ne retourne plus rien alors
        # qu'il remontait N items hier, SANS erreur réseau côté HTTP.
        # Pas déclenchée si erreur (c'est ERR_PERSIST qui s'en charge).
        # Pas déclenchée si J-1 avait déjà < MIN_PREV_COUNT items (pour
        # ne pas alerter sur des sources de queue longue normalement ~1/sem).
        if (
            not error
            and fetched == 0
            and prev_fetched >= THRESHOLD_FORMAT_DRIFT_MIN_PREV_COUNT
            and not prev_had_err
        ):
            alerts.append(Alert(
                kind="FORMAT_DRIFT",
                source_id=sid,
                message=(
                    f"{sid} : 0 items aujourd'hui alors que {prev_fetched} "
                    f"au run précédent, HTTP OK — probable changement "
                    f"de format côté source"
                ),
            ))

        # Alerte 3 — FEED_STALE : la fraîcheur de la source dépasse le
        # seuil (60 jours par défaut). On utilise un drapeau persistant
        # `stale_alerted` pour n'alerter qu'UNE FOIS par bascule : tant
        # qu'il reste au-dessus du seuil, pas de répétition. Dès qu'un
        # item récent apparaît (cur_age <= seuil), le drapeau est
        # remis à False et une prochaine bascule pourra re-alerter.
        if max_pub is not None:
            cur_age_days = (now - max_pub).days
            if cur_age_days > THRESHOLD_FEED_STALE_DAYS:
                if not prev_stale_alerted:
                    alerts.append(Alert(
                        kind="FEED_STALE",
                        source_id=sid,
                        message=(
                            f"{sid} figé : dernier item publié il y a "
                            f"{cur_age_days} jours (seuil {THRESHOLD_FEED_STALE_DAYS}) "
                            f"— feed inactif côté source"
                        ),
                    ))
                    new_entry["stale_alerted"] = True
            else:
                new_entry["stale_alerted"] = False

    # On conserve les entrées des sources plus présentes aujourd'hui
    # (désactivées entre-temps) pour garder l'historique, mais on ne les
    # compare pas contre J+1 (un source_id disparu = plus dans fetch_stats,
    # pas notre problème).
    for sid, prev in prev_sources.items():
        if sid not in new_sources:
            new_sources[sid] = prev

    new_state = {
        "schema_version": SCHEMA_VERSION,
        "last_run_at": now.isoformat(timespec="seconds"),
        "sources": new_sources,
    }
    return new_state, alerts


# ---------------------------------------------------------------------------
# Rendu digest — bloc « Santé du pipeline »
# ---------------------------------------------------------------------------

_ALERT_KIND_LABELS = {
    "ERR_PERSIST": "Erreur persistante",
    "FORMAT_DRIFT": "Format cassé",
    "FEED_STALE": "Feed figé",
}

_DIGEST_BLOCK_TEMPLATE = """\
<table role="presentation" width="100%" style="margin:0 0 24px;background:#fff4e0;border-radius:10px;border:1px solid #e8d7a8;">
  <tr><td style="padding:14px 18px;">
    <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#8a5a00;font-weight:700;">Santé du pipeline — {n} alerte{s}</div>
    <ul style="margin:10px 0 0;padding-left:18px;color:#4e3500;font-size:13px;line-height:1.55;">
{items}
    </ul>
  </td></tr>
</table>
"""


def render_digest_block(alerts: list[Alert]) -> str:
    """Rend un bloc HTML prêt à injecter en tête du digest email.

    Retourne une string vide si aucune alerte — le template Jinja du
    digest peut juste la concaténer sans garde particulière.
    """
    if not alerts:
        return ""
    lines = []
    for a in alerts:
        kind_label = _ALERT_KIND_LABELS.get(a.kind, a.kind)
        lines.append(
            f'      <li><strong>[{kind_label}]</strong> {a.message}</li>'
        )
    return _DIGEST_BLOCK_TEMPLATE.format(
        n=len(alerts),
        s="s" if len(alerts) > 1 else "",
        items="\n".join(lines),
    )


def log_alerts(alerts: list[Alert]) -> None:
    """Émet les alertes en WARNING (visible dans les logs GHA)."""
    if not alerts:
        log.info("Monitoring : aucune alerte santé pipeline")
        return
    log.warning("Monitoring : %d alerte(s) santé pipeline", len(alerts))
    for a in alerts:
        log.warning("  [%s] %s", a.kind, a.message)
