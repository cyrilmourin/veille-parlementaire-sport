"""Tests ciblés sur `_normalize_rows` côté questions Sénat
(src/sources/senat.py).

Régressions couvertes :

* R23-D2 (2026-04-23) — le parser Sénat expose le CORPS de la question
  via `raw.texte_question`, clé stable utilisée par site_export pour
  construire le snippet depuis le vrai texte (et non depuis les
  métadonnées préfixées du summary).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.sources.senat import _normalize_rows  # noqa: E402


def _row(*, texte="Corps de question sportive."):
    """Ligne CSV Sénat minimale exploitable par le parser questions."""
    return {
        "Numéro": "12345S",
        "Titre": "Gouvernance du sport",
        "Texte": texte,
        "Rubrique": "sports",
        "Civilité": "M.",
        "Prénom": "Jean",
        "Nom": "Dupont",
        "Groupe": "Les Républicains",
        "Ministère de dépôt": "Sports",
        "Date de publication JO": "2026-04-10",
        "URL Question": "http://www.senat.fr/basile/visio.do?id=qSEQ26041234S",
    }


def test_senat_parser_exposes_texte_question():
    """Le parser Sénat doit persister `raw.texte_question` depuis la
    colonne CSV `Texte` — utilisé côté site_export comme haystack du
    snippet pour les questions matchées."""
    src = {"id": "senat_questions", "category": "questions"}
    items = list(_normalize_rows(src, [_row()]))
    assert len(items) == 1
    raw = items[0].raw
    assert raw["texte_question"] == "Corps de question sportive."


def test_senat_parser_texte_question_absent_when_empty():
    """Colonne `Texte` vide → la clé `texte_question` n'est PAS ajoutée
    (le fallback site_export retombera sur le summary)."""
    src = {"id": "senat_questions", "category": "questions"}
    items = list(_normalize_rows(src, [_row(texte="")]))
    raw = items[0].raw
    assert "texte_question" not in raw


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
