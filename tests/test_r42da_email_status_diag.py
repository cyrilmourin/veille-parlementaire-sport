"""R42-DA (2026-05-16) — Diagnostic SMTP via `data/email_status.json`.

Cyril 2026-05-16 : « depuis des semaines je ne reçois de mail qu'en cas
d'échec d'un run, jamais sinon ». Le pipeline tourne normalement (les
commits `chore: snapshot digest` confirment qu'il complete chaque jour)
mais le digest n'arrive plus. Symptôme : `send_email` retourne False
silencieusement parce que SMTP_HOST / SMTP_USER / SMTP_PASS sont
manquants ou cassés dans les secrets GHA — sans aucune trace dans le
repo, impossible à diagnostiquer.

Fix : `send_email` écrit désormais `data/email_status.json` à chaque
appel (stages : `ok`, `not_configured`, `send_failed`). Le fichier est
commité par daily.yml. Cyril peut vérifier le statut depuis le repo
sans accès aux logs GHA.

Garde-fou sécurité : on n'expose JAMAIS le mot de passe ni le contenu
du mail dans le fichier de diag. Uniquement noms de secrets manquants
et type d'erreur.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.digest import send_email


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_email_status_not_configured_smtp_host_manquant(
    in_tmp_cwd, monkeypatch
):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    result = send_email("<html>...</html>", "Sujet", "cyril@example.com")
    assert result is False
    status_file = Path("data/email_status.json")
    assert status_file.exists()
    data = json.loads(status_file.read_text())
    assert data["stage"] == "not_configured"
    assert "SMTP_HOST" in data["missing"]
    assert data["to"] == "cyril@example.com"


def test_email_status_not_configured_smtp_pass_manquant(
    in_tmp_cwd, monkeypatch
):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.delenv("SMTP_PASS", raising=False)
    result = send_email("<html>...</html>", "Sujet", "cyril@example.com")
    assert result is False
    data = json.loads(Path("data/email_status.json").read_text())
    assert data["stage"] == "not_configured"
    assert data["missing"] == ["SMTP_PASS"]


def test_email_status_n_expose_pas_le_password(in_tmp_cwd, monkeypatch):
    """SÉCURITÉ : le fichier de diag ne doit JAMAIS contenir la valeur
    du mot de passe SMTP. Critique : il est commité dans le repo public."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv("SMTP_PASS", "TR0P_SECRET_2026!")
    # Pas de host accessible → l'envoi échouera (mais on capture l'erreur)
    try:
        send_email("<html>...</html>", "Sujet", "cyril@example.com")
    except Exception:
        pass
    raw = Path("data/email_status.json").read_text()
    assert "TR0P_SECRET_2026" not in raw, (
        "Le mot de passe SMTP ne doit JAMAIS apparaître dans le fichier "
        "de diag (commité dans le repo public)."
    )


def test_email_status_n_expose_pas_le_html_du_mail(in_tmp_cwd, monkeypatch):
    """Le contenu du mail digest peut contenir des keywords sport, des
    libellés d'amendements, etc. Pas de raison de l'exposer dans le diag."""
    monkeypatch.delenv("SMTP_HOST", raising=False)
    secret_html = "<html><body>Contenu confidentiel SECRET</body></html>"
    send_email(secret_html, "Sujet", "cyril@example.com")
    raw = Path("data/email_status.json").read_text()
    assert "SECRET" not in raw
    assert "Contenu confidentiel" not in raw


def test_email_status_send_failed_capture_erreur(in_tmp_cwd, monkeypatch):
    """SMTP configuré mais host inaccessible → l'exception est levée
    après écriture du fichier de diag. Type d'erreur loggué."""
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid.local.xxxxxxx")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    with pytest.raises(Exception):
        send_email("<html>...</html>", "Sujet", "cyril@example.com")
    data = json.loads(Path("data/email_status.json").read_text())
    assert data["stage"] == "send_failed"
    assert "error_type" in data
    assert "error_msg" in data
    # Le type d'erreur est en clair (utile pour diag)
    assert data["error_type"]


def test_email_status_payload_contient_timestamp(in_tmp_cwd, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    send_email("<html>...</html>", "Sujet", "to@example.com")
    data = json.loads(Path("data/email_status.json").read_text())
    assert "ts" in data
    assert data["ts"].endswith("Z")
