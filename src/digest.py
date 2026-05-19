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
    {{ health_block|safe }}
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


def build_html(
    rows: list[dict],
    site_url: str,
    health_block: str = "",
) -> tuple[str, int]:
    """Construit l'email HTML. `health_block` (R29) est du HTML pré-rendu
    injecté en tête du contenu, vide par défaut pour ne pas polluer les
    mails quotidiens où tout va bien. Produit par `monitoring.render_digest_block`.
    """
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
        # R43-S.b (2026-05-19) — Tolérance str JSON vs list Python déjà
        # parsée. Avant R43-S, `rows` venait de `store.fetch_matched_since`
        # qui retournait des rows SQLite avec `matched_keywords` en TEXT
        # (string JSON). Depuis R43-S, `rows` vient de
        # `site_export.filtered_rows` qui passe par `_load(rows)` →
        # `matched_keywords` est désormais une liste Python parsée.
        # Crash du run daily 18/05 21:14 : `TypeError: the JSON object
        # must be str, bytes or bytearray, not list`. Le `digest.build_html`
        # doit accepter les 2 formats pour absorber proprement la
        # transition d'architecture sans coupler `digest` à `site_export`.
        mk = r.get("matched_keywords")
        if isinstance(mk, list):
            matched = mk
        elif mk:
            try:
                matched = json.loads(mk)
            except (TypeError, json.JSONDecodeError):
                matched = []
        else:
            matched = []
        if not matched:
            continue
        # Statut procédural (dossiers législatifs) — extrait de raw, alimenté
        # par assemblee._normalize_dosleg.
        # R43-S.b — Idem `raw` peut être dict déjà parsé (post-site_export)
        # ou string JSON (legacy/DB direct). Défense double.
        raw_in = r.get("raw")
        if isinstance(raw_in, dict):
            raw = raw_in
        elif raw_in:
            try:
                raw = json.loads(raw_in)
            except Exception:
                raw = {}
        else:
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
        health_block=health_block,
    )
    return html, sum(len(v) for v in buckets.values())


def send_email(html: str, subject: str, to: str) -> bool:
    """Envoie via SMTP (variables d'environnement SMTP_HOST/PORT/USER/PASS/FROM).

    Retourne False si les credentials ne sont pas configurés.

    R42-DA (2026-05-16) — Écrit aussi un fichier `data/email_status.json`
    pour diagnostiquer depuis le repo (sans accès aux logs GHA gatés
    derrière auth). Cyril 2026-05-16 : « depuis des semaines je ne
    reçois de mail qu'en cas d'échec d'un run, jamais sinon ». Symptôme :
    le digest tombe silencieusement parce que `send_email` retourne
    False (credentials manquants) ou lève une exception non capturée.
    Pattern de diag inspiré de R42-CB (`min_sports_debug.json`).
    """
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    sender = os.environ.get("SMTP_FROM", user or "veille@sideline-conseil.fr")
    # R43-O (2026-05-18) — BCC optionnel via secret GHA `DIGEST_BCC`. Cyril
    # ne reçoit plus le digest sur `cyrilmourin@sideline-conseil.fr` depuis
    # plusieurs semaines alors que `s.sendmail` retourne sans exception
    # (cf. data/email_status.json : 10+ envois consécutifs en `stage=ok`).
    # Hypothèses : DMARC strict OVH, filtre anti-spam OVH ou boîte saturée.
    # Le BCC permet de comparer la livraison OVH vs Gmail SANS modifier
    # `DIGEST_TO` (donc sans toucher au flux email existant). Le destinataire
    # BCC est ajouté UNIQUEMENT dans l'enveloppe SMTP (`sendmail()` 2e arg)
    # — il n'apparaît pas dans les headers `To:` / `Cc:` du message visible.
    # Si `DIGEST_BCC` est vide, comportement strictement inchangé.
    bcc_raw = (os.environ.get("DIGEST_BCC") or "").strip()
    # Support multi-destinataires séparés par virgule (futur-proof).
    bcc_list = [b.strip() for b in bcc_raw.split(",") if b.strip()] if bcc_raw else []
    # Bool de présence par secret — diag ciblé sans jamais exposer la valeur.
    missing = [
        name for name, val in (
            ("SMTP_HOST", host),
            ("SMTP_USER", user),
            ("SMTP_PASS", pwd),
        ) if not val
    ]
    if missing:
        _write_email_status(
            stage="not_configured",
            missing=missing,
            to=to or "",
            host=host or "",
            sender=sender or "",
            bcc_count=len(bcc_list),
        )
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        msg.attach(MIMEText(html, "html", "utf-8"))
        envelope_recipients = [to] + bcc_list
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            s.login(user, pwd)
            s.sendmail(sender, envelope_recipients, msg.as_string())
    except Exception as e:
        # On capture, on diagnostique, on relève — le run échoue, GHA notifie
        # « workflow failed » et Cyril sait qu'il y a un problème SMTP.
        # Sans cette branche, l'exception passait inchangée mais aucun fichier
        # diag ne traçait l'erreur dans le repo (cf. R42-DA).
        _write_email_status(
            stage="send_failed",
            error_type=type(e).__name__,
            error_msg=str(e)[:300],
            to=to or "",
            host=host or "",
            sender=sender or "",
            bcc_count=len(bcc_list),
        )
        raise
    _write_email_status(
        stage="ok",
        to=to or "",
        host=host or "",
        sender=sender or "",
        subject_len=len(subject or ""),
        html_len=len(html or ""),
        bcc_count=len(bcc_list),
    )
    return True


def _write_email_status(**kwargs) -> None:
    """R42-DA (2026-05-16) — Snapshot diagnostic d'envoi mail dans
    `data/email_status.json`. Permet à Cyril de vérifier depuis le
    repo (post-push veille-bot) si le digest est bien parti. Stages :
    `ok` (envoyé), `not_configured` (secrets manquants),
    `send_failed` (SMTP a levé). N'expose JAMAIS de valeurs sensibles
    (password, contenu du mail) — uniquement les noms de secrets
    manquants et le type d'erreur."""
    from datetime import datetime as _dt
    import json as _json
    from pathlib import Path as _Path
    payload = {
        "ts": _dt.utcnow().isoformat(timespec="seconds") + "Z",
        **kwargs,
    }
    out = _Path("data/email_status.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")


def save_html(html: str, path: str | Path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(html, encoding="utf-8")
