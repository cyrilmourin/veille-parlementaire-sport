"""Mode `ping` — check d'après-midi des nouveautés matchées.

R24 (2026-04-23) — architecture :
Le pipeline principal (`python -m src.main run`, 4h Paris) écrit à la fin un
snapshot `data/ping_state.json` contenant l'ensemble des hash_keys matchés
dans les 4 catégories "chaudes" (dossiers, amendements, questions, comptes
rendus). Ce ping (`python -m src.main ping`, 17h30 Paris) compare ce set à
l'état actuel de la DB et envoie un email court si du neuf est apparu entre
les deux.

Contraintes :
- Aucun fetch réseau : on part du principe que le run de 4h a déjà ingéré les
  sources du jour, et que des sources "quasi-temps-réel" (an_amendements,
  senat_amendements) se réingèrent via d'autres voies (ex. data.assemblee-
  nationale.fr mis à jour en fil-de-l'eau si un autre run intra-jour les
  repoussé en DB). Le ping se limite à détecter des nouveautés *déjà en DB*.
- Silence total si rien de neuf — pas d'email "tout va bien" à 17h30, ça
  deviendrait du bruit quotidien.
- Idempotent : après envoi, on merge les nouveaux hash_keys dans
  `pinged_uids` pour ne pas re-notifier au prochain ping (ou au ping du
  lendemain si aucun run intermédiaire n'a réécrit le state).

Le template email est volontairement minimaliste (pas de snippets, pas de
statut procédural) : le digest quotidien de 4h reste le format riche, le ping
est une simple alerte "va voir le site".
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from jinja2 import Template

from . import digest, ping_state
from .store import Store

log = logging.getLogger("veille.ping")


CHAMBER_COLORS = {
    "AN": "#20acd9",
    "Senat": "#62c925",
    "Sénat": "#62c925",
}

CATEGORY_LABELS = {
    "dossiers_legislatifs": "Dossiers législatifs",
    "amendements": "Amendements",
    "questions": "Questions",
    "comptes_rendus": "Comptes rendus",
}


PING_EMAIL_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"/><title>Veille sport — nouveautés {{ date_human }}</title></head>
<body style="font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1b2235;background:#EEE8D1;margin:0;padding:24px;">
  <table role="presentation" width="100%" style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #dfd9c1;">
    <tr><td style="padding:18px 24px;background:#122549;color:#EEE8D1;border-bottom:4px solid #DA4431;">
      <div style="font-size:11px;letter-spacing:3px;opacity:.85;font-weight:600;">SIDELINE CONSEIL — PING</div>
      <div style="font-size:18px;font-weight:800;margin-top:3px;color:#fff;">⚡ Nouveautés de l'après-midi</div>
      <div style="opacity:.8;margin-top:6px;font-size:12px;">{{ date_human }} — {{ total }} nouveauté{{ 's' if total > 1 else '' }}</div>
    </td></tr>
    <tr><td style="padding:18px 24px;">
      {% for cat, label in categories %}
        {% if cat in buckets %}
        <h2 style="font-size:12px;text-transform:uppercase;letter-spacing:2px;color:#122549;margin:18px 0 10px;border-left:3px solid #DA4431;padding:2px 0 2px 8px;font-weight:700;">
          {{ label }} <span style="color:#9ca3af;font-weight:400;">({{ buckets[cat]|length }})</span>
        </h2>
        {% for it in buckets[cat] %}
          {%- set ch_color = CHAMBER_COLORS.get(it.chamber, "#5c6577") -%}
          <div style="margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #f1ead0;">
            {% if it.url %}<a href="{{ it.url }}" style="color:#122549;font-weight:600;font-size:14px;text-decoration:none;">{{ it.title }}</a>{% else %}<span style="color:#122549;font-weight:600;font-size:14px;">{{ it.title }}</span>{% endif %}
            <div style="color:#5c6577;font-size:11.5px;margin-top:3px;line-height:1.6;">
              {% if it.chamber %}<span style="display:inline-block;background:{{ ch_color }};color:#fff;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;margin-right:5px;">{{ it.chamber }}</span>{% endif %}
              {% if it.published_at %}<span style="color:#DA4431;font-weight:700;font-variant-numeric:tabular-nums;">{{ it.published_at[:10] }}</span>{% endif %}
            </div>
          </div>
        {% endfor %}
        {% endif %}
      {% endfor %}
    </td></tr>
    <tr><td style="padding:12px 24px;background:#122549;color:#EEE8D1;font-size:11px;text-align:center;">
      Détails et filtres : <a href="{{ site_url }}" style="color:#fff;text-decoration:underline;">{{ site_url }}</a><br/>
      <span style="opacity:.7;">Sideline Conseil — Ping automatique 17h30, lun-ven.</span>
    </td></tr>
  </table>
</body></html>""")


def _fetch_matched_rows_for_categories(
    store: Store,
    categories: tuple[str, ...],
) -> list[dict]:
    """Lit en DB tous les items matchés (matched_keywords != '[]') dont la
    catégorie est dans `categories`. Pas de filtre de date (on compare au set
    connu par hash_key, la date est redondante).

    Rationale : on pourrait filtrer sur `inserted_at > last_run_at`, mais un
    item ré-upserté (R23-A : `_sort_parlementaire_amendement` qui change un
    `raw` sans changer le hash_key) n'aurait pas un `inserted_at` postérieur,
    tandis qu'un item légitimement *nouveau* aura bien un hash_key absent du
    set baseline. Le diff par hash_key est donc plus robuste que par date.
    """
    if not categories:
        return []
    placeholders = ",".join("?" for _ in categories)
    q = f"""
        SELECT hash_key, source_id, uid, category, chamber, title, url,
               published_at, summary, matched_keywords, inserted_at
        FROM items
        WHERE category IN ({placeholders})
          AND matched_keywords IS NOT NULL
          AND matched_keywords != '[]'
        ORDER BY published_at DESC, inserted_at DESC
    """
    cur = store.conn.execute(q, categories)
    return [dict(row) for row in cur.fetchall()]


def _build_buckets_for_email(
    rows_by_hash: dict[str, dict],
    diff: dict[str, list[str]],
) -> dict[str, list[dict]]:
    """Construit {category: [item_dict, …]} pour le template, dans l'ordre
    `diff[cat]` (tri descendant par published_at déjà appliqué en amont).

    On ne garde que titre / url / chambre / published_at : pas de snippet
    ni de status_label. Le ping est une alerte volontairement plate."""
    buckets: dict[str, list[dict]] = {}
    for cat, hks in diff.items():
        items = []
        for hk in hks:
            r = rows_by_hash.get(hk)
            if not r:
                continue
            items.append({
                "title": (r.get("title") or "").strip() or "(sans titre)",
                "url": r.get("url") or "",
                "chamber": r.get("chamber") or "",
                "published_at": r.get("published_at") or "",
            })
        if items:
            buckets[cat] = items
    return buckets


def build_ping_html(
    diff: dict[str, list[str]],
    rows_by_hash: dict[str, dict],
    site_url: str,
    now: datetime | None = None,
) -> tuple[str, int]:
    """Rend le template email. Renvoie (html, total_items)."""
    buckets = _build_buckets_for_email(rows_by_hash, diff)
    total = sum(len(v) for v in buckets.values())
    now = now or datetime.now()
    html = PING_EMAIL_TEMPLATE.render(
        date_human=now.strftime("%A %d %B %Y").capitalize(),
        total=total,
        categories=[(c, CATEGORY_LABELS.get(c, c)) for c in ping_state.PING_CATEGORIES],
        buckets=buckets,
        site_url=site_url,
        CHAMBER_COLORS=CHAMBER_COLORS,
    )
    return html, total


def run_ping(
    db_path: str | Path,
    state_path: str | Path,
    *,
    site_url: str,
    to: str,
    send: bool = True,
    send_email_fn: Optional[Callable[[str, str, str], bool]] = None,
    now: datetime | None = None,
) -> int:
    """Exécute le mode ping. Renvoie un code de sortie :
    - 0  : pas de nouveautés, ou envoi réussi, ou envoi volontairement désactivé
    - 2  : SMTP non configuré (send=True mais send_email_fn a renvoyé False)
    - 10 : erreur inattendue (state illisible en écriture, DB absente, etc.)

    `send_email_fn(html, subject, to) -> bool` est injectable pour les tests
    (par défaut = `digest.send_email`).
    """
    send_fn = send_email_fn or digest.send_email
    now = now or datetime.now(timezone.utc)

    db = Path(db_path)
    if not db.exists():
        log.error("DB introuvable à %s, abandon du ping", db)
        return 10

    store = Store(db)
    try:
        rows = _fetch_matched_rows_for_categories(store, ping_state.PING_CATEGORIES)
    finally:
        store.close()
    log.info(
        "ping : %d items matchés en DB sur les %d catégories surveillées",
        len(rows), len(ping_state.PING_CATEGORIES),
    )

    current = ping_state.snapshot_from_rows(rows, ping_state.PING_CATEGORIES)
    state = ping_state.load(state_path)
    baseline = state.get("pinged_uids") or {}
    diff = ping_state.diff_new(current, baseline, ping_state.PING_CATEGORIES)

    if not diff:
        log.info("ping : aucune nouveauté, silence.")
        return 0

    total = sum(len(v) for v in diff.values())
    log.info(
        "ping : %d nouveauté(s) → %s",
        total,
        ", ".join(f"{cat}={len(uids)}" for cat, uids in sorted(diff.items())),
    )

    rows_by_hash = {r["hash_key"]: r for r in rows if r.get("hash_key")}
    html, total_html = build_ping_html(diff, rows_by_hash, site_url, now=now)
    subject = f"⚡ Veille — {total_html} nouvelle{'s' if total_html > 1 else ''} occurrence{'s' if total_html > 1 else ''} cet après-midi"

    if not send:
        log.info("ping : envoi désactivé (--no-email), state non mis à jour.")
        return 0

    ok = send_fn(html, subject, to)
    if not ok:
        log.warning("ping : SMTP non configuré, email non envoyé. State non mis à jour.")
        return 2

    log.info("ping : email envoyé à %s.", to)
    merged = ping_state.merge(baseline, diff)
    ping_state.save(
        state_path,
        last_run_at=_parse_iso(state.get("last_run_at")),
        last_ping_at=now,
        pinged_uids=merged,
    )
    return 0


def _parse_iso(s: str | None) -> datetime | None:
    """Parse tolérant : None, chaîne vide, ou format ISO variable."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        log.warning("ping : last_run_at illisible (%r), ignoré", s)
        return None
