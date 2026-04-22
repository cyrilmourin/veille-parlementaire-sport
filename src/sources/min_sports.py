"""Connecteur ministère des Sports — agenda prévisionnel hebdomadaire.

Contexte (R15, 2026-04-22) : le ministère des Sports publie chaque
semaine un bulletin d'agenda prévisionnel de la ministre
(actuellement Marina Ferrari) sur son site Drupal 9. C'est le seul
agenda ministériel « cœur de cible » sport qu'on puisse scraper en
HTTP direct — pas de Cloudflare, pas de WAF F5, pas de dataset open
data équivalent.

Spécificités du scraper :

- Le slug de la page est instable (`-1787`, `-1745`, etc. — un noeud
  Drupal différent à chaque publication). La seule URL stable est la
  home `https://www.sports.gouv.fr/` qui affiche en tête un lien vers
  la dernière édition. On fetche donc la home, on extrait le lien vers
  `/agenda-previsionnel-de-*`, puis on fetche cette page.

- La structure HTML est spécifique au site : un `<h2>` contenant le
  libellé « pour la semaine du JJ mois AAAA », puis des `<h5>` par
  jour (`Lundi JJ mois`, `Mardi JJ mois`, …) suivis de blocs `<p>`
  alternés : 1 `<p>` avec `<strong>horaire</strong>  description` puis
  1 `<p>` avec `<em>lieu</em>` optionnel. Horaire peut être une heure
  (« 08h45 », « 19h00 ») ou un créneau nommé (« Matin »,
  « Après-midi », « Soirée », « Journée »).

- Un uid stable est dérivé de (week_start, day_index, slot_raw,
  description). Les events sans horaire précis reçoivent une heure
  conventionnelle (9h / 14h / 18h / 12h) pour conserver un tri
  chronologique cohérent dans le digest.

Régressions possibles (à surveiller) :
- Changement de slug home → scraper renvoie 0 items (logguer WARNING).
- Changement de ministre → le `<h2>` contient un autre nom. On NE
  filtre PAS sur le nom — on parse tout ce qui matche le pattern
  « semaine du … ».
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Item
from ._common import fetch_text

log = logging.getLogger(__name__)


# Mois français (minuscules, sans accent de fin) — pour parser le libellé
# "semaine du 20 avril 2026" et les têtes de jour "Lundi 20 avril".
# On accepte variantes avec/sans accent car certaines pages ont des NBSP
# ou des accents combinants (rares mais vus une fois).
_MONTHS_FR: dict[str, int] = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    "decembre": 12,
}

_DAYS_FR: dict[str, int] = {
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4,
    "samedi": 5, "dimanche": 6,
}

# Créneaux nommés (pas d'heure exacte dans la source) → heure conventionnelle
# pour le tri. Volontairement midi-centrique pour Matin/Après-midi : le digest
# classe les events par heure et on veut que « Matin » arrive avant « 14h00 ».
_SLOT_DEFAULT_HOUR: dict[str, tuple[int, int]] = {
    "matin": (9, 0),
    "matinée": (9, 0),
    "matinee": (9, 0),
    "midi": (12, 0),
    "déjeuner": (12, 30),
    "dejeuner": (12, 30),
    "après-midi": (14, 0),
    "apres-midi": (14, 0),
    "après midi": (14, 0),
    "soirée": (19, 0),
    "soiree": (19, 0),
    "soir": (19, 0),
    "journée": (9, 0),
    "journee": (9, 0),
    "toute la journée": (9, 0),
    "toute la journee": (9, 0),
}

# "08h45", "8h45", "19h", "19 h 00", "19H00"
_TIME_RE = re.compile(
    r"^\s*(\d{1,2})\s*[hH]\s*(\d{0,2})\s*$",
    re.UNICODE,
)

# "semaine du 20 avril 2026" (tolère espaces multiples, NBSP, fin d'œil)
_WEEK_RE = re.compile(
    r"semaine\s+du\s+(\d{1,2})\s+([A-Za-zéûôâîÉÛÔÂÎ]+)\s+(\d{4})",
    re.IGNORECASE | re.UNICODE,
)

# "Lundi 20 avril" ; le « 2026 » est dans le libellé de semaine, pas
# répété sur chaque H5. On tolère espaces de fin et caractères NBSP.
_DAY_RE = re.compile(
    r"^\s*(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)"
    r"\s+(\d{1,2})\s+([A-Za-zéûôâîÉÛÔÂÎ]+)\s*$",
    re.IGNORECASE | re.UNICODE,
)


def fetch_source(src: dict) -> list[Item]:
    """Route les sources ministère des Sports selon leur format.

    Supports :
    - `min_sports_agenda_hebdo` : scrape le bulletin hebdomadaire de
      l'agenda de la ministre (1 URL en entrée, N items en sortie,
      1 item = 1 créneau).
    """
    fmt = src.get("format")
    if fmt == "min_sports_agenda_hebdo":
        return _fetch_agenda_hebdo(src)
    log.warning("min_sports : format %r non géré pour %s", fmt, src.get("id"))
    return []


def _resolve_agenda_url(landing_url: str) -> str | None:
    """Cherche le lien courant vers l'agenda hebdo depuis une page de
    navigation (typiquement la home sports.gouv.fr).

    Le slug change chaque semaine (`agenda-previsionnel-de-<nom>-<id>`),
    d'où ce resolver. On retourne None si aucun lien n'est trouvé — le
    caller loggue WARNING et return [].
    """
    try:
        html = fetch_text(landing_url)
    except Exception as e:
        log.warning("min_sports : home KO (%s) : %s", landing_url, e)
        return None

    # BS4 suffirait mais une regex est plus rapide et plus robuste ici
    # (le lien peut apparaître dans 3 emplacements différents selon la
    # promotion éditoriale du jour : bandeau haut, carrousel, footer).
    m = re.search(
        r'href="(/agenda-previsionnel-de-[a-z0-9\-]+)"',
        html,
        re.IGNORECASE,
    )
    if not m:
        log.warning(
            "min_sports : aucun lien /agenda-previsionnel-de-* trouvé sur %s",
            landing_url,
        )
        return None
    return urljoin(landing_url, m.group(1))


def _parse_week_start(title_text: str) -> datetime | None:
    """Extrait la date de début de semaine depuis le <h2>.

    Ex. « Agenda prévisionnel de … pour la semaine du 20 avril 2026 »
    → datetime(2026, 4, 20, 0, 0). Renvoie None si introuvable.
    """
    if not title_text:
        return None
    m = _WEEK_RE.search(title_text)
    if not m:
        return None
    day = int(m.group(1))
    month_name = m.group(2).lower()
    year = int(m.group(3))
    month = _MONTHS_FR.get(month_name)
    if not month:
        return None
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _parse_day_header(h5_text: str) -> tuple[str, int, int] | None:
    """Extrait (day_name, day_num, month) depuis le libellé d'un <h5>.

    Ex. « Lundi 20 avril » → ("lundi", 20, 4). Renvoie None si le
    libellé n'a pas le format attendu (un <h5> parasite peut exister,
    ex. pour annoncer une section hors agenda — on skipe).
    """
    if not h5_text:
        return None
    # Normalise NBSP, espaces multiples, espace de fin
    norm = re.sub(r"\s+", " ", h5_text.replace("\u00a0", " ")).strip()
    m = _DAY_RE.match(norm)
    if not m:
        return None
    day_name = m.group(1).lower()
    day_num = int(m.group(2))
    month_name = m.group(3).lower()
    month = _MONTHS_FR.get(month_name)
    if not month:
        return None
    return day_name, day_num, month


def _parse_slot_time(slot_raw: str) -> tuple[int, int] | None:
    """Parse un libellé horaire en (heure, minute).

    Accepte :
    - « 08h45 », « 19h00 », « 8h », « 19 h 00 »
    - « Matin », « Après-midi », « Soirée », « Journée », variantes
    Retourne None si aucun match (on filtre le slot côté appelant).
    """
    s = slot_raw.strip().replace("\u00a0", " ")
    m = _TIME_RE.match(s)
    if m:
        h = int(m.group(1))
        minute = int(m.group(2) or "0")
        if 0 <= h <= 23 and 0 <= minute <= 59:
            return h, minute
    # Créneaux nommés — on normalise pour tolérer accents / casse
    norm = re.sub(r"\s+", " ", s.lower()).strip()
    return _SLOT_DEFAULT_HOUR.get(norm)


def _fetch_agenda_hebdo(src: dict) -> list[Item]:
    """Fetch + parse le bulletin hebdo. 1 item = 1 créneau.

    Paramètres YAML :
        url          : home sports.gouv.fr (la scraper suit le lien vers
                        la page agenda-previsionnel-de-*). Si l'URL
                        pointe directement vers une page agenda, on
                        parse celle-ci — utile pour tests / rattrapage.
        id           : source_id Follaw (obligatoire)
        category     : défaut `agenda`
        chamber      : défaut `MinSports`
        title_prefix : ex. « MinSports — » ajouté au titre
    """
    sid = src["id"]
    landing_url = src["url"]
    cat = src.get("category", "agenda")
    chamber = src.get("chamber", "MinSports")
    title_prefix = src.get("title_prefix", "")

    # Si l'URL pointe déjà vers une page agenda (cas des tests, du diag
    # manuel, ou d'un snapshot archivé), pas besoin de passer par la
    # home. Sinon (home ou tout autre listing), on résoud le lien.
    if "/agenda-previsionnel-de-" in landing_url:
        agenda_url = landing_url
    else:
        agenda_url = _resolve_agenda_url(landing_url)
        if not agenda_url:
            return []

    try:
        html = fetch_text(agenda_url)
    except Exception as e:
        log.warning("min_sports %s : fetch KO %s : %s", sid, agenda_url, e)
        return []

    return _parse_agenda_html(html, src=src, agenda_url=agenda_url,
                              sid=sid, cat=cat, chamber=chamber,
                              title_prefix=title_prefix)


def _parse_agenda_html(
    html: str,
    *,
    src: dict,
    agenda_url: str,
    sid: str,
    cat: str,
    chamber: str,
    title_prefix: str,
) -> list[Item]:
    """Extrait les events depuis le HTML de la page agenda hebdo.

    Fonction séparée de `_fetch_agenda_hebdo` pour pouvoir la tester
    unitairement sans monkeypatch de `fetch_text`.
    """
    soup = BeautifulSoup(html, "lxml")

    # 1. Trouver le <h2> contenant « semaine du … »
    h2 = None
    for h in soup.find_all("h2"):
        if _WEEK_RE.search(h.get_text(" ", strip=True)):
            h2 = h
            break
    if h2 is None:
        log.warning(
            "min_sports %s : aucun <h2> avec 'semaine du …' sur %s",
            sid, agenda_url,
        )
        return []

    week_start = _parse_week_start(h2.get_text(" ", strip=True))
    if not week_start:
        log.warning("min_sports %s : week_start non parsé", sid)
        return []

    # 2. Parcourir les <h5> (jours) et collecter les <p> suivants jusqu'au
    #    prochain <h5> (ou fin de bloc). On reste dans le même parent que
    #    le <h2> — toutes les balises jour sont siblings.
    # Défense : on limite la fenêtre à 50 éléments pour éviter d'ingérer
    # un éventuel bloc de contenu parasite en fin de page.
    items: list[Item] = []
    current_day: tuple[str, int, int] | None = None
    day_events: list[dict] = []

    def _flush_day():
        """Fabrique des Items pour le jour courant en cours de parse."""
        if not current_day or not day_events:
            return
        day_name, day_num, month = current_day
        # On reconstruit la date du jour : on part de week_start, puis
        # on ajoute le décalage selon le nom du jour. Robustesse : si le
        # libellé ne matche pas (ex. changement d'année dans la semaine),
        # on retombe sur (month, day_num, year=week_start.year) brut.
        idx = _DAYS_FR.get(day_name)
        if idx is not None:
            day_dt = week_start + timedelta(days=idx)
            if day_dt.day != day_num or day_dt.month != month:
                # Mismatch : le libellé H5 diverge du décalage week+idx.
                # On privilégie (day_num, month) du libellé H5, car la
                # source peut enjamber un changement de mois (avril→mai).
                try:
                    day_dt = datetime(week_start.year, month, day_num)
                except ValueError:
                    return
        else:
            try:
                day_dt = datetime(week_start.year, month, day_num)
            except ValueError:
                return

        for ev in day_events:
            slot_raw = ev["slot_raw"]
            desc = ev["description"]
            loc = ev["location"]
            parsed = _parse_slot_time(slot_raw)
            if parsed:
                hour, minute = parsed
            else:
                # Slot inconnu → heure par défaut matin, 00
                hour, minute = 9, 0
            start_at = day_dt.replace(hour=hour, minute=minute)

            # Titre lisible : « HH:MM — description »
            # (Matin/Après-midi → pas de préfixe horaire)
            if _TIME_RE.match(slot_raw.strip()):
                title_core = f"{hour:02d}h{minute:02d} — {desc}"
            else:
                title_core = f"{slot_raw.strip()} — {desc}"
            title = title_core
            if title_prefix:
                title = f"{title_prefix} {title}"
            title = title[:220]

            # uid stable — survit aux re-publications et aux rebuilds
            # (le slug de la page bouge chaque semaine → on ne s'en sert
            # PAS dans le uid, on utilise (week_start, day_idx, slot, desc)).
            uid_seed = (
                f"{week_start.date().isoformat()}|"
                f"{day_dt.date().isoformat()}|"
                f"{slot_raw.strip()}|{desc.strip()[:200]}"
            )
            uid = hashlib.sha1(uid_seed.encode("utf-8")).hexdigest()[:16]

            summary = desc if not loc else f"{desc}\nLieu : {loc}"

            items.append(Item(
                source_id=sid,
                uid=uid,
                category=cat,
                chamber=chamber,
                title=title,
                url=agenda_url,
                published_at=start_at,
                summary=summary[:2000],
                raw={
                    "path": "min_sports:agenda_hebdo",
                    "week_start": week_start.date().isoformat(),
                    "day": day_dt.date().isoformat(),
                    "slot_raw": slot_raw.strip(),
                    "location": loc,
                },
            ))

    # Collecte : on part du <h2> et on avance dans les siblings. Les
    # événements sont structurés comme :
    #   <h5>Lundi 20 avril</h5>
    #   <p><strong>Après-midi</strong>   Description ...</p>
    #   <p><em>Lieu</em></p>         ← optionnel
    #   <p><strong>08h45</strong>   Description suivante...</p>
    #   <p><em>Lieu</em></p>
    # Sur la page Drupal actuelle, les <h5> et <p> sont siblings du <h2>
    # dans un conteneur `<div class="sports-gouv-container">`. On
    # itère donc sur `h2.next_siblings` filtrés.
    container = h2.parent
    pending_event: dict | None = None

    for el in container.find_all(["h5", "p"], recursive=False):
        # Skip tout ce qui précède le H2 (cas improbable — le H2 est en
        # premier). On utilise l'ordre du document : si on a vu au moins
        # un h5 ou si on est après le h2 dans sourceline.
        if el.name == "h5":
            # Nouveau jour → flush l'event en attente, puis flush le jour
            if pending_event is not None:
                day_events.append(pending_event)
                pending_event = None
            _flush_day()
            day_events = []
            current_day = _parse_day_header(el.get_text(" ", strip=True))
            continue

        # <p> — deux cas : event (contient <strong>) ou lieu (contient <em>)
        if current_day is None:
            # <p> avant tout <h5> : probablement du texte d'intro, skip
            continue

        text = el.get_text(" ", strip=True)
        if not text:
            continue

        strong = el.find("strong")
        em = el.find("em")

        if strong is not None:
            # Nouveau créneau : on flush l'éventuel event en attente
            if pending_event is not None:
                day_events.append(pending_event)
            slot_raw = strong.get_text(" ", strip=True)
            # Description = tout le texte du <p> APRÈS le 1er <strong>.
            # On reconstitue en retirant le 1er strong du get_text.
            full = el.get_text(" ", strip=True).replace("\u00a0", " ")
            # Heuristique : le slot_raw est forcément au début (format
            # du site). On coupe dessus.
            if full.startswith(slot_raw):
                desc = full[len(slot_raw):].strip()
            else:
                # Edge case : le strong n'est pas au début (inattendu)
                desc = full
            # Nettoyages : espaces multiples → simple, puis resserrer les
            # espaces parasites avant ponctuation (« JEAN-MARIE , » →
            # « JEAN-MARIE, ») que BS4 introduit autour des <strong>
            # imbriqués (get_text avec séparateur " ").
            desc = re.sub(r"\s{2,}", " ", desc)
            desc = re.sub(r"\s+([,.;:!?])", r"\1", desc)
            desc = desc.strip(" -—:")
            pending_event = {
                "slot_raw": slot_raw,
                "description": desc,
                "location": "",
            }
        elif em is not None and pending_event is not None:
            # Lieu rattaché à l'event en attente
            loc = em.get_text(" ", strip=True)
            pending_event["location"] = loc
            day_events.append(pending_event)
            pending_event = None
        # Sinon : <p> vide ou parasite → ignore

    # Fin de boucle : flush le dernier event / jour
    if pending_event is not None:
        day_events.append(pending_event)
    _flush_day()

    log.info(
        "min_sports %s : %d items normalisés (semaine du %s)",
        sid, len(items), week_start.date().isoformat(),
    )
    return items
