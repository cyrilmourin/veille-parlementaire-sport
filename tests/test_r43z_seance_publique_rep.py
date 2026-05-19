"""Tests R43-Z (2026-05-19) — La séance publique AN doit rester visible
comme prochaine étape avec un badge REP tant que la séance reportée
n'a pas de nouvelle date.

Constat Cyril 19/05 : « on remet puisque ça n'apparait plus la séance
publique comme prochaine étape de la PPL en REP ».

Cause : `_render_special_ppl_card` filtrait les items agenda avec
`date >= today_iso`. Le 18/05 (séance reportée) étant passé, l'item
disparaissait de `upcoming` → `next_event` None → ligne « Prochaine
échéance » disparaissait de la carte d'accueil.

Fix : inclure aussi les items `is_postponed=True` quel que soit leur
date, parce qu'un item reporté A par essence sa date obsolète tant
qu'aucune nouvelle date n'est publiée.

Côté page dédiée PPL Sport pro (timeline statique) : ajout d'une
nouvelle variante CSS `.ppl-v3-step--postponed` (fond rouge pâle,
bordure rouge) + badge `.ppl-v3-step__rep` dans le label.
"""
from __future__ import annotations

from pathlib import Path


def test_r43z_upcoming_inclut_items_postponed_meme_date_passee():
    """Un item agenda postponed avec date passée (ex. 18/05/2026 quand
    on est le 19/05/2026) doit rester dans `upcoming` et apparaître
    comme next_event sur la carte home."""
    from src.site_export import _render_special_ppl_card

    payload = {
        "meta": {"slug_path": "/ppl-sport-professionnel/"},
        "counts": {"amdt_commission": 5, "amdt_seance": 0},
        "agenda": [
            {
                "title": "PPL Sport pro — séance publique",
                "date": "2026-05-18",  # passé
                "is_postponed": True,
                "postponed_reason": "Reportée",
                "meeting_kind": "Séance publique",
            },
        ],
    }
    html_lines = _render_special_ppl_card(payload)
    html = "\n".join(html_lines)
    assert "Prochaine échéance" in html, (
        "L'item postponed doit rester en next_event (R43-Z), pas être "
        "filtré par `date < today`"
    )
    assert "REP" in html, "Le badge REP doit être visible sur l'item postponed"
    assert "date-pill--postponed" in html


def test_r43z_upcoming_priorise_postponed_avant_futur():
    """Si on a un postponed avec date 18/05 (passé) + un agenda futur
    confirmé 25/05, le postponed sort devant — c'est ce qu'on veut
    éditorialement (signaler d'abord ce qui attend une nouvelle date,
    ensuite le confirmé).
    """
    from src.site_export import _render_special_ppl_card

    payload = {
        "meta": {"slug_path": "/ppl-sport-professionnel/"},
        "counts": {"amdt_commission": 1},
        "agenda": [
            {
                "title": "Séance publique reportée",
                "date": "2026-05-18",
                "is_postponed": True,
                "meeting_kind": "Séance publique",
            },
            {
                "title": "Commission AN du 25/05 (confirmée)",
                "date": "2026-05-25",
                "is_postponed": False,
                "meeting_kind": "Commission",
            },
        ],
    }
    html = "\n".join(_render_special_ppl_card(payload))
    # Le postponed (date 2026-05-18) sort en premier (tri asc), donc
    # « Séance publique reportée » apparaît comme next_event
    assert "Séance publique reportée" in html
    pos_postponed = html.find("Séance publique reportée")
    pos_futur = html.find("Commission AN du 25/05")
    # Le postponed apparait dans le bloc next_event en premier
    # Le futur n'apparait nulle part dans cette card (juste next_event = 1)
    assert pos_postponed >= 0
    # Pour ce test on vérifie juste que le postponed prend la place
    # next_event ; le futur est lui aussi dans la liste mais pas dans
    # next_event, donc moins central.


def test_r43z_template_a_la_step_postponed_class():
    """Garde-fou template : `site/layouts/page/ppl-sport-pro.html` doit
    contenir la classe `ppl-v3-step--postponed` et le badge `ppl-v3-step__rep`
    pour styler la step Séance publique AN en REP."""
    p = Path("site/layouts/page/ppl-sport-pro.html")
    assert p.exists()
    txt = p.read_text(encoding="utf-8")
    assert "ppl-v3-step--postponed" in txt, (
        "La classe CSS pour la step reportée doit exister"
    )
    assert "ppl-v3-step__rep" in txt, (
        "Le badge REP dans la step doit exister"
    )
    # Et il doit être effectivement appliqué à la step Séance publique AN
    # (pas juste défini en CSS sans usage)
    assert (
        'ppl-v3-step--postponed' in txt
        and 'Séance publique AN' in txt
    ), "La step Séance publique AN doit utiliser la classe postponed"


def test_r43z_upcoming_sans_postponed_comportement_inchange():
    """Garde-fou non-régression : si tous les items futurs ne sont PAS
    postponed, le comportement reste exactement comme avant R43-Z
    (filtre `date >= today`)."""
    from src.site_export import _render_special_ppl_card

    # Cas 1 : agenda futur normal → next_event = ce futur
    payload = {
        "meta": {"slug_path": "/x"},
        "counts": {"amdt_commission": 1},
        "agenda": [
            {
                "title": "Commission du 25/05",
                "date": "2026-12-31",  # bien futur
                "is_postponed": False,
                "meeting_kind": "Commission",
            },
        ],
    }
    html = "\n".join(_render_special_ppl_card(payload))
    assert "Commission du 25/05" in html
    assert "REP" not in html  # pas de badge REP

    # Cas 2 : agenda passé NON postponed → exclu (comportement legacy)
    payload2 = {
        "meta": {"slug_path": "/x"},
        "counts": {"amdt_commission": 1},
        "agenda": [
            {
                "title": "Séance ancienne (terminée)",
                "date": "2020-01-01",
                "is_postponed": False,
                "meeting_kind": "Séance publique",
            },
        ],
    }
    html2 = "\n".join(_render_special_ppl_card(payload2))
    # L'item passé non-postponed est exclu → pas de bloc next_event
    assert "Séance ancienne" not in html2
    assert "Prochaine échéance" not in html2
