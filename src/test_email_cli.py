"""R43-Q (2026-05-18) — Mini-CLI pour tester l'envoi email SMTP sans
relancer toute la pipeline (~30 sec vs ~25 min côté daily.yml).

Cas d'usage : valider rapidement un changement de `SMTP_HOST`,
`DIGEST_BCC`, ou d'autres secrets SMTP/Brevo, sans payer 25 min de
fetch + matching pour chaque essai. Cyril 2026-05-18 : « je ne
comprends toujours pas pourquoi on doit refaire à chaque fois un run
de 30 min qui reprend tout alors qu'il s'agit ici juste de tester un
email ».

Usage :
    python -m src.test_email_cli

Variables d'env requises (mêmes que send_email) :
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, DIGEST_TO
Optionnel :
    DIGEST_BCC (BCC additionnel pour comparer la livraison)
    SMTP_TEST_TIMEOUT (secondes, défaut 15) — défense contre un host
    SMTP non-répondant. `smtplib.SMTP()` sans timeout utilise le
    timeout OS qui peut être > 2h ; cf. premier essai R43-Q où
    `mail.ovh.net:587` n'a jamais répondu et a bloqué le run.

Le HTML envoyé est statique avec un timestamp UTC + l'ID du run GHA
(si disponible via `GITHUB_RUN_ID`) pour permettre de croiser un mail
reçu avec un run précis.

Le statut d'envoi est loggué dans `data/email_status.json` via les
mêmes helpers que `send_email`, donc le workflow GHA peut commiter
ce fichier comme témoin du test.

R43-Q.b (2026-05-18) — N'utilise PLUS `src.digest.send_email` mais une
copie locale qui passe un `timeout` explicite à `smtplib.SMTP()`. On
ne modifie pas `send_email` côté digest pour ne pas changer le
comportement du pipeline quotidien (timeout par défaut OK pour
ssl0.ovh.net qui répond < 1 s). Le test isolé doit pouvoir échouer
vite si un nouveau host SMTP est non-répondant.
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


def _write_status(**kwargs) -> None:
    """R43-Q.b — Copie locale de `_write_email_status` pour ne pas
    dépendre d'un import qui transitivement importe jinja2/pydantic
    inutilement."""
    payload = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        **kwargs,
    }
    Path("data").mkdir(exist_ok=True)
    Path("data/email_status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _send(html: str, subject: str, to: str, timeout: float) -> bool:
    """Envoi SMTP avec timeout explicite + support DIGEST_BCC."""
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    sender = os.environ.get("SMTP_FROM") or user or "veille@sideline-conseil.fr"
    bcc_raw = (os.environ.get("DIGEST_BCC") or "").strip()
    bcc_list = [b.strip() for b in bcc_raw.split(",") if b.strip()] if bcc_raw else []
    missing = [n for n, v in (("SMTP_HOST", host), ("SMTP_USER", user),
                              ("SMTP_PASS", pwd)) if not v]
    if missing:
        _write_status(stage="not_configured", missing=missing, to=to,
                      host=host or "", sender=sender, bcc_count=len(bcc_list))
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))
    envelope = [to] + bcc_list
    try:
        with smtplib.SMTP(host, port, timeout=timeout) as s:
            s.starttls()
            s.login(user, pwd)
            s.sendmail(sender, envelope, msg.as_string())
    except Exception as e:
        _write_status(stage="send_failed", error_type=type(e).__name__,
                      error_msg=str(e)[:300], to=to, host=host, sender=sender,
                      bcc_count=len(bcc_list), timeout_sec=timeout)
        raise
    _write_status(stage="ok", to=to, host=host, sender=sender,
                  subject_len=len(subject), html_len=len(html),
                  bcc_count=len(bcc_list), timeout_sec=timeout)
    return True


def main() -> int:
    ts_utc = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    run_url = ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo and run_id != "local":
        run_url = f"https://github.com/{repo}/actions/runs/{run_id}"
    timeout = float(os.environ.get("SMTP_TEST_TIMEOUT", "15"))

    to = (os.environ.get("DIGEST_TO") or "").strip()
    if not to:
        print("DIGEST_TO non défini — abort", file=sys.stderr)
        return 2

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><title>Test email pipeline veille</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 600px; margin: 20px auto; color: #122549;">
  <h2 style="color: #1b3b6f;">📬 Test email pipeline veille parlementaire sport</h2>
  <p>Si tu reçois ce mail, le fix SMTP fonctionne pour cette config.</p>
  <table style="border-collapse: collapse; margin-top: 16px;">
    <tr><td style="padding: 4px 12px 4px 0;"><strong>Timestamp UTC</strong></td>
        <td style="padding: 4px 0;"><code>{ts_utc}</code></td></tr>
    <tr><td style="padding: 4px 12px 4px 0;"><strong>SMTP host</strong></td>
        <td style="padding: 4px 0;"><code>{os.environ.get('SMTP_HOST', '?')}</code></td></tr>
    <tr><td style="padding: 4px 12px 4px 0;"><strong>SMTP from</strong></td>
        <td style="padding: 4px 0;"><code>{os.environ.get('SMTP_FROM', '?')}</code></td></tr>
    <tr><td style="padding: 4px 12px 4px 0;"><strong>Destinataire</strong></td>
        <td style="padding: 4px 0;"><code>{to}</code></td></tr>
    <tr><td style="padding: 4px 12px 4px 0;"><strong>Run GHA</strong></td>
        <td style="padding: 4px 0;">{run_url or run_id}</td></tr>
  </table>
  <p style="margin-top: 24px; padding: 12px; background: #f1f5f9; border-radius: 6px; font-size: 13px;">
    Email de diagnostic généré par <code>src.test_email_cli</code>.
    Voir <code>data/email_status.json</code> pour le statut côté pipeline
    (stage, bcc_count, etc.).
  </p>
</body></html>"""

    subject = f"[TEST veille parlementaire] {ts_utc}"
    print(f"→ Envoi vers {to} via {os.environ.get('SMTP_HOST', '?')}")
    print(f"  BCC count via DIGEST_BCC : "
          f"{len([b for b in (os.environ.get('DIGEST_BCC', '') or '').split(',') if b.strip()])}")
    print(f"  Timeout SMTP : {timeout}s")
    try:
        ok = _send(html, subject, to, timeout=timeout)
    except Exception as e:
        # _send logue déjà dans email_status.json côté stage='send_failed'
        print(f"❌ SMTP a levé : {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not ok:
        print("⚠ SMTP non configuré (cf. email_status.json)", file=sys.stderr)
        return 1
    print(f"✓ Email envoyé. Sujet : {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
