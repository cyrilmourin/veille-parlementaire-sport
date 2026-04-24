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

SCHEMA_VERSION = 2  # R34 : ajout volumetry_history

# Seuils — valeurs conservatrices pour éviter le bruit. Modifier ici si
# un run J-1 s'avère trop faux-positif (et noter dans HANDOFF).
THRESHOLD_ERR_PERSIST_RUNS = 3
THRESHOLD_FORMAT_DRIFT_MIN_PREV_COUNT = 5
THRESHOLD_FEED_STALE_DAYS = 60

# R34 (2026-04-24) — Volumétrie : garde un historique des 30 derniers
# runs (ring buffer) pour détecter un collapse silencieux du pipeline
# (ex : ingestion passe de 400 items/jour → 20 sans qu'aucune source
# n'ait déclenché d'alerte individuelle). Seuil : volume J < 50 % de la
# moyenne des 7 derniers runs ET échantillon J-7 d'au moins 5 runs
# (pour éviter le faux-positif sur DB fraîche).
VOLUMETRY_HISTORY_MAX = 30
VOLUMETRY_COLLAPSE_RATIO = 0.50
VOLUMETRY_MIN_SAMPLES = 5

# R34 — Seuil de fail CI (opt-in via env var STRICT_MONITORING=1). Ne
# compte que ERR_PERSIST + VOLUMETRY_COLLAPSE — FEED_STALE/FORMAT_DRIFT
# peuvent cascader sur plusieurs sources simultanément sur un simple
# changement CMS, donc ne doivent pas bloquer le CI. Cf. consigne Cyril
# 2026-04-24 : « pas trop pousser sur la surveillance ».
STRICT_CI_ALERT_KINDS = frozenset({"ERR_PERSIST", "VOLUMETRY_COLLAPSE"})
STRICT_CI_FAIL_THRESHOLD = 3


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


def _empty_state() -> dict:
    """État neuf (1er run ou fichier corrompu). Factorisé pour que
    l'ajout de `volumetry_history` en R34 ne multiplie pas les
    littéraux."""
    return {
        "schema_version": SCHEMA_VERSION,
        "sources": {},
        "volumetry_history": [],
        "last_run_at": None,
    }


def load_state(path: str | Path) -> dict:
    """Charge l'état précédent. Retourne un état neuf si absent,
    illisible ou si le schema_version est plus ancien que le courant
    (cas upgrade R29 → R34 : on garde les sources mais on initialise
    `volumetry_history` à vide si absent)."""
    p = Path(path)
    if not p.exists():
        return _empty_state()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("pipeline_health.json illisible (%s) — reset état", e)
        return _empty_state()
    if not isinstance(data, dict) or "sources" not in data:
        return _empty_state()
    # Upgrade progressif : si la clé volumetry_history n'existe pas
    # (state écrit par R29), on la crée vide. Les sources existantes
    # sont préservées — pas de reset à l'upgrade.
    data.setdefault("volumetry_history", [])
    data.setdefault("last_run_at", None)
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

    # R34 — Volumétrie : on ajoute le run courant au ring buffer puis on
    # calcule le collapse. Le buffer est plafonné à VOLUMETRY_HISTORY_MAX
    # pour ne pas grossir indéfiniment ; les entrées anciennes sont jetées.
    # Chaque entrée ne porte que la date + total_fetched (pas de per_source
    # pour rester léger — les détails par source sont déjà dans new_sources).
    new_history = list((previous_state or {}).get("volumetry_history") or [])
    total_fetched = sum(
        int((s or {}).get("fetched") or 0) for s in fetch_stats.values()
    )
    new_history.append({
        "date": now.isoformat(timespec="seconds"),
        "total_fetched": total_fetched,
    })
    if len(new_history) > VOLUMETRY_HISTORY_MAX:
        new_history = new_history[-VOLUMETRY_HISTORY_MAX:]

    alerts.extend(_volumetry_collapse_alerts(new_history))

    new_state = {
        "schema_version": SCHEMA_VERSION,
        "last_run_at": now.isoformat(timespec="seconds"),
        "sources": new_sources,
        "volumetry_history": new_history,
    }
    return new_state, alerts


def _volumetry_collapse_alerts(history: list[dict]) -> list[Alert]:
    """Détecte un collapse global : run J < VOLUMETRY_COLLAPSE_RATIO de
    la moyenne des 7 runs précédents, à condition d'avoir suffisamment
    d'historique (≥ VOLUMETRY_MIN_SAMPLES runs antérieurs) pour que la
    moyenne soit significative.

    Ne monitore QUE le total global — pas par source — parce que le
    scope réduit du projet fait qu'une source peut légitimement être à
    0 un jour (FORMAT_DRIFT s'en charge déjà). L'intérêt du collapse
    global est d'attraper une chute silencieuse massive que personne
    n'a remontée via une alerte individuelle (ex : un refactor qui
    casse le scoring → 90 % des items filtrés en silence).
    """
    if len(history) < VOLUMETRY_MIN_SAMPLES + 1:
        return []
    current = int(history[-1].get("total_fetched") or 0)
    # Fenêtre des 7 derniers runs AVANT le courant (si dispo)
    window = history[-8:-1] if len(history) >= 8 else history[:-1]
    if len(window) < VOLUMETRY_MIN_SAMPLES:
        return []
    avg = sum(int(e.get("total_fetched") or 0) for e in window) / len(window)
    if avg <= 0:
        return []
    ratio = current / avg
    if ratio >= VOLUMETRY_COLLAPSE_RATIO:
        return []
    return [Alert(
        kind="VOLUMETRY_COLLAPSE",
        source_id="*",
        message=(
            f"Volume global en chute : {current} items aujourd'hui "
            f"contre ~{avg:.0f} en moyenne sur les {len(window)} runs "
            f"précédents (ratio {ratio:.0%}, seuil "
            f"{VOLUMETRY_COLLAPSE_RATIO:.0%})"
        ),
    )]


def compute_freshness_snapshot(
    state: dict, now: datetime | None = None
) -> list[tuple[str, int]]:
    """Âge (en jours) du dernier item connu pour chaque source.

    Utilisé pour enrichir le digest : permet de voir d'un coup d'œil
    les sources qui vieillissent SANS encore avoir franchi le seuil
    FEED_STALE (par défaut 60 j). Une source sans `last_max_published_at`
    (jamais eu d'items) est ignorée — pas de signal utilisable.

    Retourne une liste `[(source_id, age_days), ...]` triée par âge
    décroissant (la plus ancienne en tête).
    """
    now = now or _now_utc_naive()
    sources = (state or {}).get("sources") or {}
    snapshots: list[tuple[str, int]] = []
    for sid, entry in sources.items():
        max_pub = _parse_iso_naive((entry or {}).get("last_max_published_at"))
        if max_pub is None:
            continue
        age_days = (now - max_pub).days
        snapshots.append((sid, age_days))
    snapshots.sort(key=lambda x: x[1], reverse=True)
    return snapshots


def compute_volumetry_averages(state: dict) -> dict:
    """Moyennes J-7 / J-30 du volume total, pour affichage dans le digest.

    Retourne `{"current": int, "avg_7d": float|None, "avg_30d": float|None,
    "samples": int}`. Les moyennes sont None si l'historique n'a pas assez
    de runs (minimum 2 runs dans la fenêtre pour qu'une moyenne ait du sens).
    """
    history = (state or {}).get("volumetry_history") or []
    if not history:
        return {"current": 0, "avg_7d": None, "avg_30d": None, "samples": 0}
    current = int(history[-1].get("total_fetched") or 0)
    samples = len(history)

    def _mean(xs: list[dict]) -> float | None:
        if len(xs) < 2:
            return None
        return sum(int(e.get("total_fetched") or 0) for e in xs) / len(xs)

    win_7 = history[-8:-1] if samples >= 8 else history[:-1]
    win_30 = history[-31:-1] if samples >= 31 else history[:-1]
    return {
        "current": current,
        "avg_7d": _mean(win_7),
        "avg_30d": _mean(win_30),
        "samples": samples,
    }


def should_fail_ci(
    alerts: list[Alert], env_var: str = "STRICT_MONITORING"
) -> bool:
    """Retourne True si le CI doit sortir en échec (exit code != 0).

    Opt-in via variable d'environnement (défaut : `STRICT_MONITORING`).
    Sans la var, retourne toujours False — on ne casse pas le CI quotidien
    par défaut, l'observation se fait via le digest email.

    Ne compte que les alertes de type STRICT_CI_ALERT_KINDS : les
    FEED_STALE et FORMAT_DRIFT peuvent cascader simultanément sur
    plusieurs sources si un CMS change (ex : Élysée R22c), alors que
    ERR_PERSIST + VOLUMETRY_COLLAPSE sont des signaux « le pipeline est
    réellement cassé ». Seuil : STRICT_CI_FAIL_THRESHOLD (3).
    """
    import os
    if not os.environ.get(env_var):
        return False
    strict_count = sum(1 for a in alerts if a.kind in STRICT_CI_ALERT_KINDS)
    return strict_count >= STRICT_CI_FAIL_THRESHOLD


# ---------------------------------------------------------------------------
# Rendu digest — bloc « Santé du pipeline »
# ---------------------------------------------------------------------------

_ALERT_KIND_LABELS = {
    "ERR_PERSIST": "Erreur persistante",
    "FORMAT_DRIFT": "Format cassé",
    "FEED_STALE": "Feed figé",
    "VOLUMETRY_COLLAPSE": "Volume en chute",
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
