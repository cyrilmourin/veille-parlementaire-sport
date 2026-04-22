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
    # R17 (2026-04-22) : l'ordre de ce dict est la source de vérité pour
    # l'affichage accueil (`CATEGORY_ORDER`). Il doit rester aligné sur
    # le menu de navigation (`site/layouts/partials/header.html`) sur
    # demande Cyril — cohérence UX entre la home et les pages dédiées.
    # Ordre menu : Dossiers / Amendements / Questions / CR / Agenda /
    # JORF / Nominations / Publications.
    "dossiers_legislatifs": "Dossiers législatifs",
    "amendements": "Amendements",
    "questions": "Questions",
    "comptes_rendus": "Comptes rendus",
    "agenda": "Agenda",
    # R13-G (2026-04-21) : "Journal Officiel" — plus lisible que le sigle JORF
    # dans les sommaires pliables du site. Les items gardent category="jorf"
    # côté DB, c'est uniquement un libellé d'affichage.
    "jorf": "Journal Officiel",
    "nominations": "Nominations",
    "communiques": "Publications",
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
        {%- set ch_color = "#5c6577" -%}
        {%- if it.chamber == "AN" -%}{%- set ch_color = "#20acd9" -%}{%- endif -%}
        {%- if it.chamber in ("Senat", "Sénat") -%}{%- set ch_color = "#62c925" -%}{%- endif -%}
        <div style="margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #f1ead0;">
          {% if it.url %}<a href="{{ it.url }}" style="color:#122549;font-weight:600;font-size:15px;text-decoration:none;">{{ it.title }}</a>{% else %}<span style="color:#122549;font-weight:600;font-size:15px;">{{ it.title }}</span>{% endif %}
          <div style="color:#5c6577;font-size:12px;margin-top:4px;line-height:1.7;">
            {% if it.chamber %}<span style="display:inline-block;background:{{ ch_color }};color:#fff;padding:1px 7px;border-radius:4px;font-size:10.5px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;margin-right:5px;">{{ it.chamber }}</span>{% endif %}
            {% if it.status_label %}
              {%- set st_bg = "#c9c2a6" -%}
              {%- set st_fg = "#122549" -%}
              {%- if it.is_promulgated -%}{%- set st_bg = "#66A266" -%}{%- set st_fg = "#ffffff" -%}{%- endif -%}
              <span style="display:inline-block;background:{{ st_bg }};color:{{ st_fg }};padding:1px 7px;border-radius:4px;font-size:10.5px;font-weight:600;letter-spacing:.3px;margin-right:5px;">{{ it.status_label }}</span>
            {% endif %}
            {% if it.published_at %}<span style="color:#DA4431;font-weight:700;font-variant-numeric:tabular-nums;">{{ it.published_at[:10] }}</span>{% endif %}
            {% if it.matched %}
              {% for kw in it.matched[:12] %}{% if not loop.first %}<span style="color:#9ca3af;"> | </span>{% endif %}<span style="font-style:italic;font-weight:700;font-size:12px;color:inherit;">{{ kw }}</span>{% endfor %}
            {% endif %}
          </div>
          {% if it.snippet %}
          <div style="color:#5c6577;font-size:13px;margin-top:8px;padding:8px 12px;border-left:3px solid #DA4431;background:#fff8ea;font-style:italic;">« {{ it.snippet }} »</div>
          {% elif it.summary %}
          <div style="color:#374151;font-size:13px;margin-top:6px;">{{ it.summary[:280] }}{% if it.summary|length > 280 %}…{% endif %}</div>
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
    # UX-E : le schéma SQL n'a jamais persisté `snippet` (cf. store.SCHEMA),
    # donc r.get("snippet") est toujours vide en lecture DB. On rebuild à
    # la volée depuis summary, comme côté site_export. Idempotent.
    from .keywords import KeywordMatcher
    from .site_export import (
        SNIPPET_LEN_BY_CATEGORY,
        _fix_agenda_row, _fix_cr_row, _fix_dossier_row, _fix_question_row,
    )
    _matcher = KeywordMatcher("config/keywords.yml")

    buckets: dict[str, list[dict]] = {}
    for r in rows:
        matched = json.loads(r.get("matched_keywords") or "[]")
        if not matched:
            continue
        # Statut procédural (dossiers législatifs) — extrait de raw, alimenté
        # par assemblee._normalize_dosleg.
        try:
            raw = json.loads(r.get("raw") or "{}")
        except Exception:
            raw = {}
        status_label = (raw.get("status_label") or "").strip()
        # On retire le préfixe d'institution redondant avec le badge chambre
        for prefix in ("AN · ", "Senat · ", "Sénat · "):
            if status_label.startswith(prefix):
                status_label = status_label[len(prefix):]
                break
        # R12a (2026-04-21) : même fixups qu'à l'export site pour que le
        # digest email soit cohérent avec /items/ (titres CR lisibles,
        # auteurs questions résolus, préfixe "Agenda - " retiré, dossiers
        # Sénat capitalisés). Sans ça, l'email diverge du site web.
        # Le `raw` dict vient d'être parsé ci-dessus, on le ré-injecte
        # dans r pour que les fixups y accèdent proprement.
        r_proxy = dict(r)
        r_proxy["raw"] = raw
        _fix_cr_row(r_proxy)
        _fix_question_row(r_proxy)
        _fix_agenda_row(r_proxy)
        _fix_dossier_row(r_proxy)
        # Relire title/url après fixup (raw peut avoir été enrichi aussi)
        title_fixed = r_proxy.get("title") or r["title"]
        url_fixed = r_proxy.get("url") or r["url"]
        published_at_fixed = r_proxy.get("published_at") or r.get("published_at") or ""
        raw = r_proxy.get("raw") if isinstance(r_proxy.get("raw"), dict) else raw
        snippet = (r.get("snippet") or "").strip()
        if not snippet:
            haystack = (r.get("summary") or title_fixed or "").strip()
            if haystack:
                # R14 : même logique que site_export — longueur pilotée
                # par catégorie pour que les valeurs > 320 prennent effet.
                _target = SNIPPET_LEN_BY_CATEGORY.get(r.get("category") or "", 800)
                snippet = _matcher.build_snippet(haystack, max_len=_target)
        item = {
            "title": title_fixed, "url": url_fixed,
            "summary": r.get("summary") or "",
            "snippet": snippet,
            "published_at": published_at_fixed, "chamber": r.get("chamber") or "",
            "matched": matched,
            "status_label": status_label,
            "is_promulgated": bool(raw.get("is_promulgated")),
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
