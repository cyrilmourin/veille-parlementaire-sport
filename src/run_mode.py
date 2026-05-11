"""R42-AD (2026-05-11) — Mode d'exécution du pipeline : nominal vs full.

Origine : Cyril a remonté que les runs nominaux quotidiens duraient ~28 min
alors que la plupart du travail (fetch des textes intégraux des ~2500
dossiers législatifs AN via R42-X `/dyn/opendata/`, ~300 textes Sénat
via R42-L `/leg/`, ~30 PDF rapports via R42-B/Q) est redondant : ces
items sont déjà en DB et `upsert_many` ne refresh pas leur `raw.*` à
hash_key constant. Le matching keyword stocké est préservé.

**Esprit Cyril** : reset périodique (workflow_dispatch avec reset_category
ou reset_db) ré-ingère TOUT l'historique sur la fenêtre LARGE (3 ans). Les
runs nominaux quotidiens scannent UNIQUEMENT les items récents (90j) pour
capter les actualisations (nouvel acte, transmission AN↔Sénat, etc.).

Détection du mode :
- env var `RUN_MODE=full` → fenêtres larges (reset)
- env var `RUN_MODE=nominal` (ou non-défini) → fenêtres courtes
- Le workflow `daily.yml` set `RUN_MODE=full` automatiquement quand
  `reset_category` ou `reset_db=1` est passé en workflow_dispatch.

Préservation des actualisations :
- Les anciens items restent en DB et continuent d'être exposés via
  `_filter_window` côté site_export (fenêtre 1095j inchangée).
- Quand un dossier ancien reçoit un nouvel acte (ex. PPL transmise au
  Sénat 1 an après son dépôt AN), son `last_date` = max(dateActe) se
  met à jour automatiquement → il rentre dans la fenêtre nominale 90j
  au run suivant → re-traité, re-matché, re-fetché texte intégral.
- Si on ajoute un keyword au yaml entre 2 runs, les anciens en DB ne
  sont PAS re-matchés (comportement actuel). Solution : reset périodique
  (manuel ou cron hebdomadaire).
"""
from __future__ import annotations

import os


def is_full_mode() -> bool:
    """True si on est en mode 'full' (reset) — fenêtres larges.

    Mode déterminé par env var `RUN_MODE` :
    - `full` → True (reset)
    - autre / non-défini → False (nominal)
    """
    return (os.environ.get("RUN_MODE") or "nominal").lower() == "full"


def window_days(nominal: int, full: int) -> int:
    """Retourne la fenêtre en jours selon le mode courant.

    Usage :
        max_age = window_days(nominal=90, full=1095)

    En mode `full` (reset), retourne `full`. Sinon `nominal`.
    """
    return full if is_full_mode() else nominal
