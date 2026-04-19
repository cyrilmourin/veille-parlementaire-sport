"""Génération de l'email HTML quotidien."""
from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable

from jinja2 import Template

CATEGORY_LABELS = {
    "dossiers_legislatifs": "Dossiers législatifs",
    "jorf": "JORF",
    "amendements": "Amendements",
    "questions": "Questions",
    "comptes_rendus": "Comptes rendus",
    "nominations": "Nominations",
    "agenda": "Agenda",
    "communiques": "Communiqués",
}

CATEGORY_ORDER = list(CATEGORY_LABELS.keys())

EMAIL_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"/><title>Veille parlementaire sport — {{ date }}</title></head>
<body style="font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1b2235;background:#EEE8D1;margin:0;padding:24px;">
  <table role="presentation" width="100%" style="max-width:720px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #dfd9c1;">
    <tr><td style="padding:22px 28px;background:#122549;color:#EEE8D1;border-bottom:4px solid #DA4431;">
      <div style="font-size:12px;letter-spacing:3px;opacity:.85;font-weight:600;">SIDELINE CONSEIL</div>
      <div style="font-size:22px;font-weight:800;margin-top:3px;color:#fff;">Veille parlementaire sport</div>
      <div style="font-size:13px;font-style:italic;color:#DA4431;margin-top:3px;">Voir clair. Jouer juste.</div>
      <div style="opacity:.8;margin-top:10px;font-size:13px;">{{ date_human }} — {{ total }} nouveauté{{ 's' if total > 1 else '' }}</div>
    </td></tr>
    <tr><td style="padding:24px 28px;">
    {% if total == 0 %}
      <p style="margin:0;color:#5c6577;">Aucun nouvel item sur les 24 dernières heures. Les sources ont bien été collectées ;
      la veille reste active sur <a href="{{ site_url }}" style="color:#DA4431;">{{ site_url }}</a>.</p>
    {% endif %}
    {% for cat, label in categories %}
      {% if cat in buckets %}
      <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:2px;color:#122549;margin:26px 0 12px;border-left:4px solid #DA4431;padding:2px 0 2px 10px;font-weight:700;">
        {{ label }} <span style="color:#9ca3af;font-weight:400;">({{ buckets[cat]|length }})</span>
      </h2>
      {% for it in buckets[cat] %}
        <div style="margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #f1ead0;">
          <a href="{{ it.url }}" style="color:#122549;font-weight:600;font-size:15px;text-decoration:none;">{{ it.title }}</a>
          <div style="color:#5c6577;font-size:12px;margin-top:3px;">
            {{ it.chamber or '' }}{% if it.published_at %} · {{ it.published_at[:10] }}{% endif %}
          </div>
          {% if it.snippet %}
          <div style="color:#5c6577;font-size:13px;margin-top:8px;padding:8px 12px;border-left:3px solid #DA4431;background:#fff8ea;font-style:italic;">« {{ it.snippet }} »</div>
          {% elif it.summary %}
          <div style="color:#374151;font-size:13px;margin-top:6px;">{{ it.summary }}</div>
          {% endif %}
          {% if it.matched %}
          <div style="margin-top:8px;">
            {% for kw in it.matched %}
            <span style="display:inline-block;background:#DA4431;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;margin:2px 4px 2px 0;">{{ kw }}</span>
            {% endfor %}
          </div>
          {% endif %}
        </div>
      {% endfor %}
      {% endif %}
    {% endfor %}
    </td></tr>
    <tr><td style="padding:16px 28px;background:#122549;color:#EEE8D1;font-size:12px;text-align:center;">
      Consulter la veille complète : <a href="{{ site_url }}" style="color:#fff;text-decoration:underline;">{{ site_url }}</a><br/>
      <span style="opacity:.75;">Sideline Conseil — Veille automatisée, sources officielles uniquement.</span>
    </td></tr>
  </table>
</body></html>""")


def build_html(rows: list[dict], site_url: str) -> tuple[str, int]:
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        matched = json.loads(r.get("matched_keywords") or "[]")
        if not matched:
            continue
        item = {
            "title": r["title"], "url": r["url"],
            "summary": r.get("summary") or "",
            "snippet": r.get("snippet") or "",
            "published_at": r.get("published_at") or "", "chamber": r.get("chamber") or "",
            "matched": matched,
        }
        buckets.setdefault(r["category"], []).append(item)

    now = datetime.now()
    html = EMAIL_TEMPLATE.render(
        date=now.strftime("%Y-%m-%d"),
        date_human=now.strftime("%A %d %B %Y").capitalize(),
        total=sum(len(v) for v in buckets.values()),
        categories=[(c, CATEGORY_LABELS[c]) for c in CATEGORY_ORDER],
        buckets=buckets,
        site_url=site_url,
    )
    return html, sum(len(v) for v in buckets.values())


def send_email(html: str, subject: str, to: str) -> bool:
    """Envoie via SMTP (variables d'environnement SMTP_HOST/PORT/USER/PASS/FROM).

    Retourne False si les credentials ne sont pas configurés.
    """
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    sender = os.environ.get("SMTP_FROM", user or "veille@sideline-conseil.fr")
    if not host or not user or not pwd:
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pwd)
        s.sendmail(sender, [to], msg.as_string())
    return True


def save_html(html: str, path: str | Path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(html, encoding="utf-8")
