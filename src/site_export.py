"""Export JSON + Markdown Hugo pour le site statique veille.sideline-conseil.fr.

Structure produite :

    site/data/index.json                    — tous les items matchés (≤ 30 j)
    site/data/by_category/{cat}.json        — regroupement par catégorie
    site/data/by_chamber/{cham}.json        — regroupement par chambre
    site/content/_index.md                  — page d'accueil (zone <24h puis 30j)
    site/content/items/{cat}/_index.md      — page de listing catégorie
    site/content/items/{cat}/{slug}.md      — une page par item matché
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from . import amo_loader
from .digest import CATEGORY_LABELS, CATEGORY_ORDER

# R13-G (2026-04-21) : label de version système, affiché dans le header
# (R13-J : déplacé depuis la sidebar) pour que Cyril puisse identifier
# rapidement quelle révision du pipeline a généré la page en ligne. À
# incrémenter à chaque cumul de patches UX.
SYSTEM_VERSION_LABEL = "R13-J"

# Fenêtre de publication visible sur le site (jours) — par défaut pour les
# flux à forte rotation (questions, CR, amendements, communiqués, agenda).
WINDOW_DAYS = 30

# Fenêtre spécifique par catégorie pour les flux à cycle long (dossiers
# législatifs : navettes de plusieurs mois à plusieurs années). Le dict prime
# sur WINDOW_DAYS pour les catégories listées.
WINDOW_DAYS_BY_CATEGORY: dict[str, int] = {
    # UX-B (2026-04-21) : 3 ans max — l'user a vu apparaître des vieux
    # dossiers de 1990 sur le dopage. 730j (2 ans) laissait passer certains
    # items sans `published_at` via le fallback `inserted_at`. Désormais :
    # - fenêtre 3 ans (1095j)
    # - dossiers_legislatifs ajouté à STRICT_DATED_CATEGORIES (pas de fallback)
    # Résultat : seuls les dossiers avec une date de dépôt / promulgation
    # fiable et dans les 3 dernières années apparaissent.
    # NB R13-G : la page d'accueil applique une sous-fenêtre plus courte
    # (HOMEPAGE_WINDOW_DAYS_BY_CATEGORY ci-dessous) pour les dosleg, la
    # page /items/dossiers_legislatifs/ garde les 3 ans complets.
    "dossiers_legislatifs": 1095,  # 3 ans
    # Agenda : on veut 3 mois pour voir les réunions passées récentes ET les
    # séances futures planifiées (ordre du jour de la session en cours).
    # 30j coupait trop court : une réunion annoncée 6 semaines à l'avance
    # ou passée depuis 5 semaines disparaissait du site.
    "agenda": 90,
    # Publications (communiqués de presse, actualités ministères / autorités
    # indépendantes) : 3 mois. Au-delà la page perd en pertinence opérationnelle.
    "communiques": 90,
    # Comptes rendus : 6 mois. Avant R11g (UX-E), la fenêtre 30j tuait
    # quasi tous les CR sport — `_fix_cr_row` recale `published_at` sur la
    # vraie date de séance (extraite du nom de fichier d20260205.xml ou du
    # XML Syceron), et beaucoup de séances sport pertinentes (JO 2030,
    # dopage, ANS) datent de plusieurs mois. Les CR sont par ailleurs peu
    # nombreux et restent référents longtemps : 180j est un meilleur
    # compromis.
    "comptes_rendus": 180,
}

# Catégories pour lesquelles on exige une vraie `published_at` ≤ now (pas de
# fallback `inserted_at`, pas de dates futures). Ça évite que la page
# Publications se fasse polluer par :
#   - des rapports Sénat CSV livrés sans date de dépôt,
#   - des flux RSS legacy sans <pubDate>,
#   - des « pages pivot » scrapées par html_generic (« Page suivante », « Presse »),
#   - des agendas hebdo datés en fin de semaine à venir.
STRICT_DATED_CATEGORIES = {"communiques", "dossiers_legislatifs"}

# R13-G (2026-04-21) : sous-fenêtre appliquée UNIQUEMENT dans
# `_write_home` (pas dans les pages /items/<cat>/). La fenêtre globale de
# WINDOW_DAYS_BY_CATEGORY reste référentielle pour les pages dédiées —
# la home est plus sélective pour ne pas noyer l'utilisateur dans du
# vieux. Cyril (2026-04-21) : les dossiers législatifs affichés sur la
# home doivent avoir été "mis à jour depuis moins de 6 mois", le reste
# reste accessible depuis /items/dossiers_legislatifs/.
HOMEPAGE_WINDOW_DAYS_BY_CATEGORY: dict[str, int] = {
    "dossiers_legislatifs": 180,  # 6 mois (home) vs 1095j (page dédiée)
}

# R13-G : libellés humains pour les fenêtres par catégorie. Clé présente
# → texte custom ("Mis à jour depuis moins de 6 mois"). Clé absente →
# format générique "Depuis X jours" (remplace l'ancien "fenêtre X j").
HOMEPAGE_WINDOW_LABEL_BY_CATEGORY: dict[str, str] = {
    "dossiers_legislatifs": "Mis à jour depuis moins de 6 mois",
}

# R13-D (2026-04-21) — snippet par contexte :
#
# Home : pour garder la page d'accueil compacte, on n'affiche les snippets
# QUE pour les catégories à forte densité informative (questions,
# amendements, comptes rendus). Publications / agenda / JORF / dossiers
# législatifs : titre seul, plus clair en coup d'œil.
HOMEPAGE_SNIPPET_CATEGORIES = {"questions", "amendements", "comptes_rendus"}

# Pages thématiques (/items/<cat>/) — tailles demandées par Cyril
# (R13-D 2026-04-21). Absence de clé → snippet retiré pour la catégorie.
# Note : on stocke le snippet déjà tronqué dans le frontmatter des .md,
# pour que le template Hugo n'ait pas à se soucier de la catégorie.
SNIPPET_LEN_BY_CATEGORY: dict[str, int] = {
    "jorf": 500,
    "communiques": 500,
    "amendements": 250,
    "questions": 250,
    # R13-K (2026-04-21) : CR 250 → 500 (au moins double demandé par Cyril).
    "comptes_rendus": 500,
    "agenda": 250,
    # dossiers_legislatifs : clé absente = pas de snippet sur la page
    # dédiée (demande user — les cartes dosleg portent déjà titre, type,
    # date, statut, tags ; un extrait y ajouterait du bruit).
}

# --- Regex partagés pour la réparation CR AN ---------------------------
# Les dumps Syceron Brut (AN) ne portent pas la date de séance dans le nom
# de fichier (CRSAN…), mais l'en-tête du XML contient :
#   <timeStampDebut>YYYYMMDDHHMMSSmmm</timeStampDebut>
# soit en texte brut "20250709150000000" = 2025-07-09 15:00. On récupère la
# date en lisant la première occurrence d'une séquence AAAAMMJJ + 9 chiffres.
_AN_CR_DATE_RE = re.compile(r"\b(20\d{2})(\d{2})(\d{2})\d{9}\b")
# Après l'en-tête, la première ligne du texte contient toujours
#   « Présidence de <M./Mme/Mlle> <prénom> <nom> <OBJET DE LA SEANCE> 0 …»
# (le « 0 » servant de séparateur de sous-section Syceron). On capture donc
# le thème jusqu'au premier « 0 » isolé.
_AN_CR_THEME_RE = re.compile(
    r"Présidence\s+de\s+(?:M\.|Mme|Mlle)\s+\S+\s+\S+\s+(.+?)\s+0\s",
    re.IGNORECASE | re.DOTALL,
)


def _extract_an_cr_meta(summary: str | None) -> tuple[str, str]:
    """Depuis le résumé Syceron stripé, retourne (date_iso, theme).

    Les deux peuvent être vides si le pattern ne matche pas."""
    text = (summary or "")[:4000]
    date_iso = ""
    m_d = _AN_CR_DATE_RE.search(text)
    if m_d:
        date_iso = f"{m_d.group(1)}-{m_d.group(2)}-{m_d.group(3)}"
    theme = ""
    m_t = _AN_CR_THEME_RE.search(text)
    if m_t:
        theme = re.sub(r"\s+", " ", m_t.group(1)).strip(" .,;:—-")
        # Garde-fou : on accepte ≤130 caractères ; au-delà, on a probablement
        # raté la borne du sous-section divider — on coupe au dernier mot.
        if len(theme) > 130:
            theme = theme[:130].rsplit(" ", 1)[0] + "…"
    return date_iso, theme


def _fix_cr_row(r: dict) -> None:
    """Répare en mémoire l'URL et le titre d'un item comptes_rendus
    ingéré avant les patchs AN/Sénat (CR). Opère sur place.

    - AN : ancienne URL /dyn/17/seances (404) → /dyn/17/comptes-rendus/seance/{cr_ref}
      ou à défaut /dyn/17/comptes-rendus (toujours 200). Date séance +
      thème extraits du résumé (timeStampDebut + "Présidence de …").
    - Sénat : URL tronquée https://www.senat.fr/seances/s{YYYYMM}/ → ajoute
      le jour final /s{YYYYMMDD}/ depuis raw.seance_date_iso.
    - Titre générique (« Compte rendu AN — CRSAN… ») réécrit en « Séance
      {chambre} du JJ/MM/AAAA — {thème} ».

    Cette fonction est idempotente : réappliquer le patch n'a pas d'effet
    sur un item déjà normalisé.
    """
    if (r.get("category") or "") != "comptes_rendus":
        return
    raw = r.get("raw")
    if not isinstance(raw, dict):
        return
    url = (r.get("url") or "").strip()
    title = (r.get("title") or "").strip()
    cham = (r.get("chamber") or "").strip()

    # Date réelle de séance, à déduire avec prudence :
    # - AN : on ne fait PAS confiance à published_at (= date d'ingestion).
    #        On extrait la date du XML stripé (timeStampDebut = AAAAMMJJ+9 chiffres).
    # - Sénat : le parser pré-patch laissait le nom de fichier XML dans le
    #        titre ("CR intégral — d20260119.xml"). On y récupère la date.
    seance_iso = (raw.get("seance_date_iso") or "").strip()
    an_theme_extracted = ""
    if not seance_iso and cham == "AN":
        # Extraction depuis le résumé (Syceron brut stripé)
        seance_iso, an_theme_extracted = _extract_an_cr_meta(r.get("summary"))
    if not seance_iso:
        # Nom de fichier embarqué dans le titre (cas Sénat pré-patch)
        m_date = re.search(r"d(\d{4})(\d{2})(\d{2})\.xml", title)
        if m_date:
            seance_iso = f"{m_date.group(1)}-{m_date.group(2)}-{m_date.group(3)}"
    # Pour le Sénat uniquement, si toujours rien, on peut tomber sur
    # published_at comme dernier recours. Pour AN on n'utilise jamais
    # published_at : c'est la date d'ingestion Syceron, pas la séance.
    if not seance_iso and cham in ("Senat", "Sénat"):
        pa = r.get("published_at")
        if isinstance(pa, str) and re.match(r"^\d{4}-\d{2}-\d{2}", pa):
            seance_iso = pa[:10]

    # --- URL AN cassée
    if cham == "AN" and "/dyn/17/seances" in url and "/comptes-rendus/" not in url:
        cr_ref = (raw.get("cr_ref") or "").strip()
        if not cr_ref:
            fichier = raw.get("fichier") or ""
            m = re.search(r"CRSAN[A-Z0-9]{5,30}", fichier, re.IGNORECASE)
            if m:
                cr_ref = m.group(0).upper()
        # Cas rare : la cr_ref est parfois seulement dans le titre pré-patch
        if not cr_ref:
            m = re.search(r"CRSAN[A-Z0-9]{5,30}", title, re.IGNORECASE)
            if m:
                cr_ref = m.group(0).upper()
        if cr_ref:
            url = f"https://www.assemblee-nationale.fr/dyn/17/comptes-rendus/seance/{cr_ref}"
        else:
            url = "https://www.assemblee-nationale.fr/dyn/17/comptes-rendus"
        r["url"] = url

    # --- URL Sénat cassée (manque le jour)
    elif cham in ("Senat", "Sénat"):
        m_s = re.match(r"^https://www\.senat\.fr/seances/s(\d{6})/?$", url)
        if m_s and re.match(r"^\d{4}-\d{2}-\d{2}$", seance_iso):
            ymd = seance_iso.replace("-", "")
            r["url"] = f"https://www.senat.fr/seances/s{ymd[:6]}/s{ymd}/"

    # --- Titre générique pré-patch → réécriture avec date + thème
    # On détecte aussi les titres déjà partiellement « patchés » mais
    # construits sur la date d'ingestion (ex. « Séance AN du 20/04/2026 — … »
    # pour tous les CR d'une même run Syceron).
    looks_generic = bool(
        re.match(r"^(Compte rendu (AN|intégral|analytique) —|CR (intégral|analytique) —)",
                 title)
    )
    # Détection d'un titre "Séance AN du JJ/MM/AAAA — …" où la date vient de
    # published_at (bug _fetch_xml_zip) et qu'on a maintenant la vraie date.
    fake_an_date = False
    if cham == "AN" and seance_iso:
        m_old = re.match(r"^Séance AN du (\d{2})/(\d{2})/(\d{4}) — ", title)
        if m_old:
            old_iso = f"{m_old.group(3)}-{m_old.group(2)}-{m_old.group(1)}"
            if old_iso != seance_iso:
                fake_an_date = True
    if looks_generic or fake_an_date:
        theme = (
            (raw.get("theme") or "").strip()
            or an_theme_extracted
        )
        # Si on n'a toujours pas de thème mais qu'on a un titre existant qui
        # ressemble à « Séance AN du JJ/MM/AAAA — <bruit> », on le refait
        # depuis le résumé (plus fiable que _THEMES_RE qui capture les
        # jetons de balise).
        if not theme and cham == "AN":
            _, theme_ex = _extract_an_cr_meta(r.get("summary"))
            theme = theme_ex
        # UX-E : même logique pour les CR Sénat pré-patch qui apparaissaient
        # avec le titre "CR intégral — d20260205.xml" (date + .xml du nom
        # de fichier dans le titre). `extract_cr_theme` sait repérer les
        # patterns Sénat ("Discussion du projet de loi…", "Questions au
        # gouvernement", "Examen du rapport…") dans les 8000 premiers chars
        # du résumé, exactement comme le fait le connecteur au moment de
        # l'ingestion. Ça réparé les CR historiques sans reset DB.
        if not theme and cham in ("Senat", "Sénat"):
            from .sources._common import extract_cr_theme
            theme = extract_cr_theme(r.get("summary")) or ""
        date_label = ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", seance_iso):
            y, mo, dd = seance_iso.split("-")
            date_label = f"{dd}/{mo}/{y}"
        # R13-G (2026-04-21) : pour l'AN on retire la mention "AN" du titre
        # (le badge <span class="chamber" data-chamber="AN"> suffit à
        # identifier la chambre). Pour le Sénat on garde "Sénat" dans le
        # titre — les deux CR Sénat (intégral vs analytique) coexistent et
        # il est utile d'afficher la chambre en toutes lettres pour éviter
        # toute ambiguïté sur les pages thématiques.
        if cham in ("Senat", "Sénat"):
            seance_prefix = "Séance Sénat du"
        else:
            seance_prefix = "Séance du"
        # Type de CR (intégral / analytique)
        type_label = "intégral"
        if "analytique" in title.lower():
            type_label = "analytique"
        if date_label and theme:
            r["title"] = f"{seance_prefix} {date_label} — {theme}"[:220]
        elif date_label:
            r["title"] = (
                f"{seance_prefix} {date_label} "
                f"— Compte rendu {type_label}"
            )[:220]
        elif cham == "AN":
            # Pas de date fiable pour AN : on garde la cr_ref + chambre
            m = re.search(r"CRSAN[A-Z0-9]{5,30}", title, re.IGNORECASE)
            cref = m.group(0).upper() if m else ""
            if cref:
                r["title"] = f"Compte rendu AN — séance {cref}"[:220]

    # R13-G : CR AN existants titrés "Séance AN du …" → "Séance du …"
    # (le badge .chamber affiche déjà "AN"). Idempotent : n'agit que si le
    # titre commence exactement par "Séance AN du ". Ne touche PAS le Sénat.
    if cham == "AN":
        cur_title = r.get("title") or ""
        if cur_title.startswith("Séance AN du "):
            r["title"] = cur_title.replace("Séance AN du ", "Séance du ", 1)[:220]

    # --- Date publication recalée : pour trier correctement les CR sur la
    # page d'accueil, on remplace published_at par la date de séance
    # extraite (published_at Syceron/Sénat = date d'ingestion, biaise le tri
    # et fait apparaître des CR anciens comme « Dernières 24h »).
    if seance_iso and re.match(r"^\d{4}-\d{2}-\d{2}$", seance_iso):
        pa = (r.get("published_at") or "")
        if not isinstance(pa, str) or not pa.startswith(seance_iso):
            # On fixe 12:00 : seule la date est fiable ici.
            r["published_at"] = f"{seance_iso}T12:00:00"


# Pattern pour repérer (et retirer) le segment "→ {ministère} [{sort}]" dans
# les vieux titres Sénat questions, et la mention "Député PAxxx" dans les
# items Sénat/AN questions ingérés avant que le cache AMO résolve le code.
_QTITLE_MIN_RE = re.compile(
    r"\s*→\s*[^:\[]+(?:\s*\[[^\]]+\])?\s*(?=:)",  # "→ ministère [sort]" avant le ":"
)
# Certaines vieilles entrées produisaient "(GRP):sujet" (pas d'espace avant
# le colon) après retrait du "→ …". Ce post-pattern normalise l'espacement.
_QTITLE_COLON_FIX_RE = re.compile(r"\)\s*:\s*")
_QTITLE_DEPUTE_RE = re.compile(r"Député\s+(PA\d+)")


def _fix_question_row(r: dict) -> None:
    """Réécrit en mémoire le titre des items `questions` ingérés avec
    l'ancien format `… — auteur (groupe) → ministère [sort] : sujet`.

    Le user veut un titre épuré : `{type} n°{num} — {nom prénom auteur}
    ({groupe}) : {objet/sujet}` (UX-D 2026-04-21). Le ministre interrogé
    n'apparaît plus, le `[sort]` non plus. L'info ministre / sort reste
    dans le summary pour matching et consultation.

    R13-J (2026-04-21) : retire aussi la date dupliquée `· DD/MM/YYYY`
    du titre (Cyril patch 3 — la date est déjà affichée en meta-line
    sous le titre, avec le nouveau format "JJ mois complet AAAA").

    Résout aussi les `Député PAxxx` résiduels via le cache AMO. Idempotent.
    """
    if (r.get("category") or "") != "questions":
        return
    original_title = (r.get("title") or "")
    title = original_title
    raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}

    # 0) R13-J : retire la date dupliquée du titre AN "Question écrite ·
    #    12/04/2026 — Auteur (Groupe) : Sujet" → "Question écrite — …".
    title = re.sub(r"\s*·\s*\d{2}/\d{2}/\d{4}(?=\s*—|\s|$)", "", title)

    # 1) Retire la séquence "→ ministère [sort] " du titre ancien Sénat
    new_title = _QTITLE_MIN_RE.sub("", title) if title else title

    # 2) Résout "Député PAxxx" via cache AMO (manquait avant que le cache
    #    soit alimenté). Idempotent : si l'auteur est déjà résolu ou si
    #    le cache n'a pas la clé, on ne touche pas.
    def _resolve(match: re.Match) -> str:
        ref = match.group(1)
        resolved = amo_loader.resolve_acteur(ref) if ref else ""
        return resolved or match.group(0)

    new_title = _QTITLE_DEPUTE_RE.sub(_resolve, new_title)
    # Nettoie l'espacement autour du colon après retrait du "→ …" :
    # "(GRP):sujet" → "(GRP) : sujet"
    new_title = _QTITLE_COLON_FIX_RE.sub(") : ", new_title)
    # Double espace résiduel éventuel
    new_title = re.sub(r"\s{2,}", " ", new_title).strip()
    if new_title != original_title:
        r["title"] = new_title[:220]

    # 3) Si raw.auteur commence encore par "Député PAxxx", on tente la
    #    résolution AMO et on met à jour pour que l'export Markdown
    #    n'affiche pas le code dans la fiche détaillée.
    if isinstance(raw, dict):
        auteur = (raw.get("auteur") or "").strip()
        ref = (raw.get("auteur_ref") or "").strip()
        if auteur.startswith("Député PA") and ref.startswith("PA"):
            resolved = amo_loader.resolve_acteur(ref)
            if resolved:
                raw["auteur"] = resolved
                if not raw.get("groupe"):
                    raw["groupe"] = amo_loader.resolve_groupe(ref) or ""
                # Le raw vient de json.loads(r["raw"]) donc on doit le
                # réécrire dans r pour qu'il soit propagé en aval.
                r["raw"] = raw

    # 4) R13-G (2026-04-21) : priorité d'affichage analyse > rubrique.
    #    Les items pré-R13-G ont un titre "... : sport" (rubrique) là où
    #    Cyril attend "... : Réforme du sport à l'école" (analyse). Si le
    #    raw contient `analyse`, on remplace le suffixe "sujet" du titre.
    if isinstance(raw, dict):
        new_analyse = (raw.get("analyse") or raw.get("tete_analyse") or "").strip()
        if new_analyse:
            cur = r.get("title") or ""
            # Le titre se termine par ": <sujet>" — on coupe au dernier ":"
            # en conservant tout ce qui est avant. Idempotent (si analyse
            # déjà dans le titre, le replace ne change rien).
            m_colon = re.search(r"^(.*? : )(.+)$", cur)
            if m_colon:
                prefix, suffix = m_colon.group(1), m_colon.group(2).strip()
                if suffix.lower() != new_analyse.lower():
                    r["title"] = (prefix + new_analyse)[:220]


_AMEND_DEPUTE_RE = re.compile(r"Député\s+(PA\d+)")


def _fix_amendement_row(r: dict) -> None:
    """Résout les « Député PAxxx » résiduels dans les titres d'amendements AN.

    R13-A (2026-04-21) — le refresh du cache AMO /17/ couvre maintenant tous
    les députés XVIIe législature. Les *nouveaux* amendements ingérés après
    le refresh sont bien résolus dans `_normalize_amendement`
    (src/sources/assemblee.py), mais les items déjà en DB gardent leur ancien
    titre "Amendement n°X [statut] — Député PAxxxx · art. … · sur « … »" car
    `upsert_many` ne met pas à jour les hash_key existants. On réécrit ici en
    mémoire à l'export sans exiger un reset DB. Idempotent.

    Ne touche pas aux amendements Sénat (auteur en clair dans le CSV).
    """
    if (r.get("category") or "") != "amendements":
        return
    title = (r.get("title") or "")
    raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
    auteur_ref_raw = (
        (raw.get("auteur_ref") or "").strip() if isinstance(raw, dict) else ""
    )

    # Étape 1 — Résolution "Député PAxxx" si présent (legacy). Ne touche pas
    # au titre si le code n'y figure pas (early exit de l'ancienne logique,
    # refactoré en condition pour que l'étape 2 s'applique quand même).
    if "Député PA" in title:
        def _resolve(match: re.Match) -> str:
            # Priorité à auteur_ref du raw (canonique), fallback sur le code capturé.
            ref = (
                auteur_ref_raw
                if auteur_ref_raw.startswith("PA")
                else match.group(1)
            )
            resolved = amo_loader.resolve_acteur(ref) if ref else ""
            return resolved or match.group(0)

        new_title = _AMEND_DEPUTE_RE.sub(_resolve, title)
        if new_title != title:
            r["title"] = new_title[:220]

    # Étape 2 — R13-G (2026-04-21) : "Amendement n°X" → "Amdt n°X" pour
    # les items ingérés avant la modif parser. Idempotent. S'applique même
    # quand le titre ne contenait pas "Député PA" (cas auteur en clair).
    t = r.get("title") or ""
    if t.startswith("Amendement n°"):
        r["title"] = ("Amdt n°" + t[len("Amendement n°"):])[:220]
    elif " — Amendement n°" in t:
        r["title"] = t.replace(" — Amendement n°", " — Amdt n°", 1)[:220]


# R13-G : mapping domaine de ministère → badge (copie de html_generic._MIN_MAP).
# Dupliqué volontairement ici pour que les items "chamber=Www" déjà en DB
# (ingérés avant la correction parser) soient réécrits en mémoire à l'export
# sans exiger de reset DB. Quand la DB sera exclusivement post-R13-G, ce
# fixup ne fera plus rien (idempotent).
_CHAMBER_WWW_FIXUP = {
    # source_id → bon badge
    "min_armees":               "MinARMEES",
    "min_justice":              "MinJUSTICE",
    "min_interieur":            "MinINTERIEUR",
    "min_culture":              "MinCULTURE",
    "min_education":            "MinEDUCATION",
    "min_economie":             "MinECO",
    "min_sante":                "MinSANTE",
    "min_travail":              "MinTRAVAIL",
    "min_affaires_etrangeres":  "MinAFFAIRES",
    "min_enseignement_sup":     "MinESR",
    "min_ruralite":             "MinCOHESION",
    "min_transition_ecologique": "MinECOLOGIE",
}


def _fix_chamber_row(r: dict) -> None:
    """Réécrit le champ `chamber="Www"` en badge ministériel correct.

    R13-G (2026-04-21) — `html_generic._chamber` tombait sur le fallback
    `d.split(".")[0].capitalize()` pour les URLs commençant par `www.`, ce
    qui produisait "Www" pour la plupart des ministères (defense, justice,
    interieur, culture, …). Cyril a signalé le cas defense → MinARMEES.
    Le parser corrige maintenant les nouveaux items, ce fixup corrige les
    items déjà en DB sans reset. Idempotent : n'agit que si chamber="Www".
    """
    if (r.get("chamber") or "") != "Www":
        return
    sid = (r.get("source_id") or "").strip()
    mapped = _CHAMBER_WWW_FIXUP.get(sid)
    if mapped:
        r["chamber"] = mapped


def _fix_dossier_row(r: dict) -> None:
    """Capitalise la 1re lettre du titre des dossiers législatifs Sénat.

    UX-B (2026-04-21) — certains CSV Sénat (senat_promulguees / senat_ppl /
    senat_dosleg) exposent le titre en minuscule ("projet de loi relatif à
    l'organisation…"). Le connecteur récent applique déjà `_cap_first`,
    mais la DB contient des items pre-patch non normalisés. On capitalise
    en mémoire à l'export pour ne pas exiger un reset DB. Idempotent,
    préserve les sigles déjà en majuscules (CNIL, PJL, etc.).
    """
    if (r.get("category") or "") != "dossiers_legislatifs":
        return
    title = (r.get("title") or "")
    if not title:
        return
    # Si la 1re lettre est déjà en majuscule ou non-alphabétique, on skip
    first = title[0]
    if not first.isalpha() or first.isupper():
        return
    r["title"] = (first.upper() + title[1:])[:220]


def _fix_agenda_row(r: dict) -> None:
    """Nettoie le titre des items agenda / communiqués « Agenda — … ».

    UX-A (2026-04-21) — demande utilisateur : "pas de mention de Agenda suivi
    d'un tiret dans les occurrences". Retrait strict du préfixe `Agenda -`,
    `Agenda –`, `Agenda — ` (avec ou sans espace) uniquement quand le titre
    COMMENCE par ce motif. On ne touche pas aux "Agenda de X" / "Agenda du Y"
    qui restent informatifs tels quels (la présence d'une personne nommée
    apporte du sens — retirer "Agenda de" laisserait un nom nu sans contexte).

    Idempotent. Agit sur les catégories agenda ET communiques (les communiqués
    ministériels "Agenda - Semaine du X au Y" sont de vrais bulletins d'agenda
    hebdo, l'user veut que le préfixe redondant disparaisse dans le titre).
    """
    cat = r.get("category") or ""
    if cat not in ("agenda", "communiques"):
        return
    title = (r.get("title") or "").strip()
    if not title:
        return
    # Tirets à matcher : hyphen-minus, en-dash, em-dash.
    # Le pattern impose un espace avant/après pour ne pas casser "Agenda-X"
    # (ligaturé) qui serait un nom propre.
    m = re.match(r"^Agenda\s*[-–—]\s*(.+)$", title)
    if m:
        new_title = m.group(1).strip()
        if new_title:
            # Capitalise la 1re lettre pour un rendu propre
            new_title = new_title[0].upper() + new_title[1:]
            r["title"] = new_title[:220]

    # R13-H (2026-04-21) : "Réunion (POxxx)" — l'item AN agenda n'a pas pu
    # résoudre l'organe au parsing (cache AMO incomplet pour les commissions
    # récentes) ET n'a pas de libellé ODJ dans le JSON AN. Plutôt que d'exposer
    # le code brut, on retente la résolution à l'export (le cache peut avoir
    # été enrichi entre temps) puis on retombe sur la date de séance si connue.
    # Idempotent — ne touche pas aux titres qui ne matchent pas le pattern.
    if cat == "agenda":
        cur_title = r.get("title") or ""
        m_po = re.match(
            r"^(Réunion(?:\s+de\s+commission)?)\s*\((PO\d+)\)$",
            cur_title,
        )
        if m_po:
            base, po_ref = m_po.group(1), m_po.group(2)
            organe_label = amo_loader.resolve_organe(po_ref)
            if organe_label:
                r["title"] = f"{base} — {organe_label}"[:220]
            else:
                # Fallback : date de séance en format humain.
                pa = r.get("published_at") or ""
                if isinstance(pa, str) and re.match(r"^\d{4}-\d{2}-\d{2}", pa):
                    y, mo, dd = pa[:10].split("-")
                    r["title"] = f"{base} AN du {dd}/{mo}/{y}"[:220]
                else:
                    # Dernier recours : "Réunion parlementaire" (lisible, sans
                    # code technique — le badge .chamber[data-chamber=AN]
                    # et le tag date ci-dessous porteront le contexte).
                    r["title"] = "Réunion parlementaire"


def _window_for(category: str | None) -> int:
    """Fenêtre (jours) applicable à une catégorie donnée."""
    if category and category in WINDOW_DAYS_BY_CATEGORY:
        return WINDOW_DAYS_BY_CATEGORY[category]
    return WINDOW_DAYS

# Sous-fenêtre "mises à jour du jour" pour le haut de la home.
RECENT_HOURS = 24


def _slugify(s: str) -> str:
    s = s or ""
    # Retire les schémas d'URL pour qu'ils ne polluent pas les slugs
    s = re.sub(r"https?://", "", s, flags=re.IGNORECASE)
    s = re.sub(r"www\.", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] or "item"


def _parse_dt(value) -> datetime | None:
    """Parse best-effort d'un datetime stocké en string ISO.

    Normalise en naïf UTC (cf. R11f / `_common.parse_iso`) pour rester
    comparable à `datetime.utcnow()` utilisé en aval dans `_window_keep`.
    Sans ça, un `published_at` stocké avec tz (ex. vieil item AN agenda
    pré-R11f) crash la comparaison `dt >= cutoff`.
    """
    from datetime import timezone as _tz
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(_tz.utc).replace(tzinfo=None)
        return value
    s = str(value)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        # fallback : juste la date
        try:
            dt = datetime.fromisoformat(s[:10])
        except Exception:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_tz.utc).replace(tzinfo=None)
    return dt


def _load(rows: list[dict]) -> list[dict]:
    """Parse les colonnes JSON-string vers des objets.

    Recalcule aussi le `snippet` depuis `summary` pour CHAQUE item matché :
    le schéma SQL n'a jamais eu de colonne `snippet` (cf. `store.SCHEMA`),
    donc `item.snippet` construit par `KeywordMatcher.apply` n'a jamais été
    persisté. Résultat : avant UX-E, les CR (et toutes les autres catégories)
    apparaissaient sans extrait sur le site et dans le digest. On reconstruit
    ici à l'export, c'est idempotent et suffisamment rapide (~quelques
    centaines d'items matchés).
    """
    # Import local pour éviter une dépendance cyclique au niveau module.
    from .keywords import KeywordMatcher
    _matcher = KeywordMatcher("config/keywords.yml")

    out = []
    for r in rows:
        r = dict(r)
        try:
            r["matched_keywords"] = json.loads(r.get("matched_keywords") or "[]")
        except Exception:
            r["matched_keywords"] = []
        # R13-B backfill : les items pré-capitalisation du yaml ont des
        # `matched_keywords` stockés en minuscules non-accentuées ("jeux
        # olympiques", "activite physique adaptee"). On remappe sur le
        # libellé affichable courant sans re-matcher. Idempotent.
        if r["matched_keywords"]:
            r["matched_keywords"] = _matcher.recapitalize(r["matched_keywords"])
        try:
            r["keyword_families"] = json.loads(r.get("keyword_families") or "[]")
        except Exception:
            r["keyword_families"] = []
        # Snippet : reconstruit à la volée depuis summary (ou title en fallback)
        # pour les items matchés. On ne stocke pas en DB — UX-E commentaire.
        if r.get("matched_keywords") and not r.get("snippet"):
            haystack = (r.get("summary") or r.get("title") or "").strip()
            if haystack:
                r["snippet"] = _matcher.build_snippet(haystack)
        # `raw` est stocké en TEXT JSON dans la DB — on le parse pour exposer
        # les champs enrichis (notamment status_label pour les dossiers
        # législatifs, cf. assemblee._normalize_dosleg).
        try:
            r["raw"] = json.loads(r.get("raw") or "{}")
        except Exception:
            r["raw"] = {}
        out.append(r)
    return out


def _filter_window(rows: list[dict]) -> list[dict]:
    """Garde uniquement les items dont la date de PUBLICATION est dans la
    fenêtre applicable à leur catégorie (WINDOW_DAYS_BY_CATEGORY sinon
    WINDOW_DAYS).

    Règles :
    - Catégories « strictes » (STRICT_DATED_CATEGORIES, p.ex. publications) :
      on n'accepte QUE les items avec un `published_at` valide ET ≤ now.
      Pas de fallback `inserted_at` (évite que les rapports Sénat CSV sans
      date ou les « pages pivot » scrapées se glissent dans la page), pas
      de dates futures (évite les agendas hebdos annoncés à fin de semaine).
    - Autres catégories : stratégie historique — `published_at` dans la
      fenêtre, sinon fallback `inserted_at` dans la fenêtre.
    """
    now = datetime.utcnow()
    kept = []
    for r in rows:
        cat = r.get("category") or ""
        window = _window_for(cat)
        cutoff = now - timedelta(days=window)
        dt = _parse_dt(r.get("published_at"))
        if cat in STRICT_DATED_CATEGORIES:
            # Strict : impose une published_at valide, non-future, dans la fenêtre.
            if dt is None:
                continue
            if dt > now:
                continue
            if dt >= cutoff:
                kept.append(r)
            continue
        # Catégories non strictes : comportement historique.
        if dt is not None:
            if dt >= cutoff:
                kept.append(r)
            continue
        # Pas de date de publication : on garde si l'insertion est récente
        # (source sans date fiable — on ne fait pas semblant d'en avoir une).
        ins = _parse_dt(r.get("inserted_at"))
        if ins is not None and ins >= cutoff:
            kept.append(r)
    return kept


def _dedup(rows: list[dict]) -> list[dict]:
    """Déduplication par (title, url) — filet de sécurité au-delà du hash_key.

    Le store déduplique déjà par (source_id, uid), mais il arrive qu'un même
    dossier législatif (ou une même question) soit référencé sous plusieurs
    UIDs différents selon le chemin dans le JSON AN (ex : un dossier a un uid
    au niveau racine ET un uid dans dossier.uid, stockés comme 2 items).
    On garde la 1re occurrence (la plus récente, car rows est déjà trié
    par date desc à ce stade).
    """
    seen: set[tuple[str, str]] = set()
    out = []
    dropped = 0
    for r in rows:
        key = (
            (r.get("title") or "").strip().lower(),
            (r.get("url") or "").strip().lower(),
        )
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(r)
    if dropped:
        import logging
        logging.getLogger(__name__).info(
            "site_export : %d doublons (title+url) écartés", dropped,
        )
    return out


def _group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        v = r.get(key) or "autre"
        buckets.setdefault(v, []).append(r)
    return buckets


def _sort_by_date_desc(rows: list[dict]) -> list[dict]:
    """Tri par date de publication décroissante. Les items sans published_at
    sont placés en fin de liste (ils apparaîtront après les items datés).
    On n'utilise PAS inserted_at pour trier — on ne veut pas qu'un item sans
    date officielle remonte en haut juste parce qu'on l'a ingéré aujourd'hui."""
    return sorted(
        rows,
        key=lambda r: (_parse_dt(r.get("published_at")) or datetime.min),
        reverse=True,
    )


def export(rows: list[dict], site_root: str | Path) -> dict:
    """Écrit les fichiers JSON + Markdown dans le site/ Hugo.

    Renvoie {total, par_categorie, par_chambre, recent_24h}.
    """
    root = Path(site_root)
    data = root / "data"
    content = root / "content"
    items_dir = content / "items"
    data.mkdir(parents=True, exist_ok=True)
    (data / "by_category").mkdir(parents=True, exist_ok=True)
    (data / "by_chamber").mkdir(parents=True, exist_ok=True)

    # R12a (2026-04-21) : purge complète de `content/items/` avant regénération.
    # Sinon les .md dont le slug dépend du titre (ex. "compte-rendu-an-crsan…")
    # persistent aux côtés de leur version rebaptisée par `_fix_cr_row`
    # ("s-ance-an-du-18-12-2025-jeux-olympique.md") → doublons visibles sur
    # le site Hugo après rebuild. Purge + recréation garantit que chaque
    # export part d'un contenu frais, cohérent avec l'état DB.
    import shutil
    if items_dir.exists():
        shutil.rmtree(items_dir)
    items_dir.mkdir(parents=True, exist_ok=True)

    # Charge + réparation in-place des CR (URLs AN/Sénat, titres génériques,
    # et surtout `published_at` recalé sur la date de séance AN — les CR
    # Syceron arrivent avec published_at = date d'ingestion).
    # Appliqué AVANT le filtre fenêtre pour que la fenêtre 30j s'applique
    # bien à la date de séance et pas à la date de compression du zip.
    rows = _load(rows)
    for r in rows:
        _fix_cr_row(r)
        _fix_question_row(r)
        _fix_agenda_row(r)
        _fix_dossier_row(r)
        _fix_amendement_row(r)
        _fix_chamber_row(r)
    rows = _filter_window(rows)
    rows = _sort_by_date_desc(rows)
    # Dédup APRÈS tri par date : on garde la version la plus récente en cas
    # de doublons (title+url identique, UID différent).
    rows = _dedup(rows)

    # Index global
    index_payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "window_days": WINDOW_DAYS,
        "total": len(rows),
        "items": rows,
    }
    (data / "index.json").write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    by_cat = _group(rows, "category")
    for cat, lst in by_cat.items():
        (data / "by_category" / f"{cat}.json").write_text(
            json.dumps(lst, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    by_cham = _group(rows, "chamber")
    for cham, lst in by_cham.items():
        (data / "by_chamber" / f"{_slugify(cham)}.json").write_text(
            json.dumps(lst, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    # Index de recherche léger (~400 o/item) pour le moteur côté client.
    # Servi tel quel à /search_index.json via site/static/. Contient titre +
    # URL + category + chamber + date + résumé tronqué + mots-clés, afin de
    # permettre un filtrage full-text en JS sans requête serveur.
    # Clés courtes pour minimiser la taille : t=title, u=url, c=category,
    # ch=chamber, d=date, s=summary court, k=keywords.
    static_dir = root / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    # R13-D : la recherche applique les mêmes règles que la home pour le
    # snippet. Les catégories hors HOMEPAGE_SNIPPET_CATEGORIES n'ont pas
    # d'extrait affiché dans les résultats (on envoie quand même le summary
    # court pour que le filtrage full-text en JS fonctionne, mais le
    # template search.html ne l'affiche que si la catégorie le permet).
    search_items = []
    for r in rows:
        cat = r.get("category") or ""
        s_full = (r.get("summary") or "").strip()
        if len(s_full) > 280:
            s_full = s_full[:280]
        # `s` = snippet affichable ; `si` = summary court pour indexation
        # full-text (toujours présent pour que la recherche trouve même
        # dans les catégories "silencieuses").
        snip_raw = (r.get("snippet") or "").strip()
        show_snip = cat in HOMEPAGE_SNIPPET_CATEGORIES
        search_items.append({
            "t": (r.get("title") or "").strip(),
            "u": (r.get("url") or "").strip(),
            "c": cat,
            "ch": r.get("chamber") or "",
            "d": (r.get("published_at") or "")[:10],
            "s": snip_raw if show_snip else "",
            "si": s_full,
            "k": r.get("matched_keywords") or [],
        })
    (static_dir / "search_index.json").write_text(
        json.dumps({
            "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
            "total": len(search_items),
            "items": search_items,
        }, ensure_ascii=False, separators=(",", ":"), default=str),
        encoding="utf-8",
    )

    # Sidebar agenda : 8 prochains rendez-vous (futurs ou du jour),
    # consommés par layouts/index.html pour afficher un module latéral.
    # On repart de by_cat["agenda"] déjà constitué et on filtre sur les
    # dates à venir. Si rien dans le futur (collecte en retard), on retombe
    # sur les 8 items les plus récents pour garder le module alimenté.
    today_iso = datetime.utcnow().date().isoformat()
    agenda_rows = by_cat.get("agenda", [])
    upcoming = sorted(
        [r for r in agenda_rows if (r.get("published_at") or "")[:10] >= today_iso],
        key=lambda r: (r.get("published_at") or ""),
    )
    if not upcoming:
        # Fallback : 8 plus récents (tous dans le passé mais mieux que vide).
        upcoming = _sort_by_date_desc(agenda_rows)[:8]
    else:
        upcoming = upcoming[:8]
    (data / "sidebar_agenda.json").write_text(
        json.dumps(upcoming, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # R13-G : méta sidebar — date de mise à jour + version système (label
    # cumulé + hash commit court). Consommé par layouts/partials/sidebar.html.
    # Le format de date_str colle à la demande Cyril : "XX/XX/XX à XXhXX"
    # (Paris local, Hugo le ré-affiche tel quel). On calcule à partir de
    # datetime.now() comme pour le reste du site (cohérent avec le footer).
    meta_now = datetime.now()
    short_sha = ""
    try:
        short_sha = subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=str(Path.cwd()),
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8").strip()
    except Exception:
        short_sha = ""
    system_version = (
        f"{SYSTEM_VERSION_LABEL} · {short_sha}"
        if short_sha else SYSTEM_VERSION_LABEL
    )
    (data / "meta.json").write_text(
        json.dumps({
            "updated_at_iso": meta_now.isoformat(timespec="seconds"),
            "updated_at_human": meta_now.strftime("%d/%m/%y à %Hh%M"),
            "system_version": system_version,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Page d'accueil
    recent = _recent(rows, hours=RECENT_HOURS)
    _write_home(content, rows, by_cat, recent)

    # Page de listing par catégorie (_index.md) — nécessaire pour que
    # /items/amendements/ etc. ne donne pas un 404.
    _write_category_indexes(items_dir, by_cat)

    # Une page par item matché
    _write_item_pages(items_dir, rows)

    return {
        "total": len(rows),
        "par_categorie": {k: len(v) for k, v in by_cat.items()},
        "par_chambre": {k: len(v) for k, v in by_cham.items()},
        "recent_24h": len(recent),
        "window_days": WINDOW_DAYS,
    }


def _recent(rows: list[dict], hours: int = 24) -> list[dict]:
    """Items publiés (officiellement) dans les dernières `hours` heures.
    On utilise strictement `published_at` ici — pas `inserted_at` —
    pour que la zone 'dernières 24h' reflète la publication institutionnelle
    réelle, pas la date à laquelle le scraper a inséré en base."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    out = []
    for r in rows:
        dt = _parse_dt(r.get("published_at"))
        if dt and dt >= cutoff:
            out.append(r)
    return out


# ---------- écritures Markdown ---------------------------------------------

def _fmt_item_line(it: dict, with_tags: bool = True,
                    with_snippet: bool = True) -> str:
    """Ligne Markdown d'un item (home / catégorie). Layout :

    - **[Titre](url)** <span class="chamber" data-chamber="AN">AN</span> · Date · tags inline
      <snippet éventuel>

    Si url est vide (typiquement catégorie agenda, cf. `_normalize_agenda`),
    le titre est rendu en texte simple, sans lien cliquable — alignement
    sur Follaw qui affiche les réunions sans hypertexte.

    `with_tags=False` : n'affiche pas les mots-clés. Utilisé par la section
    "Dernières 24 h" pour ne garder que titre + chambre + date (demande
    utilisateur : zone très compacte, les tags encombrent).

    `with_snippet=False` (R13-D) : masque l'extrait. Utilisé par le home
    pour les catégories publications/agenda/jorf/dossiers_legislatifs
    où un extrait n'apporte rien au-delà du titre.
    """
    date = (it.get("published_at") or "")[:10]
    title = (it.get("title") or "").replace("\n", " ").strip()
    url = (it.get("url") or "").strip()
    chamber = it.get("chamber") or ""
    kws = it.get("matched_keywords") or []
    fams = it.get("keyword_families") or []
    # Pair chaque mot-clé avec sa famille (même ordre que matched_keywords).
    # Le matcher ne stocke que les familles uniques, pas la famille de chaque
    # mot. Pour une coloration par famille on ne peut donc que teinter
    # UNIFORMÉMENT via la 1re famille ; acceptable pour un tag visuel.
    dominant_fam = fams[0] if fams else ""
    snippet = (it.get("snippet") or "").replace("\n", " ").strip()

    # Chambre : badge HTML avec data-chamber pour coloration AN/Senat distincte
    chamber_html = ""
    if chamber:
        chamber_html = (
            f'<span class="chamber" data-chamber="{_escape(chamber)}">'
            f'{_escape(chamber)}</span>'
        )

    # Statut procédural (dossiers législatifs) : badge dédié à droite de la
    # chambre, ex. "1ère lecture · commission". Source : raw["status_label"]
    # injecté par assemblee._normalize_dosleg.
    raw = it.get("raw") or {}
    status_label = (raw.get("status_label") or "").strip() if isinstance(raw, dict) else ""
    status_html = ""
    if status_label:
        # On évite d'afficher juste "AN" en doublon avec le badge chambre :
        # status_label commence souvent par "AN · ", on retire ce préfixe.
        clean = status_label
        for prefix in ("AN · ", "Senat · ", "Sénat · "):
            if clean.startswith(prefix):
                clean = clean[len(prefix):]
                break
        if clean:
            promulgated = " status-promulgated" if raw.get("is_promulgated") else ""
            status_html = (
                f'<span class="status{promulgated}">{_escape(clean)}</span>'
            )

    date_html = f'<time class="date">{date}</time>' if date else ""

    # Meta principale (chambre · statut · date) sur une ligne, puis tags sur
    # une 2e ligne dédiée (.meta-tags) — évite que la liste de mots-clés
    # déborde du cadre sur les écrans étroits.
    # Coloration des tags via CSS .kw-tag[data-family=...].
    main_parts = [p for p in [chamber_html, status_html, date_html] if p]
    main_inline = " · ".join(main_parts) if main_parts else ""

    tags_html = ""
    if kws and with_tags:
        tags_html = " ".join(
            f'<span class="kw-tag" data-family="{_escape(dominant_fam)}">'
            f'{_escape(k)}</span>'
            for k in kws[:12]
        )

    meta_html = ""
    if main_inline or tags_html:
        meta_html = ' <span class="item-meta">'
        if main_inline:
            meta_html += f'<span class="meta-main">{main_inline}</span>'
        if tags_html:
            meta_html += f'<span class="meta-tags">{tags_html}</span>'
        meta_html += "</span>"

    # Titre : hypertexte uniquement si on a une URL exploitable.
    # Sinon (ex. réunions AN : pas d'URL publique stable), on affiche
    # le titre en texte gras simple — cf. Follaw.
    if url:
        line = f"- **[{title}]({url})**{meta_html}"
    else:
        line = f"- **{title}**{meta_html}"

    if snippet and with_snippet:
        line += f"  \n  <div class=\"snippet-inline\">« {_escape(snippet)} »</div>"
    return line


def _escape(s: str) -> str:
    """Échappement HTML minimal pour injection dans le Markdown."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def _write_home(content_dir: Path, rows: list[dict], by_cat: dict[str, list[dict]],
                recent: list[dict]):
    now = datetime.now()
    # NB : on ne met pas l'heure dans `date:` pour éviter les pages cachées
    # par Hugo si `date > now()` au moment du build (fuseau navigateur vs UTC).
    lines = [
        "---",
        f'title: "Veille parlementaire sport — {now:%Y-%m-%d}"',
        f'date: {now:%Y-%m-%d}',
        'description: "Veille institutionnelle du sport — actualisée quotidiennement par Sideline Conseil."',
        "---",
        "",
        f"**{len(rows)} publications officielles** dans la fenêtre glissante.",
        "Dernière mise à jour : " + now.strftime("%A %d %B %Y — %H:%M").capitalize() + ".",
        "",
    ]

    # -------- Section top : mises à jour des dernières 24 h ----------
    # Bloc compact (padding réduit, pas de tags) — cf. demande utilisateur
    # pour densifier le haut de page. Les tags restent dans les sections
    # par thématique en dessous, qui servent à la lecture exploratoire.
    lines.append(f"## Dernières 24 h ({len(recent)})")
    lines.append("")
    lines.append('<div class="recent-24">')
    lines.append("")
    if recent:
        for it in recent[:30]:
            lines.append(_fmt_item_line(it, with_tags=False))
    else:
        lines.append("_Aucune nouveauté dans les dernières 24 heures — la collecte reste active._")
    lines.append("")
    lines.append("</div>")
    lines.append("")

    # -------- Sections par catégorie (fenêtre par catégorie) ----------
    # Chaque thématique est rendue dans un <details> repliable, avec le
    # compteur dans le summary. Demande utilisateur : la page d'accueil
    # doit tenir en un coup d'œil, l'utilisateur déplie ce qui l'intéresse.
    lines.append("## Par thématique")
    lines.append("")
    for cat in CATEGORY_ORDER:
        if cat not in by_cat:
            continue
        label = CATEGORY_LABELS.get(cat, cat)
        # R13-G : sous-fenêtre home (ex. dosleg 180j vs 1095j page dédiée).
        # Si la catégorie y figure, on re-filtre le bucket avant l'affichage.
        home_window = HOMEPAGE_WINDOW_DAYS_BY_CATEGORY.get(cat)
        display_window = home_window if home_window is not None else _window_for(cat)
        # Tri explicite du bucket par date desc (plus récent en haut)
        bucket = _sort_by_date_desc(by_cat[cat])
        if home_window is not None:
            cutoff = datetime.now() - timedelta(days=home_window)
            cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
            bucket = [
                it for it in bucket
                if (it.get("published_at") or "") >= cutoff_iso
            ]
        count = len(bucket)
        # Libellé fenêtre : custom pour les catégories mappées (ex. dosleg),
        # sinon format générique "Depuis X jours" (R13-G, remplace l'ancien
        # "fenêtre X j" jugé technique par Cyril).
        window_label = HOMEPAGE_WINDOW_LABEL_BY_CATEGORY.get(cat)
        if window_label is None:
            window_label = f"Depuis {display_window} jours"
        # <details> HTML brut — rendu nativement par tous les navigateurs,
        # pas de JS. `open` n'est PAS positionné par défaut → tout est plié.
        # Le summary contient le compteur et la fenêtre.
        lines.append(f'<details class="cat-fold" data-cat="{_escape(cat)}">')
        lines.append(
            f'<summary><span class="cat-label">{_escape(label)}</span>'
            f' <span class="cat-count">{count}</span>'
            f' <span class="cat-window">{_escape(window_label)}</span>'
            f' <a class="cat-all" href="/items/{cat}/">voir tout →</a>'
            f'</summary>'
        )
        lines.append("")
        # R13-D : snippet uniquement pour catégories pertinentes sur la home.
        show_snip = cat in HOMEPAGE_SNIPPET_CATEGORIES
        for it in bucket[:10]:
            lines.append(_fmt_item_line(it, with_snippet=show_snip))
        if count > 10:
            lines.append("")
            lines.append(f"→ [Voir les {count} {label.lower()}](/items/{cat}/)")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    (content_dir / "_index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_category_indexes(items_dir: Path, by_cat: dict[str, list[dict]]):
    """Écrit un _index.md par catégorie pour que Hugo route /items/<cat>/."""
    for cat in CATEGORY_ORDER:
        d = items_dir / cat
        d.mkdir(parents=True, exist_ok=True)
        label = CATEGORY_LABELS.get(cat, cat)
        count = len(by_cat.get(cat, []))
        window = _window_for(cat)
        # R13-K (2026-04-21) : `type: <cat>` UNIQUEMENT pour agenda et
        # dossiers_legislatifs qui ont un layout spécifique voulu
        # (blocs "À venir/Passé récent" + single dossier législatif).
        # Pour les CR / publications / questions / amendements / JORF, on
        # reste sur _default/list.html (liste plate sans groupement).
        # Cyril (R13-K) : plus de séparation AN/Sénat sur les CR, plus de
        # "Compte rendu intégral/analytique" badge. De plus, le `type:
        # communiques` semblait empêcher Hugo d'indexer les 12 .md sous
        # /items/communiques/ (seuls 3 affichés) — voir R13-K note.
        SPECIFIC_LAYOUT_CATS = {"agenda", "dossiers_legislatifs"}
        lines = [
            "---",
            f'title: "{label}"',
        ]
        if cat in SPECIFIC_LAYOUT_CATS:
            lines.append(f'type: "{cat}"')
        lines += [
            f'description: "Veille {label.lower()} — {count} items sur {window} jours glissants."',
            "---",
            "",
            f"{count} publication{'s' if count > 1 else ''} dans cette catégorie sur les {window} derniers jours.",
            "",
        ]
        (d / "_index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_item_pages(items_dir: Path, rows: list[dict]):
    # On évite l'explosion du nombre de fichiers : on garde les 500 plus récents.
    rows_sorted = _sort_by_date_desc(rows)
    for r in rows_sorted[:500]:
        cat = r.get("category") or "autre"
        d = items_dir / cat
        d.mkdir(parents=True, exist_ok=True)
        slug = _slugify(f"{r.get('source_id','')}-{r.get('uid','')}-{r.get('title','')[:40]}")
        fp = d / f"{slug}.md"
        title = (r.get("title") or "").replace('"', "'")
        # Date réelle de publication uniquement — pas de fallback inserted_at,
        # qui ferait apparaître la date du jour pour les items sans date fiable.
        published_at = r.get("published_at") or ""
        source_url = (r.get("url") or "").replace('"', "")
        # R13-K (2026-04-21) : pour les comptes rendus, on ajoute un
        # text-fragment (#:~:text=<kw>) sur le 1er mot-clé matché. Permet
        # au navigateur (Chrome, Edge, Safari 16.4+) de sauter directement
        # à la 1re occurrence du kw dans la page AN/Sénat. Firefox le
        # dégrade silencieusement (URL normale). Pas d'ancre si pas de kw.
        if cat == "comptes_rendus" and source_url and "#" not in source_url:
            kws = r.get("matched_keywords") or []
            if kws:
                from urllib.parse import quote
                fragment = quote(str(kws[0]), safe="")
                source_url = f"{source_url}#:~:text={fragment}"
        # R13-D : snippet tronqué à la taille demandée pour la page
        # thématique. Si la catégorie n'est pas dans le dict → snippet vide
        # (ex. dossiers_legislatifs : pas d'extrait sur la page dédiée).
        _snip_raw = (r.get("snippet") or "").replace('"', "'").replace("\n", " ")
        _snip_len = SNIPPET_LEN_BY_CATEGORY.get(cat)
        if _snip_len is None:
            snippet = ""
        elif len(_snip_raw) > _snip_len:
            # Tronque sans couper un mot (recherche dernier espace dans la zone)
            cut = _snip_raw[:_snip_len].rstrip()
            last_space = cut.rfind(" ")
            if last_space > _snip_len - 50:
                cut = cut[:last_space].rstrip()
            snippet = cut + "…"
        else:
            snippet = _snip_raw
        # Remonte les champs enrichis depuis `raw` pour les dossiers
        # législatifs (status_label + is_promulgated injectés par
        # assemblee._normalize_dosleg). Permet à list.html d'afficher le
        # badge de statut sur /items/dossiers_legislatifs/.
        raw = r.get("raw") or {}
        # NB : _fix_cr_row a déjà réécrit r["url"] / r["title"] pour les CR
        # pré-patch au tout début d'export() — pas de post-process ici.
        status_label = ""
        is_promulgated = False
        actes_timeline: list[dict] = []
        nb_actes_utiles = 0
        auteur_label = ""
        auteur_groupe = ""
        auteur_url = ""
        if isinstance(raw, dict):
            status_label = (raw.get("status_label") or "").strip()
            is_promulgated = bool(raw.get("is_promulgated"))
            # On retire le préfixe "AN · " ou "Senat · " pour éviter le
            # doublon visuel avec le badge chambre (cf. _fmt_item_line).
            for prefix in ("AN · ", "Senat · ", "Sénat · "):
                if status_label.startswith(prefix):
                    status_label = status_label[len(prefix):]
                    break
            # Timeline des actes (dossiers législatifs) — exposée au layout
            # `dossiers_legislatifs/single.html` pour rendre la maquette AN.
            timeline = raw.get("actes_timeline")
            if isinstance(timeline, list):
                actes_timeline = [a for a in timeline if isinstance(a, dict)]
            nb_actes_utiles = int(raw.get("nb_actes_utiles") or 0)
            # Auteur (Questions) : label + groupe + URL fiche député AN/Sénat.
            # Injecté par assemblee._normalize_question (auteur_url est construit
            # depuis acteurRef si PAxxxx). Consommé par single.html / list.html
            # pour rendre l'auteur cliquable vers la fiche député.
            auteur_label = (raw.get("auteur") or "").strip()
            auteur_groupe = (raw.get("groupe") or "").strip()
            auteur_url = (raw.get("auteur_url") or "").strip()
            # Ré-résolution à l'export : certains items (pre-patch AMO) ont
            # été ingérés avant que le cache PA→nom soit rempli et gardent
            # "Député PAxxxx" / "PAxxxx" dans raw.auteur. On refait passer
            # via amo_loader pour afficher le vrai nom.
            auteur_ref = (raw.get("auteur_ref") or "").strip()
            needs_resolve = (
                (not auteur_label)
                or auteur_label.startswith("Député PA")
                or bool(re.match(r"^PA\d+$", auteur_label))
            )
            if needs_resolve and auteur_ref.startswith("PA"):
                resolved = amo_loader.resolve_acteur(auteur_ref)
                if resolved:
                    auteur_label = resolved
                    if not auteur_groupe:
                        auteur_groupe = amo_loader.resolve_groupe(auteur_ref) or ""
            # Groupe : résoudre POxxx → abrégé si pas encore résolu.
            if auteur_groupe.startswith("PO") and auteur_groupe[2:].isdigit():
                grp_lib = amo_loader.resolve_organe(auteur_groupe, prefer_long=False)
                if grp_lib:
                    auteur_groupe = grp_lib
            # URL fiche député : reconstruit si manquant mais acteurRef connu.
            if not auteur_url and auteur_ref.startswith("PA") and auteur_ref[2:].isdigit():
                auteur_url = f"https://www.assemblee-nationale.fr/dyn/deputes/{auteur_ref}"
            # Titre des questions : si le titre embarqué contient encore le
            # code "Député PAxxxx" (item pre-patch), on le réécrit avec le
            # nom résolu. Évite un reset DB complet.
            if auteur_label and title:
                title = re.sub(r"Député PA\d+", auteur_label, title)
        status_label = status_label.replace('"', "'")

        fm = [
            "---",
            f'title: "{title}"',
        ]
        if published_at:
            fm.append(f"date: {published_at}")
        fm += [
            f"category: {cat}",
            f'chamber: "{r.get("chamber") or ""}"',
            f'source: "{r.get("source_id") or ""}"',
            f'source_url: "{source_url}"',
            f"keywords: {json.dumps(r.get('matched_keywords') or [], ensure_ascii=False)}",
            f"families: {json.dumps(r.get('keyword_families') or [], ensure_ascii=False)}",
            f'snippet: "{snippet}"',
            f'status_label: "{status_label}"',
            f"is_promulgated: {str(is_promulgated).lower()}",
        ]
        if auteur_label:
            fm.append(f'auteur: "{auteur_label.replace(chr(34), chr(39))}"')
        if auteur_groupe:
            fm.append(f'auteur_groupe: "{auteur_groupe.replace(chr(34), chr(39))}"')
        if auteur_url:
            fm.append(f'auteur_url: "{auteur_url}"')
        # R13-J (2026-04-21) — patch 16 : chip sort/état pour les amendements.
        # Priorité `sort` (libellé final en séance / commission), fallback
        # `etat` (transitoire). Slug normalisé pour ciblage CSS (accents
        # retirés, lowercase, kebab-case).
        if cat == "amendements" and isinstance(raw, dict):
            sort_label = (raw.get("sort") or "").strip()
            etat_label = (raw.get("etat") or "").strip()
            chip_label = sort_label or etat_label
            if chip_label:
                try:
                    from unidecode import unidecode as _uni
                    slug = _uni(chip_label).lower()
                    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
                except Exception:
                    slug = re.sub(r"[^a-z0-9]+", "-", chip_label.lower()).strip("-")
                fm.append(
                    f'sort_label: "{chip_label.replace(chr(34), chr(39))}"')
                fm.append(f'sort_slug: "{slug}"')
        # Frontmatter étendu pour les dossiers législatifs (timeline).
        if cat == "dossiers_legislatifs" and actes_timeline:
            fm.append(f"nb_actes_utiles: {nb_actes_utiles}")
            fm.append("actes_timeline:")
            for a in actes_timeline:
                fm.append("  - date: \"" + str(a.get("date", ""))[:10] + "\"")
                fm.append("    code: \"" + str(a.get("code", "")).replace('"', "'") + "\"")
                fm.append("    libelle: \"" + str(a.get("libelle", "")).replace('"', "'") + "\"")
                fm.append("    institution: \"" + str(a.get("institution", "")) + "\"")
                fm.append("    stage: \"" + str(a.get("stage", "")) + "\"")
                fm.append("    step: \"" + str(a.get("step", "")) + "\"")
                fm.append("    is_promulgation: " + str(bool(a.get("is_promulgation"))).lower())
        # Frontmatter étendu pour les comptes rendus (Sénat + AN) :
        # expose report_type ("analytique" | "integral") et report_label
        # ("Compte rendu analytique" | "Compte rendu intégral") pour que
        # le template comptes_rendus/list.html puisse rendre un badge
        # distinct sans re-parser le titre.
        if cat == "comptes_rendus" and isinstance(raw, dict):
            report_type = (raw.get("report_type") or "").strip()
            report_label = (raw.get("report_label") or "").strip().replace('"', "'")
            if report_type:
                fm.append(f'report_type: "{report_type}"')
            if report_label:
                fm.append(f'report_label: "{report_label}"')
        fm += [
            "---",
            "",
            (r.get("summary") or "").strip(),
            "",
        ]
        # Bouton "Consulter la source" : seulement si on a une vraie URL.
        # Les réunions AN n'en ont pas (cf. commentaire dans _normalize_agenda).
        if source_url:
            fm.append(f"[Consulter la source officielle]({source_url})")
        fp.write_text("\n".join(fm), encoding="utf-8")
