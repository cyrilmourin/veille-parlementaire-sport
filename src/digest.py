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
    "publications": "Publications",
    "nominations": "Nominations",
    "agenda": "Agenda",
    "communiques": "Communiqués",
}

CATEGORY_ORDER = list(CATEGORY_LABELS.keys())

EMAIL_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"/><title>Veille parlementaire sport — {{ date }}</title></head>
<body style="font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#111;background:#f6f7fb;margin:0;padding:24px;">
  <table role="presentation" width="100%" style="max-width:720px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
    <tr><td style="padding:20px 28px;background:#1F3A8C;color:#fff;">
      <div style="font-size:12px;letter-spacing:2px;opacity:.8;">SIDELINE CONSEIL</div>
      <div style="font-size:22px;font-weight:700;margin-top:2px;">Veille parlementaire sport</div>
      <div style="opacity:.8;margin-top:4px;">{{ date_human }} — {{ total }} nouveauté{{ 's' if total > 1 else '' }}</div>
    </td></tr>
    <tr><td style="padding:24px 28px;">
    {% if total == 0 %}
      <p style="margin:0;color:#6b7280;">Aucun nouvel item sur les 24 dernières heures. Les sources ont bien été collectées ;
      la veille reste active sur <a href="{{ site_url }}" style="color:#1F3A8C;">{{ site_url }}</a>.</p>
    {% endif %}
    {% for cat, label in categories %}
      {% if cat in buckets %}
      <h2 style="font-size:14px;text-transform:uppercase;letter-spacing:1.5px;color:#1F3A8C;margin:24px 0 12px;border-bottom:1px solid #e5e7eb;padding-bottom:6px;">
        {{ label }} <span style="color:#9ca3af;">({{ buckets[cat]|length }})</span>
      </h2>
      {% for it in buckets[cat] %}
        <div style="margin-bottom:14px;">
          <a href="{{ it.url }}" style="color:#111;font-weight:600;font-size:15px;text-decoration:none;">{{ it.title }}</a>
          <div style="color:#6b7280;font-size:12px;margin-top:2px;">
            {{ it.chamber or '' }}{% if it.published_at %} — {{ it.published_at[:10] }}{% endif %}
          </div>
          {% if it.summary %}<div style="color:#374151;font-size:13px;margin-top:6px;">{{ it.summary }}</div>{% endif %}
          {% if it.matched %}
          <div style="margin-top:6px;">
            {% for kw in it.matched %}
            <span style="display:inline-block;background:#eef2ff;color:#1F3A8C;font-size:11px;padding:2px 8px;border-radius:10px;margin-right:4px;">{{ kw }}</span>
            {% endfor %}
          </div>
          {% endif %}
        </div>
      {% endfor %}
      {% endif %}
    {% endfor %}
    </td></tr>
    <tr><td style="padding:16px 28px;background:#f9fafb;color:#6b7280;font-size:12px;text-align:center;">
      Consulter la veille complète : <a href="{{ site_url }}" style="color:#1F3A8C;">{{ site_url }}</a><br/>
      Sideline Conseil — Veille automatisée, sources officielles uniquement.
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
            "title": r["title"], "url": r["url"], "summary": r.get("summary") or "",
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
