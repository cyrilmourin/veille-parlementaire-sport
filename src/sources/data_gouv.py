"""Connecteur data.gouv.fr — agendas publics ministériels en open data.

Contexte (R15, 2026-04-22) : les sites ministériels officiels
(gouvernement.fr, info.gouv.fr, sante.gouv.fr, education.gouv.fr,
interieur.gouv.fr, economie.gouv.fr) sont protégés par Cloudflare
challenge JS ou WAF F5 ASM (cf. audit §1.3-§1.5 dans
`docs/AGENDA_SOURCES_AUDIT.md`). Impossible d'y accéder en HTTP direct
depuis la sandbox / CI.

Contournement : certains ministères publient leur agenda public en
open data sur data.gouv.fr, avec des ressources CSV/JSON exposées via
OpenDataSoft (schéma iCal-like : `uid`, `summary`, `dtstart`, `dtend`,
`description`). Format `data_gouv_agenda` ici route vers ces
ressources — 1 source YAML = 1 dataset.

Sources concrètes identifiées (2026-04-22) :
- Ministère de l'Enseignement supérieur, de la Recherche et de
  l'Innovation : dataset `fr-esr-agenda-ministre` (4788 entrées,
  màj hebdomadaire, format iCal JSON).

Pour Matignon / info.gouv.fr : aucun dataset open data équivalent
trouvé. À réactiver via fallback Playwright si besoin (audit §4).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta

from ..models import Item
from ._common import fetch_text, parse_iso

log = logging.getLogger(__name__)


def fetch_source(src: dict) -> list[Item]:
    """Route les sources data.gouv.fr selon leur format.

    Supports :
    - `data_gouv_agenda` : endpoint JSON exposant une liste d'events
      iCal-like (`uid`, `summary`, `dtstart`, `dtend`, `description`,
      optionnel `agenda` pour le nom du ministre en poste).
    """
    fmt = src.get("format")
    if fmt == "data_gouv_agenda":
        return _fetch_agenda_json(src)
    log.warning("data_gouv : format %r non géré pour %s", fmt, src.get("id"))
    return []


def _fetch_agenda_json(src: dict) -> list[Item]:
    """Fetch un endpoint JSON iCal-like et normalise en Item.

    Paramètres YAML :
        url          : endpoint JSON (obligatoire)
        id           : source_id Follaw (obligatoire)
        category     : catégorie Follaw (défaut `agenda`)
        chamber      : label chambre pour le digest (ex. `MinESR`)
        since_days   : fenêtre glissante (défaut 90j) — filtre `dtstart`
                        avant J-since_days pour éviter de recharger des
                        milliers d'events historiques à chaque run
        title_prefix : optionnel, ex. `MinESR —` ajouté au début du titre
    """
    sid = src["id"]
    url = src["url"]
    cat = src.get("category", "agenda")
    chamber = src.get("chamber")
    since_days = int(src.get("since_days", 90))
    title_prefix = src.get("title_prefix", "")

    try:
        payload = fetch_text(url)
    except Exception as e:
        log.warning("data_gouv %s : fetch KO %s : %s", sid, url, e)
        return []

    try:
        entries = json.loads(payload)
    except json.JSONDecodeError as e:
        log.warning("data_gouv %s : JSON invalide (%s)", sid, e)
        return []

    if not isinstance(entries, list):
        log.warning("data_gouv %s : schéma inattendu (type=%s)",
                    sid, type(entries).__name__)
        return []

    # Fenêtre glissante naïve (UTC). 0 → pas de filtre.
    cutoff = None
    if since_days > 0:
        cutoff = datetime.utcnow() - timedelta(days=since_days)

    items: list[Item] = []
    skipped_date = 0
    skipped_bad = 0

    for entry in entries:
        if not isinstance(entry, dict):
            skipped_bad += 1
            continue
        uid = str(entry.get("uid") or "").strip()
        # Schéma variable selon les ministères :
        # - ESR (Enseignement sup.) expose `summary` (+ `agenda` = nom ministre).
        # - EN (Éducation nationale) n'expose QUE `description` — pas de
        #   summary ni agenda. On accepte les deux comme contenu principal.
        summary = str(entry.get("summary") or "").strip()
        description = str(entry.get("description") or "").strip()
        content = summary or description
        dtstart_raw = entry.get("dtstart")
        if not uid or not content:
            skipped_bad += 1
            continue

        dt = parse_iso(dtstart_raw) if dtstart_raw else None
        if cutoff and dt and dt < cutoff:
            skipped_date += 1
            continue

        agenda_owner = str(entry.get("agenda") or "").strip()

        # Titre : "{title_prefix}{content} ({agenda_owner})" — l'owner
        # (nom du ministre) aide à désambiguïser côté digest lorsque
        # plusieurs ministres se succèdent sur un même portefeuille.
        # Seulement ajouté si le schéma l'expose (EN ne l'expose pas).
        title = content
        if agenda_owner and agenda_owner.lower() not in title.lower():
            title = f"{title} ({agenda_owner})"
        if title_prefix:
            title = f"{title_prefix} {title}"
        title = title[:220]

        # URL item : pas d'URL canonique par event dans l'API → on
        # pointe vers la ressource source (utile pour audit / preuve).
        item_url = url
        # Hash court pour garder uid stable même si la source change de
        # format de uid (défense contre un upstream qui passerait de
        # "52206" à "uid://foo/52206").
        uid_short = hashlib.sha1(uid.encode("utf-8")).hexdigest()[:16]

        items.append(Item(
            source_id=sid,
            uid=uid_short,
            category=cat,
            chamber=chamber,
            title=title,
            url=item_url,
            published_at=dt,
            # summary Item = description si riche, sinon content court
            summary=(description if description and description != content
                     else content)[:2000],
            raw={
                "path": "data_gouv:agenda",
                "upstream_uid": uid,
                "agenda_owner": agenda_owner,
                "dtstart": dtstart_raw,
                "dtend": entry.get("dtend"),
            },
        ))

    log.info(
        "data_gouv %s : %d items normalisés (sur %d entrées, %d hors fenêtre, %d invalides)",
        sid, len(items), len(entries), skipped_date, skipped_bad,
    )
    return items
