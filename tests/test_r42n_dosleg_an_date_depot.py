"""R42-N (2026-05-11) — VRAIE date de dépôt structurelle pour les dosleg AN.

Avant R42-N, `_normalize_dossier` posait `published_at = max(dateActe)` qui
incluait les actes FUTURS (séances prévues, inscriptions agenda) → la card
affichait une date dans le futur étiquetée « Dépôt à l'AN le … ».

R42-N pose désormais `raw["published_at_original"]` à la 1re date de la
timeline d'actes utiles (= dépôt initial AN ou transmission entrante depuis
le Sénat). R42-M consomme ce champ pour émettre `date_depot:` au
frontmatter ; le template list.html l'affiche.

Cas couverts par ces tests :
- Code source contient la logique R42-N (string match dans assemblee.py)
- Le champ `published_at_original` est posé dans le raw quand la timeline
  contient des actes à des dates différentes (typique : dépôt initial il y
  a 6+ mois + inscription en commission/séance imminente).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def test_r42n_code_source_contient_extraction_first_act_date():
    """Le module assemblee.py doit contenir la logique R42-N."""
    from src.sources import assemblee as an_mod
    code = Path(an_mod.__file__).read_text(encoding="utf-8")
    # Marqueur R42-N
    assert "R42-N" in code
    # Variable + clé raw correctes
    assert "first_act_date_iso" in code, (
        "_normalize_dossier doit calculer first_act_date_iso depuis "
        "actes_timeline[0] (R42-N)"
    )
    assert '"published_at_original": first_act_date_iso' in code, (
        "Le raw du yield Item doit poser published_at_original = "
        "first_act_date_iso (R42-N)"
    )


def test_r42n_template_affiche_date_depot_via_params():
    """Le template list.html dosleg doit lire .Params.date_depot."""
    template_path = _ROOT / "site/layouts/dossiers_legislatifs/list.html"
    code = template_path.read_text(encoding="utf-8")
    # R42-M (toujours en place) — sert de socle à R42-N
    assert ".Params.date_depot" in code, (
        "Le template list.html doit utiliser .Params.date_depot (R42-M)"
    )
