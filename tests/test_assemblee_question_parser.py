"""Tests ciblés sur `_normalize_question` (src/sources/assemblee.py).

Régressions couvertes :

* R23-D2 (2026-04-23) — le parser expose le CORPS de la question dans
  `raw.texte_question`. Avant R23-D2, le corps était noyé dans `summary`
  au milieu de métadonnées (`Destinataire : X — Rubrique : Y — Analyse :
  Z — <texte>`) ; le matcher de snippet tombait souvent sur un match
  dans le préfixe `Rubrique : sports` et rendait un extrait des
  métadonnées au lieu du texte réel.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.sources.assemblee import _normalize_question  # noqa: E402


def _base_question(*, texte: str = "Corps de la question", analyse: str = "",
                    rubrique: str = "sports"):
    """Fabrique un JSON de question AN minimal et exerçable par le parser."""
    return {
        "question": {
            "uid": "QANR5L17QE14517",
            "indexQuestion": "14517",
            "auteur": {"identite": {"acteurRef": "PA123456"}},
            "indexationAN": {
                "rubrique": rubrique,
                "teteAnalyse": "",
                "analyses": {"analyse": analyse},
            },
            "textesQuestion": {"texteQuestion": {"texte": texte,
                                                   "infoJO": {"dateJO": "2026-04-21"}}},
            "minInt": {"abrege": "Sports"},
        }
    }


def _run(obj):
    src = {"id": "an_questions"}
    with patch("src.sources.assemblee.amo_loader.resolve_acteur",
               return_value="Mme Exemple"), \
         patch("src.sources.assemblee.amo_loader.resolve_groupe",
               return_value="GROUP"), \
         patch("src.sources.assemblee.amo_loader.resolve_groupe_ref",
               return_value=""), \
         patch("src.sources.assemblee.amo_loader.resolve_organe",
               return_value=""):
        return list(_normalize_question(obj, src, "questions"))


# ---------- R23-D2 : raw.texte_question persiste le corps de la question ----

def test_parser_persists_texte_question_from_body():
    """`raw.texte_question` doit contenir le corps nettoyé (pas les
    métadonnées) pour que site_export construise le snippet à partir du
    texte réel."""
    corps = (
        "Mme Jeanne Martin attire l'attention de M. le ministre des sports "
        "sur la situation préoccupante des clubs amateurs de football."
    )
    obj = _base_question(texte=corps, analyse="Clubs amateurs", rubrique="sports")
    items = _run(obj)
    assert len(items) == 1
    raw = items[0].raw
    assert raw["texte_question"] == corps


def test_parser_texte_question_empty_when_no_body():
    """Question sans corps (rare) → texte_question vide (pas d'exception,
    site_export retombera sur le summary)."""
    obj = _base_question(texte="", analyse="", rubrique="sports")
    items = _run(obj)
    raw = items[0].raw
    assert raw["texte_question"] == ""


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
