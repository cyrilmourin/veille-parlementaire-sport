"""Tests R43-O (2026-05-18) — BCC optionnel sur le digest email.

Cyril 2026-05-18 : « toujours aucun email reçu ». Le pipeline confirme
`stage=ok` à chaque envoi (cf. `data/email_status.json` : 10+ envois
consécutifs en ok depuis le 16/05). Trois hypothèses : DMARC strict OVH,
filtre anti-spam OVH, boîte saturée. Cyril choisit l'option (D) :
ajouter Gmail en BCC pour comparer la livraison OVH vs Gmail sans
toucher `DIGEST_TO`.

Implémentation : `send_email` lit la variable d'env `DIGEST_BCC`.
- Si vide / non définie → comportement strictement inchangé (zéro
  régression sur l'envoi existant).
- Si définie → destinataire(s) ajouté(s) à l'enveloppe SMTP UNIQUEMENT
  (`s.sendmail(sender, [to] + bcc_list, msg)`). Le header `To:` du
  message MIME reste à `DIGEST_TO` seul → le BCC n'apparaît pas dans
  le mail visible côté OVH.
- Support multi-destinataires séparés par virgule (`a@x.com, b@y.com`).

Sécurité : le diag `email_status.json` ne logue QUE le COUNT BCC, pas
les adresses (évite la fuite d'adresses persos dans le repo public).
"""
from __future__ import annotations

import json
from email.parser import Parser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.digest import send_email


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "veille@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.setenv("SMTP_FROM", "Sideline Veille <veille@example.com>")


def test_r43o_bcc_absent_par_defaut_envoi_inchange(
    in_tmp_cwd, smtp_env, monkeypatch
):
    """Sans `DIGEST_BCC` défini : l'envoi est strictement identique à
    l'avant — un seul destinataire dans l'enveloppe SMTP, pas de header
    Bcc dans le message."""
    monkeypatch.delenv("DIGEST_BCC", raising=False)
    mock_smtp = MagicMock()
    with patch("src.digest.smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = mock_smtp
        send_email("<p>Body</p>", "Sujet", "to@example.com")
    args, _ = mock_smtp.sendmail.call_args
    sender, recipients, msg_raw = args
    assert sender == "Sideline Veille <veille@example.com>"
    assert recipients == ["to@example.com"]
    parsed = Parser().parsestr(msg_raw)
    assert parsed["To"] == "to@example.com"
    assert parsed["Bcc"] is None  # PAS de header Bcc


def test_r43o_bcc_present_ajoute_a_enveloppe_smtp(
    in_tmp_cwd, smtp_env, monkeypatch
):
    """Avec `DIGEST_BCC=gmail` : le destinataire BCC est ajouté à la
    liste d'enveloppe (2e arg de `sendmail`), mais PAS dans les headers
    To/Cc/Bcc visibles dans le mail rendu."""
    monkeypatch.setenv("DIGEST_BCC", "shadow@gmail.com")
    mock_smtp = MagicMock()
    with patch("src.digest.smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = mock_smtp
        send_email("<p>Body</p>", "Sujet", "to@example.com")
    args, _ = mock_smtp.sendmail.call_args
    _, recipients, msg_raw = args
    assert recipients == ["to@example.com", "shadow@gmail.com"]
    # Header To inchangé, header Bcc absent
    parsed = Parser().parsestr(msg_raw)
    assert parsed["To"] == "to@example.com"
    assert parsed["Bcc"] is None
    # Le destinataire BCC ne doit pas se retrouver dans le message
    # visible (pas dans le To, ni nulle part dans les headers MIME).
    assert "shadow@gmail.com" not in msg_raw


def test_r43o_bcc_multi_destinataires_separes_par_virgule(
    in_tmp_cwd, smtp_env, monkeypatch
):
    """Support `DIGEST_BCC='a@x.com, b@y.com'` → 2 destinataires BCC."""
    monkeypatch.setenv("DIGEST_BCC", "a@x.com, b@y.com , c@z.com")
    mock_smtp = MagicMock()
    with patch("src.digest.smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = mock_smtp
        send_email("<p>Body</p>", "Sujet", "to@example.com")
    _, recipients, _ = mock_smtp.sendmail.call_args[0]
    assert recipients == ["to@example.com", "a@x.com", "b@y.com", "c@z.com"]


def test_r43o_bcc_vide_ou_whitespace_traite_comme_absent(
    in_tmp_cwd, smtp_env, monkeypatch
):
    """`DIGEST_BCC=''` ou `'   '` → enveloppe SMTP avec uniquement le
    destinataire principal (pas de string vide injectée dans la liste)."""
    monkeypatch.setenv("DIGEST_BCC", "   ")
    mock_smtp = MagicMock()
    with patch("src.digest.smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = mock_smtp
        send_email("<p>Body</p>", "Sujet", "to@example.com")
    _, recipients, _ = mock_smtp.sendmail.call_args[0]
    assert recipients == ["to@example.com"]


def test_r43o_diag_log_compte_bcc_pas_adresses(
    in_tmp_cwd, smtp_env, monkeypatch
):
    """SÉCURITÉ : le fichier de diag ne doit JAMAIS contenir les adresses
    BCC (évite la fuite dans le repo public). Uniquement un count."""
    monkeypatch.setenv("DIGEST_BCC", "personnel@gmail.com")
    mock_smtp = MagicMock()
    with patch("src.digest.smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = mock_smtp
        send_email("<p>Body</p>", "Sujet", "to@example.com")
    raw = Path("data/email_status.json").read_text()
    assert "personnel@gmail.com" not in raw
    data = json.loads(raw)
    assert data.get("bcc_count") == 1
    assert data["stage"] == "ok"


def test_r43o_diag_bcc_count_dans_send_failed(
    in_tmp_cwd, monkeypatch
):
    """Le `bcc_count` doit être loggué même en cas d'échec d'envoi
    (utile pour confirmer que la conf BCC est bien lue par le runtime
    avant le crash réseau)."""
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid.local.xxxxxxx")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "u@example.com")
    monkeypatch.setenv("SMTP_PASS", "p")
    monkeypatch.setenv("DIGEST_BCC", "shadow@gmail.com")
    with pytest.raises(Exception):
        send_email("<p>Body</p>", "Sujet", "to@example.com")
    data = json.loads(Path("data/email_status.json").read_text())
    assert data["stage"] == "send_failed"
    assert data.get("bcc_count") == 1
