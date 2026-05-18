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

Le HTML envoyé est statique avec un timestamp UTC + l'ID du run GHA
(si disponible via `GITHUB_RUN_ID`) pour permettre de croiser un mail
reçu avec un run précis.

Le statut d'envoi est loggué dans `data/email_status.json` exactement
comme pour un envoi de digest réel (réutilise `send_email` tel quel),
donc le workflow GHA peut commiter ce fichier comme témoin du test.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

from src.digest import send_email


def main() -> int:
    ts_utc = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    run_url = ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo and run_id != "local":
        run_url = f"https://github.com/{repo}/actions/runs/{run_id}"

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
    try:
        ok = send_email(html, subject, to)
    except Exception as e:
        # send_email logue déjà dans email_status.json côté stage='send_failed'
        print(f"❌ send_email a levé : {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not ok:
        print("⚠ send_email a retourné False (SMTP non configuré)", file=sys.stderr)
        return 1
    print(f"✓ Email envoyé. Sujet : {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
