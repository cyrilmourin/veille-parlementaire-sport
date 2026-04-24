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
SYSTEM_VERSION_LABEL = "R36"

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
    # Agenda : R13-L (2026-04-21) — retour à 30j (Cyril : "tu peux te
    # contenter des 30 derniers jours et futurs"). Les séances futures
    # ne sont pas filtrées par `published_at >= cutoff` (elles sont > now
    # donc > cutoff), donc 30j garde tout le futur + les 30 derniers jours.
    "agenda": 30,
    # Publications (communiqués de presse, actualités ministères / autorités
    # indépendantes) : 3 mois par défaut. R36-J (2026-04-24) : la sous-
    # catégorie « rapports parlementaires » (senat_rapports + an_rapports)
    # bénéficie d'une fenêtre élargie à 2 ans via WINDOW_DAYS_BY_SOURCE_ID
    # ci-dessous (un rapport reste référent beaucoup plus longtemps qu'un
    # communiqué, Cyril voulait pouvoir remonter à ~730 j sur cette seule
    # sous-catégorie sans dégrader la pertinence du bucket principal).
    "communiques": 90,
    # Comptes rendus : 6 mois. Avant R11g (UX-E), la fenêtre 30j tuait
    # quasi tous les CR sport — `_fix_cr_row` recale `published_at` sur la
    # vraie date de séance (extraite du nom de fichier d20260205.xml ou du
    # XML Syceron), et beaucoup de séances sport pertinentes (JO 2030,
    # dopage, ANS) datent de plusieurs mois. Les CR sont par ailleurs peu
    # nombreux et restent référents longtemps : 180j est un meilleur
    # compromis.
    "comptes_rendus": 180,
    # R22h (2026-04-23) : questions → 3 mois (au lieu de 30j par défaut).
    # Cyril veut aligner la fenêtre sur l'attente utilisateur ("un dépôt ou
    # une réponse depuis moins de 3 mois"). Le volume de questions reste
    # maîtrisé et 90j permet de capter celles dont la réponse JO est
    # publiée bien après le dépôt. Couplé à l'ajout de `questions` dans
    # STRICT_DATED_CATEGORIES ci-dessous, ça garantit qu'aucun item sans
    # `published_at` valide ne passe via le fallback `inserted_at`.
    "questions": 90,
    # R36-G (2026-04-24) : amendements 30 → 90j. Cyril veut la même fenêtre
    # que les questions — un amendement déposé en commission il y a 2 mois
    # peut encore être utile au suivi, et certaines navettes durent > 30j.
    # Le volume reste maîtrisé (quelques amendements matchés / mois côté
    # sport).
    "amendements": 90,
    # R36-K (2026-04-24) : JORF 30 → 90j. Cyril : la fenêtre 30j faisait
    # sortir du radar des arrêtés sport intéressants pris il y a ~2 mois
    # (nominations mises à part, elles ont leur propre page).
    "jorf": 90,
}

# R36-J (2026-04-24) — Override par source_id pour la sous-catégorie
# « rapports parlementaires » (AN + Sénat). Les rapports d'information
# durent longtemps en pertinence éditoriale (mission d'information de
# plusieurs mois, rapports annuels d'activité des commissions). 90j de
# communiques était trop court pour cette sous-catégorie sans pour autant
# vouloir étendre tout le bucket. Clé = source_id, valeur = jours.
# `_window_for` accepte désormais un `source_id` optionnel pour prioriser
# cet override sur la fenêtre catégorie.
WINDOW_DAYS_BY_SOURCE_ID: dict[str, int] = {
    "an_rapports": 730,     # 2 ans — rapports AN (R28)
    "senat_rapports": 730,  # 2 ans — rapports Sénat
}

# Catégories pour lesquelles on exige une vraie `published_at` ≤ now (pas de
# fallback `inserted_at`, pas de dates futures). Ça évite que la page
# Publications se fasse polluer par :
#   - des rapports Sénat CSV livrés sans date de dépôt,
#   - des flux RSS legacy sans <pubDate>,
#   - des « pages pivot » scrapées par html_generic (« Page suivante », « Presse »),
#   - des agendas hebdo datés en fin de semaine à venir.
#
# R22h (2026-04-23) : `questions` ajouté. Sans ça, un item AN sans
# `published_at` (XSD parfois vide sur dateJO/dateCloture/dateDepot) passait
# via le fallback `inserted_at` même s'il avait été déposé il y a > 90j.
# Cyril a signalé 17-11612QE (publié 2025-12-09, ~135j) visible en prod.
STRICT_DATED_CATEGORIES = {"communiques", "dossiers_legislatifs", "questions"}

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
    # R25-B (2026-04-23) : amendements 250 -> 500, CR 500 -> 800 (Cyril).
    "amendements": 500,
    "questions": 250,
    "comptes_rendus": 800,
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

# R22i (2026-04-23) : pattern legacy cassé pour les URLs des questions Sénat.
# Format vu en DB : `https://www.senat.fr/questions/base/{uid}.html` (ex.
# `.../base/1054S.html`) — ce n'est PAS une URL valide côté senat.fr, la vraie
# URL contient un segment YYYY et le préfixe `qSEQ…` (ex.
# `.../base/2026/qSEQ26041054S.html`). On réécrit depuis `raw["URL Question"]`
# dans `_fix_question_row` pour les items ingérés avant R22i.
_SENAT_QUESTION_LEGACY_URL_RE = re.compile(
    r"^https?://www\.senat\.fr/questions/base/[^/]+\.html$"
)


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

    # 0ter) R23-D (2026-04-23) : réécrit le préfixe legacy
    #       "Question de +1 an sans réponse n°… : …" → "Question écrite n°… : …".
    #       Le CSV Sénat `senat_questions_1an` listait les questions sans
    #       réponse depuis >1 an, mais la date visible est souvent récente
    #       (re-dépôt automatique) donc l'étiquette "+1 an sans réponse"
    #       était trompeuse dans le titre. Le sid d'origine reste intact
    #       côté source_id (compteurs digest, filtrage). Idempotent.
    title = re.sub(
        r"^Question\s+de\s+\+1\s+an\s+sans\s+réponse\b",
        "Question écrite",
        title,
    )

    # 0quater) R25-C (2026-04-23) : dédup QAG vs question écrite.
    #          Cyril a signalé la question n°0701G apparaissant sous le
    #          libellé « Question écrite n°0701G » — impossible, le suffixe
    #          « G » dans la numérotation Sénat indique une Question au
    #          Gouvernement (QAG), jamais une question écrite (suffixe « S »).
    #          Origine du bug : le CSV `senat_questions_1an` (ou certains
    #          dumps historiques) contient parfois des QAG avec le label
    #          "Question écrite", ce qui double avec l'entrée réelle du
    #          CSV `senat_qg`. On remappe ici le libellé sur le suffixe
    #          « G » pour rétablir la cohérence. La passe `_dedup` (title,
    #          url) écarte ensuite le doublon si les deux rows existent.
    #          Idempotent. Pattern large pour couvrir toute variation de
    #          libellé Question * (écrite, orale sans débat, etc.) qui
    #          aurait un numéro en G.
    m_qag = re.match(r"^(Question[^\n]*?)\s+n°\s*(\d+G)\b", title)
    if m_qag:
        label_found = m_qag.group(1).strip()
        num_g = m_qag.group(2)
        if label_found.lower() != "question au gouvernement":
            title = re.sub(
                r"^Question[^\n]*?\s+n°\s*\d+G\b",
                f"Question au gouvernement n°{num_g}",
                title,
                count=1,
            )

    # 0quinquies) R25b-C (2026-04-23) : reclasse les items Sénat où le titre
    #             indique "Question écrite" alors que le CSV a Nature='QOSD'
    #             (question orale sans débat) ou Nature='QO' (question orale).
    #             Cas typique : n°1054S — suffixe « S » donc pas une QAG, mais
    #             le CSV senat_questions_1an mixe QE/QOSD/QG, et l'ancien
    #             mappage figé classait tout comme "Question écrite".
    #             Idempotent : ne touche que si le label courant n'est pas
    #             déjà aligné sur raw.Nature.
    if isinstance(raw, dict):
        nature_csv = (raw.get("Nature") or raw.get("nature") or "").strip().upper()
        nature_label_map = {
            "QOSD": "Question orale sans débat",
            "QO": "Question orale",
            "QG": "Question au gouvernement",
            "QE": "Question écrite",
        }
        target_label = nature_label_map.get(nature_csv)
        if target_label:
            cur_prefix_m = re.match(r"^(Question[^\n:—]*?)(?=\s+n°|\s*:|\s*$)", title)
            if cur_prefix_m:
                cur_label = cur_prefix_m.group(1).strip()
                if cur_label.lower() != target_label.lower():
                    title = re.sub(
                        r"^Question[^\n:—]*?(?=\s+n°|\s*:|\s*$)",
                        target_label,
                        title,
                        count=1,
                    )

    # 0sexies) R25b-B (2026-04-23) : retire le fragment « n°<numéro> »
    #          (Sénat : 1054S, 0701G, 08141 ; AN : rarement présent) du titre
    #          des questions, pour harmoniser avec le format AN épuré
    #          « Question écrite : sujet ». Le numéro reste stocké dans
    #          raw.Numéro pour dédup/matching. Idempotent. On tolère un espace
    #          de séparateur avant les deux-points suivants, et on normalise
    #          les espaces multiples.
    title = re.sub(
        r"(?i)^(Question[^\n]*?)\s+n°\s*[A-Z0-9]+\b",
        r"\1",
        title,
    )

    # 0bis) R13-L (2026-04-21) : retire l'auteur + groupe du titre
    #       (demande Cyril : l'auteur est déjà affiché comme .auteur-inline
    #       cliquable AVANT le titre, avec une barre verticale séparateur).
    #       Pattern : "Question ... — {Auteur} ({Groupe}) : {sujet}" →
    #       "Question ... : {sujet}". Idempotent.
    title = re.sub(
        r"\s+—\s+[A-ZÀ-Ÿ][^()—]+?\s*\([^)]+\)\s*:\s*",
        " : ",
        title,
    )
    # Sans groupe entre parenthèses (cas rare Sénat) :
    title = re.sub(
        r"\s+—\s+(?:M\.|Mme|Mlle)\s+[^:]+?\s*:\s*",
        " : ",
        title,
    )

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

    # 4bis) R22i (2026-04-23) : répare l'URL des questions Sénat ingérées
    #       avant R22i. Le parser tombait sur le fallback
    #       `https://www.senat.fr/questions/base/{uid}.html` qui renvoie un
    #       404 côté senat.fr. La vraie URL est dans `raw["URL Question"]`
    #       (colonne CSV), en `http://…/base/YYYY/qSEQYYMM<num>.html`.
    #       `upsert_many` ne re-renormalise pas l'URL des hash_key existants,
    #       d'où la rustine ici. Idempotent : ne touche que si l'URL courante
    #       correspond au pattern legacy cassé et si raw["URL Question"] est
    #       exploitable.
    src_id = (r.get("source_id") or "")
    if src_id in {"senat_qg", "senat_questions_1an", "senat_questions"}:
        cur_url = (r.get("url") or "")
        if _SENAT_QUESTION_LEGACY_URL_RE.match(cur_url):
            raw_url = ""
            if isinstance(raw, dict):
                raw_url = (raw.get("URL Question") or raw.get("URL") or "").strip()
            if raw_url.startswith("http://"):
                raw_url = "https://" + raw_url[len("http://"):]
            if raw_url.startswith("https://www.senat.fr/questions/base/"):
                r["url"] = raw_url

    # 5) R19-C (2026-04-23) : retire du summary le préfixe redondant
    #    "{Auteur} ({Groupe}) — " (ou "Député PAxxx (Groupe) — "), puisque
    #    l'auteur est déjà affiché en en-tête de carte (auteur-inline). Le
    #    summary doit aller direct à "Destinataire : …" pour un snippet utile.
    summary = (r.get("summary") or "")
    if summary:
        new_summary = re.sub(
            r"^(?:M\.|Mme|Mlle|Député)\s+[^()—]+?\s*\([^)]+\)\s*(?:—|-)\s*",
            "",
            summary,
        )
        # Cas fallback : préfixe "Député PAxxx — " sans groupe
        new_summary = re.sub(
            r"^Député\s+PA\d+\s*(?:—|-)\s*",
            "",
            new_summary,
        )
        if new_summary != summary:
            r["summary"] = new_summary[:2000]


# R23-F (2026-04-23) — marqueurs de début du corps d'un CR AN.
# Le summary des CR AN (source Syceron) commence TOUJOURS par un préambule
# de métadonnées techniques (identifiants CRSANR…, RUANR…, SCR5A…,
# timestamps ISO, libellés "Session ordinaire", "valide complet public",
# "avant_JO PROD", numéros de séance isolés `1 130 AN 17 …`, etc.) avant
# de livrer le vrai débat. Ce préambule monopolise le début du haystack et
# évince le match du centre de l'extrait (le matcher build_snippet retient
# autour de la 1ère occurrence du keyword : si elle est à l'intérieur du
# préambule, on affiche une chaîne d'IDs techniques).
#
# Les marqueurs canoniques du début de corps (ordre de priorité) :
#   1. "Présidence de …" — quasi-systématique sur les CR de séance.
#   2. "Questions au gouvernement" — CR QAG.
#   3. "La séance est ouverte" / "La commission …" — fallback.
# Avant R23-F (R19-G), on testait une regex stricte sur le préambule et on
# ne coupait que si le résidu non-matché était <20 chars — trop timide,
# les numéros de séance isolés échappaient à la regex et empêchaient la
# coupe. R23-F : si l'un des marqueurs est trouvé dans les 600 premiers
# caractères, on coupe dessus sans condition.
_CR_AN_BODY_MARKERS = (
    "Présidence",
    "Questions au gouvernement",
    "La séance est ouverte",
    "La commission",
)


def _strip_cr_an_preamble(haystack: str, max_prefix: int = 600) -> str:
    """Retire le préambule technique Syceron d'un summary de CR AN.

    Cherche le premier marqueur de début de corps (« Présidence », etc.)
    dans les `max_prefix` premiers caractères. Si trouvé, renvoie le
    haystack à partir du marqueur. Sinon, renvoie haystack tel quel.
    Idempotent (appliquer deux fois donne le même résultat).
    """
    if not haystack:
        return haystack
    best_idx = -1
    for marker in _CR_AN_BODY_MARKERS:
        idx = haystack.find(marker)
        # idx == 0 (marqueur déjà au début, cf. appels idempotents) est
        # accepté pour bloquer une re-coupe sur un marqueur *ultérieur*.
        if 0 <= idx <= max_prefix and (best_idx < 0 or idx < best_idx):
            best_idx = idx
    if best_idx > 0:
        return haystack[best_idx:]
    return haystack


# R23-N (2026-04-23) — enrichissement des questions Sénat par le cache
# amendements Sénat pour exposer `auteur_photo_url` + `auteur_url`.
#
# Contexte :
#   - Les CSV « amendements » Sénat contiennent une colonne « Fiche Sénateur »
#     (cf. src/sources/senat_amendements.py) qui permet de construire
#     l'URL du portrait carré via amo_loader.build_photo_url_senat().
#   - Les CSV « questions » Sénat (senat_qg, senat_questions, senat_questions_1an)
#     n'ont PAS cette colonne — on ne peut donc pas peupler auteur_photo_url
#     à l'ingestion. D'où l'absence de portrait sur /items/questions/ côté
#     Sénat, alors que les portraits AN étaient déjà servis via amo_loader
#     (cache local PA→photo) et que les portraits Sénat sur amendements
#     fonctionnent depuis R23-C5.
#
# Approche : on indexe les rows d'amendements Sénat par un nom d'auteur
# normalisé (accent-free, civilité retirée, tokens triés). Puis on re-passe
# sur les rows de questions Sénat et on injecte auteur_photo_url / auteur_url
# quand le nom matche. Coût nul en réseau : toute l'info est déjà en mémoire.

_CIV_TOKENS = {"m.", "mme", "mlle", "dr", "pr", "m", "mme.", "mlle."}


def _normalize_auteur_name_senat(name: str) -> str:
    """Normalise un nom de parlementaire pour lookup :
    - passe en minuscules sans accents,
    - retire la civilité (M. / Mme / Mlle / Dr / Pr),
    - découpe en tokens, supprime tokens vides, trie alphabétiquement.

    Résultat : « M. Dany WATTEBLED » et « WATTEBLED Dany » donnent tous
    deux la même clé `"dany wattebled"`. Insensible à l'ordre nom/prénom
    (le CSV Sénat livre tantôt "Nom Prénom", tantôt "Prénom Nom" selon
    la source).
    """
    if not name:
        return ""
    from unidecode import unidecode
    s = unidecode(name).lower().strip()
    # Retire ponctuation courante (points des civilités, virgules).
    s = re.sub(r"[.,;]", " ", s)
    tokens = [t for t in s.split() if t and t not in _CIV_TOKENS]
    if not tokens:
        return ""
    return " ".join(sorted(tokens))


def _build_senat_photo_cache(rows: list[dict]) -> dict[str, tuple[str, str]]:
    """Scanne les rows d'amendements Sénat et construit un index
    `{clé_nom_normalisée: (auteur_photo_url, auteur_url)}` utilisable
    pour enrichir les questions Sénat.

    Les rows en entrée sont le retour de `_load(rows)` : `raw` est déjà
    parsé en dict et contient `auteur`, `auteur_url`, `auteur_photo_url`
    tels qu'exposés par le parser senat_amendements.

    R25b-A (2026-04-23) : deux sources complémentaires alimentent désormais
    le cache :
      1. Les amendements Sénat ingérés avec R23-C5+ (photo peuplée dans raw).
      2. L'index officiel `data/senat_slugs.json` (348 sénateurs en
         activité), chargé via `senat_slugs.resolve_by_auteur`. Couvre
         les amendements pré-R23-C5 dont raw.auteur_photo_url est vide
         ET tous les sénateurs qui ne déposent pas d'amendements dans
         la fenêtre. Les QAG Sénat en bénéficient via `_enrich_senat_question_photo`.
    Source (1) reste prioritaire (URL déjà exposée et vérifiée côté
    parser). Source (2) sert de fallback quand (1) est vide.
    """
    from . import senat_slugs
    cache: dict[str, tuple[str, str]] = {}
    for r in rows:
        if (r.get("category") or "") != "amendements":
            continue
        if (r.get("chamber") or "") != "Senat":
            continue
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        auteur = (raw.get("auteur") or "").strip()
        key = _normalize_auteur_name_senat(auteur)
        if not key:
            continue
        photo = (raw.get("auteur_photo_url") or "").strip()
        fiche = (raw.get("auteur_url") or "").strip()
        # Fallback R25b-A : si l'amendement n'a pas de photo peuplée (pre
        # R23-C5), on tente la résolution via l'index officiel Sénat.
        # Permet aussi de backfill raw pour que le frontmatter amendement
        # (cf. site_export section "auteur_photo_url backfill AN") affiche
        # un portrait.
        if not photo:
            hit = senat_slugs.resolve_by_auteur(auteur)
            if hit:
                photo = photo or hit[0]
                fiche = fiche or hit[1]
                # On écrit dans raw pour que la passe d'export Markdown
                # (qui relit raw.auteur_photo_url) profite aussi du fallback
                # sur les amendements eux-mêmes, pas juste sur les questions.
                if photo and not raw.get("auteur_photo_url"):
                    raw["auteur_photo_url"] = photo
                if fiche and not raw.get("auteur_url"):
                    raw["auteur_url"] = fiche
                r["raw"] = raw
        if not photo and not fiche:
            continue
        # Si plusieurs rows pour le même sénateur, la première suffit
        # (même slug senfic → même portrait).
        cache.setdefault(key, (photo, fiche))
    return cache


def _enrich_senat_question_photo(r: dict, cache: dict[str, tuple[str, str]]) -> None:
    """Injecte `auteur_photo_url` / `auteur_url` dans raw pour une question
    Sénat dont l'auteur est connu via les amendements de la même période
    OU via l'index officiel Sénat (R25b-A).

    Ordre de résolution :
      1. Cache R23-N (amendements de la fenêtre) — data locale fraîche.
      2. Index officiel `senat_slugs.resolve_photo()` — 348 sénateurs en
         activité, couvre les QAG dont l'auteur n'a pas déposé d'amendement
         dans la fenêtre.

    Idempotent : ne touche pas aux rows déjà pourvus d'une photo, ne
    touche pas aux rows non-Sénat.
    """
    if (r.get("category") or "") != "questions":
        return
    if (r.get("chamber") or "") != "Senat":
        return
    raw = r.get("raw") if isinstance(r.get("raw"), dict) else None
    if not isinstance(raw, dict):
        return
    # Déjà enrichi (par amo_loader ou run précédent) : ne rien faire.
    if (raw.get("auteur_photo_url") or "").strip():
        return
    # Reconstruit le nom depuis les colonnes CSV Sénat si dispo, sinon
    # tombe sur raw["auteur"].
    civ = (raw.get("Civilité") or raw.get("civilite") or "").strip()
    prenom = (raw.get("Prénom") or raw.get("prenom") or "").strip()
    nom = (raw.get("Nom") or raw.get("nom") or "").strip()
    full = (raw.get("auteur") or "").strip()
    candidate = " ".join(p for p in [civ, prenom, nom] if p).strip() or full
    key = _normalize_auteur_name_senat(candidate)
    if not key:
        return
    photo, fiche = "", ""
    # 1) Priorité au cache R23-N (amendements Sénat de la fenêtre).
    if cache:
        hit = cache.get(key)
        if hit:
            photo, fiche = hit
    # 2) Fallback R25b-A : index officiel Sénat (couvre les 348 sénateurs).
    if not photo:
        from . import senat_slugs
        hit2 = senat_slugs.resolve_photo(civ, prenom, nom) or senat_slugs.resolve_by_auteur(full)
        if hit2:
            photo = photo or hit2[0]
            fiche = fiche or hit2[1]
    if not photo and not fiche:
        return
    if photo:
        raw["auteur_photo_url"] = photo
    if fiche and not raw.get("auteur_url"):
        raw["auteur_url"] = fiche
    r["raw"] = raw


# R23-H (2026-04-23) — Regroupement des sources en 5 familles pour le
# filtre UI exposé sur les pages /items/agenda/ et /items/communiques/.
# Retourne un slug stable utilisé en `data-family-source` côté template.
#
# R23-O (2026-04-23) — refonte à 5 familles stables validées par Cyril :
#   1. parlement           : flux AN + Sénat (chambre AN / Senat).
#   2. gouvernement        : Élysée, Matignon, info.gouv, ministères
#                            (préfixes `elysee_`, `matignon_`,
#                            `info_gouv_`, `min_`), agendas ministériels.
#   3. operateurs_publics  : établissements/services publics rattachés
#                            aux ministères Sports / ESR (ANS, INSEP,
#                            INJEP, AFLD [R25-F : EPA sport],
#                            IGESR [R25-F : inspection État]).
#   4. autorites           : AAI + hautes juridictions (ARCOM, ANJ,
#                            Autorité concurrence, Défenseur droits,
#                            Conseil d'État, Conseil constitutionnel,
#                            Cour des comptes).
#   5. mouvement_sportif   : associations / fondations sport (CNOSF,
#                            CPSF/France paralympique, FDSF).
#
# Changements R23-O vs R23-H :
#   - L'ancienne famille `operateurs` est splittée en deux :
#       ans + injep + insep       -> `operateurs_publics`
#       cnosf + france_paralympique + fdsf -> `mouvement_sportif`
#   - La famille `jorf` est RETIRÉE du filtre : /items/jorf/ a sa page
#     dédiée dans la nav principale, le JORF n'apparaît pas en catégorie
#     `communiques` ni `agenda`. Le mapping `dila_jorf` est conservé
#     pour `chamber` fallback mais n'est plus servi aux boutons filtre.
#
# Si un source_id/chamber ne matche rien, on retombe sur "autres" — le
# filtre UI affiche alors un bucket générique.
_SOURCE_FAMILY_BY_PREFIX = (
    ("an_", "parlement"),
    ("senat_", "parlement"),
    ("matignon_", "gouvernement"),
    ("info_gouv_", "gouvernement"),
    ("elysee_", "gouvernement"),
    ("min_", "gouvernement"),
    ("dila_jorf", "jorf"),
)
_SOURCE_FAMILY_BY_ID = {
    # Autorités administratives indépendantes + hautes juridictions
    # (R23-O : regroupées dans le bucket "autorites" à la demande de
    # Cyril, plutôt qu'éclatées AAI vs Juridictions).
    "anj": "autorites",
    "arcom": "autorites",
    "autorite_concurrence": "autorites",
    "conseil_constit_actualites": "autorites",
    "conseil_constit_decisions": "autorites",
    "conseil_etat": "autorites",
    "defenseur_droits": "autorites",
    "ccomptes_publications": "autorites",
    # R25-F (2026-04-23) : AFLD et IGESR ne sont pas des autorités
    # indépendantes : AFLD = EPA rattaché au ministère des Sports,
    # IGESR = inspection interministérielle (service État). Ils vont
    # désormais dans "operateurs_publics" pour matcher la lecture
    # métier de Cyril (filtre Publications + Agenda).
    "afld": "operateurs_publics",
    "igesr_rapports": "operateurs_publics",
    # R23-O : opérateurs publics (établissements / services publics
    # rattachés aux ministères Sports / Jeunesse). Séparés du mouvement
    # sportif associatif pour donner deux boutons distincts au filtre.
    "ans": "operateurs_publics",
    "injep": "operateurs_publics",
    "insep": "operateurs_publics",
    # R23-O : mouvement sportif (associations RUP + fondation RUP
    # adossée au CNOSF). Catégorie séparée des opérateurs publics.
    "cnosf": "mouvement_sportif",
    "france_paralympique": "mouvement_sportif",
    "fdsf": "mouvement_sportif",
}


def _source_family(source_id: str | None, chamber: str | None = None) -> str:
    """Retourne le slug de famille pour un couple (source_id, chamber).

    Cf. _SOURCE_FAMILY_BY_PREFIX / _SOURCE_FAMILY_BY_ID. Fallback
    "autres" si aucune règle ne matche — le template affichera le bucket
    générique.
    """
    sid = (source_id or "").strip().lower()
    if sid:
        # Match id exact d'abord (plus specifique).
        if sid in _SOURCE_FAMILY_BY_ID:
            return _SOURCE_FAMILY_BY_ID[sid]
        for prefix, family in _SOURCE_FAMILY_BY_PREFIX:
            if sid.startswith(prefix):
                return family
    # Fallback chamber-only (utile pour les items sans source_id propre).
    ch = (chamber or "").strip().lower()
    if ch in ("an", "senat", "sénat"):
        return "parlement"
    if ch in ("jorf",):
        return "jorf"
    return "autres"


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

    # Étape 3 — R13-O (2026-04-21) : retire l'auteur + groupe + statut
    # éventuel du titre. Cyril : l'auteur est déjà affiché AVANT le titre
    # via .auteur-inline, comme pour les questions. Idempotent.
    t = r.get("title") or ""
    # 3a) Retire "[Discuté]" / "[Non soutenu]" / etc. (ancien sort inline).
    t = re.sub(r"\s*\[[^\]]+\]", "", t)
    # 3b) Retire "— Auteur (Groupe)" juste après "Amdt n°X".
    # Patterns couverts :
    #   "Amdt n°12 — M. Dupont (LFI) · art. 3 · sur …"
    #   "Amdt n°12 rect. — Mme Y · art. 3"
    # La regex s'arrête au premier `·` (séparateur vers article / dossier).
    t = re.sub(
        r"(Amdt n°[^—·]+?)\s*—\s+[^·]+?(?=\s*·|$)",
        r"\1 ",
        t,
    )
    t = re.sub(r"\s{2,}", " ", t).strip()
    if t != r.get("title"):
        r["title"] = t[:220]


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

    R13-L (2026-04-21) : détecte aussi les dossiers retirés pour afficher
    le badge "Retiré" (fond rouge foncé). Critères :
      - `raw.is_retire` True (flag explicite posé par le parser si dispo)
      - OR `raw.status_label` contient "retrait" (cas où le parser AN a
        mappé un codeActe de type retrait → status_label "... retrait")
      - OR un acte dans `raw.actes_timeline` a `libelle` contenant
        "retrait" (AN codeActe ANRETRAIT / similaire).
    """
    if (r.get("category") or "") != "dossiers_legislatifs":
        return
    title = (r.get("title") or "")
    if title:
        # Si la 1re lettre est déjà en majuscule ou non-alphabétique, on skip.
        first = title[0]
        if first.isalpha() and first.islower():
            r["title"] = (first.upper() + title[1:])[:220]

    # R13-L : détection d'un dossier retiré → réécrit status_label="Retiré".
    raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
    if not isinstance(raw, dict):
        return
    status_label = (raw.get("status_label") or "").strip()
    is_retire = False
    if raw.get("is_retire") is True:
        is_retire = True
    elif "retrait" in status_label.lower() or "retiré" in status_label.lower():
        is_retire = True
    else:
        timeline = raw.get("actes_timeline")
        if isinstance(timeline, list):
            for acte in timeline:
                if not isinstance(acte, dict):
                    continue
                lib = str(acte.get("libelle") or "").lower()
                code = str(acte.get("code") or "").lower()
                if "retrait" in lib or "retrait" in code:
                    is_retire = True
                    break
    if is_retire:
        raw["status_label"] = "Retiré"
        raw["is_retire"] = True
        r["raw"] = raw


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

    # R36-O (2026-04-24) : titre "Réunion — Assemblée nationale de la
    # 17ème législature" (observé en prod, cf. capture utilisateur).
    # Cas où l'organe_ref résolu pointe sur le PO souche de l'AN entière
    # (l'AN au complet, pas une commission spécifique). Ce libellé
    # d'organe n'apporte aucune info utile à la liste. On réécrit en
    # « Séance publique AN — <date> » pour rester lisible, ou simplement
    # « Séance publique AN » si la date est manquante. Idempotent : la
    # regex match spécifiquement le pattern souche, pas les libellés
    # commission qui commencent aussi par « Assemblée nationale ».
    if cat == "agenda":
        cur = r.get("title") or ""
        if re.match(
            r"^R[ée]union\s*[—\-–]\s*Assembl[ée]e\s+nationale\s+de\s+la\s+"
            r"\d+\s*[eèé]me\s+l[ée]gislature\b",
            cur,
        ):
            pa = r.get("published_at") or ""
            if isinstance(pa, str) and re.match(r"^\d{4}-\d{2}-\d{2}", pa):
                y, mo, dd = pa[:10].split("-")
                r["title"] = f"Séance publique AN — {dd}/{mo}/{y}"
            else:
                r["title"] = "Séance publique AN"
        # Symétrie Sénat : fallback générique si l'organe pointait sur le
        # PO souche du Sénat.
        elif re.match(
            r"^R[ée]union\s*[—\-–]\s*S[ée]nat\s*$", cur, re.IGNORECASE
        ):
            pa = r.get("published_at") or ""
            if isinstance(pa, str) and re.match(r"^\d{4}-\d{2}-\d{2}", pa):
                y, mo, dd = pa[:10].split("-")
                r["title"] = f"Séance publique Sénat — {dd}/{mo}/{y}"
            else:
                r["title"] = "Séance publique Sénat"

    # R23-G (2026-04-23) : nettoyage des titres AN agenda legacy qui
    # transportent un LIEU comme suffixe (« — Salle 6242 – Palais
    # Bourbon, 2ème sous-sol ») ou un préfixe "ordinal chambre (à
    # confirmer)". Avant R23-G, `_collect_agenda_titles` descendait
    # dans le sous-arbre `lieu.*` et remontait ces libellés de salle
    # comme candidats. Les items deja ingeres gardent le mauvais titre
    # tant qu'on ne les reset pas ; ce fixup reecrit le titre en place.
    #
    # Idempotent : la regex ne matche que si le suffixe " — <lieu>" est
    # encore present. Apres une 1re passe, le titre ne finit plus par
    # un lieu et le fixup est no-op.
    if cat == "agenda":
        cur = r.get("title") or ""
        # Suffixe lieu apres un em-dash ou tiret : on coupe AVANT le tiret.
        # Utilisation de `re.split` pour preserver l'entete (commission).
        _m = re.search(
            r"\s*[-–—]\s*(?:Salle\b|Visioconf[ée]rence\b|Palais\s+Bourbon\b"
            r"|H[ée]micycle\b|Petit\s+Luxembourg\b|Palais\s+du\s+Luxembourg\b"
            r"|\d+\s*(?:rue|avenue|boulevard)\b).*$",
            cur,
            flags=re.IGNORECASE,
        )
        if _m and _m.start() > 0:
            new_title = cur[:_m.start()].rstrip().rstrip("—–-").rstrip()
            if new_title:
                r["title"] = new_title[:220]
        # Titre qui EST un lieu pur (ex. "salle 4075 (9 rue de Bourgogne)") :
        # on remplace par organe_label (raw) si connu, sinon "Réunion
        # parlementaire" en dernier recours.
        cur2 = r.get("title") or ""
        if re.match(
            r"^\s*(Salle\b|Visioconf[ée]rence\b|Palais\s+Bourbon\b|"
            r"H[ée]micycle\b|\d+\s*(?:rue|avenue|boulevard)\b)",
            cur2,
            flags=re.IGNORECASE,
        ):
            raw = r.get("raw") or {}
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw or "{}")
                except Exception:
                    raw = {}
            organe_label = (raw.get("organe_label") or "").strip() \
                if isinstance(raw, dict) else ""
            r["title"] = (organe_label or "Réunion parlementaire")[:220]


def _window_for(category: str | None, source_id: str | None = None) -> int:
    """Fenêtre (jours) applicable à un (source_id, category) donné.

    Priorité : WINDOW_DAYS_BY_SOURCE_ID (R36-J, override fin) >
    WINDOW_DAYS_BY_CATEGORY (override catégorie) > WINDOW_DAYS (défaut).
    """
    if source_id and source_id in WINDOW_DAYS_BY_SOURCE_ID:
        return WINDOW_DAYS_BY_SOURCE_ID[source_id]
    if category and category in WINDOW_DAYS_BY_CATEGORY:
        return WINDOW_DAYS_BY_CATEGORY[category]
    return WINDOW_DAYS


def _format_window_human(days: int) -> str:
    """Formate une fenêtre (jours) en libellé humain court.

    R36-F (2026-04-24) — Cyril préfère « depuis 3 ans » plutôt que « depuis
    1095 jours » sur la page dossiers législatifs. Seuils retenus :
    - ≤ 90 j : "N jours"
    - 91-363 j : "N mois" (approximation 30 j/mois)
    - ≥ 365 j : "N ans" si multiple de ~365, sinon "N mois" jusqu'à 23 mois
    """
    if days <= 90:
        return f"{days} jours"
    if days < 365:
        mois = round(days / 30)
        return f"{mois} mois"
    ans = round(days / 365)
    if ans <= 1:
        return "1 an"
    return f"{ans} ans"


def _format_window_recent(days: int) -> str:
    """Comme `_format_window_human` mais retourne la locution au féminin ou
    masculin pluriel à placer APRÈS « sur les … » (R36-F bis, 2026-04-24).

    Usage : `f"sur les {_format_window_recent(days)}"` →
        - "sur les 30 derniers jours"
        - "sur les 3 derniers mois"
        - "sur les 3 dernières années"
        - "sur les 1 dernière année"

    Cyril (2026-04-24) : on ne dit pas « sur les 3 ans derniers » mais
    « sur les 3 dernières années ». Même logique pour les autres unités
    (derniers/dernières + unité au pluriel).
    """
    if days <= 90:
        return f"{days} derniers jours"
    if days < 365:
        mois = round(days / 30)
        return f"{mois} derniers mois"
    ans = round(days / 365)
    if ans <= 1:
        return "dernière année"
    return f"{ans} dernières années"

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
        # R14 : passe explicitement la longueur cible par catégorie à
        # `build_snippet` pour qu'il produise le bon gabarit dès la source
        # (avant, le défaut interne à 320 plafonnait tout et rendait les
        # valeurs > 320 de SNIPPET_LEN_BY_CATEGORY inopérantes — cf. R13-K
        # où le passage CR 250→500 n'avait aucun effet visible).
        if r.get("matched_keywords") and not r.get("snippet"):
            haystack = (r.get("summary") or r.get("title") or "").strip()
            # R23-D2 (2026-04-23) : pour les questions parlementaires, on
            # préfère le CORPS de la question (`raw.texte_question`) comme
            # haystack pour le snippet. Avant R23-D2, on utilisait `summary`
            # qui commençait par « Auteur (Groupe) — Destinataire : X —
            # Rubrique : sports — Analyse : Y — <texte> » : le matcher
            # tombait souvent sur l'occurrence « sports » du préfixe Rubrique
            # et rendait un extrait centré sur les métadonnées au lieu du
            # vrai texte de la question. Fallback propre sur summary si le
            # corps n'est pas disponible (items legacy, fiches mal formées).
            if r.get("category") == "questions":
                try:
                    _raw_q = json.loads(r.get("raw") or "{}")
                except Exception:
                    _raw_q = {}
                _texte_q = (_raw_q.get("texte_question") or "").strip() \
                    if isinstance(_raw_q, dict) else ""
                if _texte_q:
                    haystack = _texte_q
            # R23-F (2026-04-23) : pour les comptes rendus AN, retire le
            # préambule Syceron (cf. _strip_cr_an_preamble).
            if haystack and (r.get("category") == "comptes_rendus") \
                    and (r.get("chamber") == "AN"):
                haystack = _strip_cr_an_preamble(haystack)
            if haystack:
                _target = SNIPPET_LEN_BY_CATEGORY.get(r.get("category") or "", 800)
                r["snippet"] = _matcher.build_snippet(haystack, max_len=_target)
        # `raw` est stocké en TEXT JSON dans la DB — on le parse pour exposer
        # les champs enrichis (notamment status_label pour les dossiers
        # législatifs, cf. assemblee._normalize_dosleg).
        try:
            r["raw"] = json.loads(r.get("raw") or "{}")
        except Exception:
            r["raw"] = {}
        out.append(r)
    return out


def _load_disabled_source_ids(config_path: str = "config/sources.yml") -> set[str]:
    """R22b (2026-04-23) — retourne l'ensemble des `source_id` marqués
    `enabled: false` dans config/sources.yml.

    Motivation : quand Cyril désactive une source (ex. `alpes_2030_news`
    en R17, `senat_theme_sport_rss` en R19-B), le fetcher s'arrête mais
    les items déjà en DB continuent d'être ré-exportés vers le site jusqu'à
    expiration de la fenêtre (30j à 180j selon catégorie). Résultat : la
    source est « disabled » mais ses items restent affichés pendant des
    semaines.

    On filtre donc à l'export pour rendre la désactivation d'une source
    effective immédiatement sur le site, sans dépendre d'un reset DB
    manuel.
    """
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as fp:
            cfg = yaml.safe_load(fp) or {}
    except Exception:
        return set()
    disabled: set[str] = set()
    if not isinstance(cfg, dict):
        return disabled
    for group in cfg.values():
        if not isinstance(group, dict):
            continue
        for src in (group.get("sources") or []):
            if not isinstance(src, dict):
                continue
            if src.get("enabled") is False:
                sid = src.get("id")
                if isinstance(sid, str) and sid.strip():
                    disabled.add(sid.strip())
    return disabled


def _filter_disabled_sources(rows: list[dict]) -> list[dict]:
    """R22b — retire les rows dont le `source_id` est marqué disabled
    dans config/sources.yml. Idempotent, safe : si le fichier YAML est
    introuvable ou mal formé, on ne filtre rien (retourne rows tels quels).
    """
    disabled = _load_disabled_source_ids()
    if not disabled:
        return rows
    return [r for r in rows if (r.get("source_id") or "").strip() not in disabled]


# R28 (2026-04-23) — Filtre « publications parlementaires ».
# Sur la page /items/communiques/, le bucket family_source=parlement ne
# doit lister QUE les rapports (AN + Sénat), pas les actualités RSS
# Sénat (senat_rss, ~30 communiqués par mois) qui brouillent la lecture
# métier : Cyril distingue « rapport parlementaire » (document de
# contrôle avec n° + dossier) et « communiqué » (actu institutionnelle
# générique). Les items concernés :
#   - senat_rapports → GARDÉ (rapports Sénat, ~1500 entrées)
#   - an_rapports    → GARDÉ (rapports AN, R28)
#   - senat_rss      → RETIRÉ de la famille parlement (reste visible
#                      via la nav principale /items/communiques/ s'il
#                      était encore rangé ailleurs, mais ici on le
#                      masque pour le bucket parlement uniquement)
# Autres catégories (agenda, dossiers_legislatifs, questions, etc.) :
# pas touchées — la famille parlement continue à couvrir tous les flux
# AN/Sénat sur ces volets.
_PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES = {"senat_rapports", "an_rapports"}


def _filter_parlement_publications(rows: list[dict]) -> list[dict]:
    """R28 — pour category=communiques ET family_source=parlement, ne
    garde que les rapports (senat_rapports + an_rapports). Les autres
    items parlementaires en publications (ex. senat_rss) sont retirés.

    Idempotent. Aucun effet sur les autres catégories / familles.
    """
    kept: list[dict] = []
    dropped = 0
    for r in rows:
        cat = (r.get("category") or "").strip()
        if cat != "communiques":
            kept.append(r)
            continue
        family = _source_family(r.get("source_id"), r.get("chamber"))
        if family != "parlement":
            kept.append(r)
            continue
        sid = (r.get("source_id") or "").strip()
        if sid in _PARLEMENT_PUBLICATIONS_ALLOWED_SOURCES:
            kept.append(r)
        else:
            dropped += 1
    if dropped:
        import logging
        logging.getLogger(__name__).info(
            "R28 filtre publications parlement : %d item(s) non-rapport masqué(s)",
            dropped,
        )
    return kept


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
        sid = r.get("source_id") or ""
        # R36-J (2026-04-24) : override par source_id (rapports parlementaires
        # à 2 ans sans élargir tout le bucket communiques).
        window = _window_for(cat, source_id=sid)
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


_DOSLEG_STRIP_PREFIX_RE = re.compile(
    r"^(?:projet de loi(?:\s+(?:organique|constitutionnelle|de finances(?:\s+rectificative)?|de financement(?:\s+de\s+la\s+s[ée]curit[ée]\s+sociale)?))?|"
    r"proposition de loi(?:\s+organique|\s+constitutionnelle)?|"
    r"proposition de r[ée]solution(?:\s+europ[ée]enne)?|"
    r"pjl|ppl|pplo|ppr)\s+",
    re.IGNORECASE,
)
_DOSLEG_STRIP_CONNECTORS_RE = re.compile(
    r"^(?:relatif[e]?s?\s+[àa]|relative[s]?\s+[àa]|visant\s+[àa]|portant|tendant\s+[àa]|ayant\s+pour\s+objet|concernant|autorisant|approuvant|de\s+modernisation\s+de|pour\s+l['e]|pour\s+un)",
    re.IGNORECASE,
)
# R13-L (2026-04-21) : après stripage des préfixes type+connecteurs, on retire
# aussi tous les mots-outils courts en tête pour matcher des titres
# grammaticalement différents mais sémantiquement identiques
# (ex. "l'organisation des jeux Olympiques" vs "jeux Olympiques" → match).
_DOSLEG_STOPWORDS = {
    "l", "la", "le", "les", "de", "du", "des", "d", "a", "au", "aux",
    "et", "ou", "un", "une", "en", "dans", "sur", "par", "pour",
    "lorganisation", "organisation",
    "lheritage", "heritage",
    "lactivite", "activite",
    "ratification", "ratifier", "rectifier",
    "projet", "proposition", "loi", "pjl", "ppl", "relatif", "relative",
    "relatifs", "relatives", "visant", "portant", "tendant", "concernant",
    "autorisant", "approuvant",
}


def _dosleg_word_set(title: str) -> set[str]:
    """R18 (2026-04-22) — retourne l'ensemble des mots significatifs d'un
    titre de dossier législatif (≥3 chars, hors stopwords). Sert de base
    au dedup sémantique par intersection (plus robuste que l'égalité de
    bag-of-words trié qui fait manquer les titres avec un mot en plus/
    moins entre les versions AN et Sénat).
    """
    if not title:
        return set()
    try:
        from unidecode import unidecode as _uni
        s = _uni(title).lower()
    except Exception:
        s = title.lower()
    # Coupe au 1er caractère de ponctuation forte (suffixes "(PJL)" etc.)
    for sep in ("(", " - ", " — ", " – ", " : "):
        if sep in s:
            s = s.split(sep, 1)[0]
    # Retire préfixes type de texte + connecteurs grammaticaux
    s = _DOSLEG_STRIP_PREFIX_RE.sub("", s).strip()
    s = _DOSLEG_STRIP_CONNECTORS_RE.sub("", s).strip()
    # Ne garde que lettres + chiffres + espaces
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    words = s.split()
    return {w for w in words if len(w) >= 3 and w not in _DOSLEG_STOPWORDS}


def _dosleg_subject_key(title: str) -> str:
    """Clé normalisée pour détecter qu'un dossier AN et un dossier Sénat
    décrivent le même projet/proposition de loi.

    R13-L (2026-04-21) — Cyril voit par exemple :
      - "Projet de loi relatif à l'organisation des jeux Olympiques..."
      - "Jeux Olympiques et Paralympiques 2030 (PJL)"

    Implémentation : délègue à `_dosleg_word_set` + tri + join. Gardée
    pour compat logs/affichage — le dedup passe désormais par
    l'intersection des ensembles (R18).
    """
    return " ".join(sorted(_dosleg_word_set(title)))[:80]


def _is_dosleg_url(url: str) -> bool:
    """R18 (2026-04-22) — True si l'URL pointe vers une fiche-dossier
    législatif officielle (Sénat `/dossier-legislatif/`, AN `/dossiers/`).
    Utilisé comme tiebreak secondaire dans `_dedup` : à date et chambre
    égales, on garde l'entrée avec l'URL dosleg officielle plutôt qu'un
    amendement/compte rendu qui aurait atterri dans la catégorie.
    """
    if not url:
        return False
    u = url.lower()
    return "/dossier-legislatif/" in u or "/dossiers/" in u


# R18+ (2026-04-22) — extraction d'identifiants de procédure législative
# depuis une URL. Utilisé par `_dedup` passe 2c pour fusionner AN↔Sénat.
# AN : `/dyn/17/dossiers/DLR5L17N52100` → `dlr5l17n52100`
# Sénat : `/dossier-legislatif/pjl24-630.html` → `pjl24-630`
# Sénat (fallback) : `/leg/pjl24-630.html`, `/rap/…` sont hors scope ici.
_AN_DOSSIER_ID_RE = re.compile(r"/dossiers?/([A-Za-z0-9]{6,})", re.IGNORECASE)
_SENAT_DOSSIER_ID_RE = re.compile(
    r"/dossier-legislatif/([A-Za-z0-9_-]+?)(?:\.html|/|$|[?#])",
    re.IGNORECASE,
)


def _extract_dossier_ids_from_url(url: str) -> set[str]:
    """Extrait les identifiants AN/Sénat reconnaissables dans une URL.
    Retourne un set (vide si aucun match). Normalisé lowercase.
    """
    ids: set[str] = set()
    if not url:
        return ids
    m = _AN_DOSSIER_ID_RE.search(url)
    if m:
        ids.add(m.group(1).lower())
    m = _SENAT_DOSSIER_ID_RE.search(url)
    if m:
        ids.add(m.group(1).lower())
    return ids


def _item_dossier_ids(row: dict) -> set[str]:
    """R18+ (2026-04-22) — rassemble tous les identifiants de procédure
    législative qu'un item contribue au graphe de dedup.

    Sources :
      - `raw["dossier_id"]` (AN uid ou Sénat signet/numéro, selon parser)
      - `raw["signet"]` (Sénat AKN uniquement)
      - ID AN/Sénat extrait de `url`
      - ID AN extrait de `raw["url_an"]` (croisement Sénat→AN : un dossier
        Sénat expose souvent une URL vers sa contrepartie AN dans le FRBR)
    """
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    ids: set[str] = set()
    for k in ("dossier_id", "signet"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            ids.add(v.strip().lower())
    ids |= _extract_dossier_ids_from_url(row.get("url") or "")
    url_an = raw.get("url_an")
    if isinstance(url_an, str) and url_an:
        ids |= _extract_dossier_ids_from_url(url_an)
    # R22a (2026-04-23) — IDs cumulés lors des fusions précédentes (2a/2b).
    # Cf. `_merge_ids_into_winner` : on préserve l'info de bridge AN↔Sénat
    # quand un senat_akn_* (qui portait url_an) est écarté en 2a au profit
    # d'un senat_promulguees (sans url_an). Sans ce cumul, la passe 2c ne
    # peut plus relier l'AN qui reste.
    merged = raw.get("_merged_dossier_ids")
    if isinstance(merged, list):
        for m in merged:
            if isinstance(m, str) and m.strip():
                ids.add(m.strip().lower())
    ids.discard("")
    return ids


def _merge_ids_into_winner(winner: dict, loser: dict) -> None:
    """R22a (2026-04-23) — injecte les IDs du loser dans winner.raw pour que
    les passes de dédup ultérieures (`_item_dossier_ids`) voient encore le
    bridge AN↔Sénat des items écartés.

    Sans ça, le scénario JOP Alpes 2030 cassait : passe 2a fusionne les 3
    Sénat sur `pjl24-630.html` en gardant senat_promulguees (date desc),
    mais senat_promulguees n'a pas `url_an` — donc passe 2c perd le lien
    vers DLR5L17N52100 côté AN et les 2 items (AN + Sénat) restent.
    """
    raw_w = winner.get("raw")
    if not isinstance(raw_w, dict):
        return
    loser_ids = _item_dossier_ids(loser)
    if not loser_ids:
        return
    existing = raw_w.get("_merged_dossier_ids")
    if not isinstance(existing, list):
        existing = []
    cumul = set(existing) | loser_ids
    raw_w["_merged_dossier_ids"] = sorted(cumul)


def _dedup(rows: list[dict]) -> list[dict]:
    """Déduplication multi-passes.

    1. (title, url) : filet de sécurité au-delà du hash_key — couvre les
       items référencés sous plusieurs UIDs dans un même dump.

    2. R13-L (2026-04-21) — dossiers législatifs :
       a. Dédup par URL exacte : garde la version la plus récente.
       b. Dédup par clé sémantique (`_dosleg_subject_key`) : garde la plus
          récente, avec priorité Sénat en cas d'égalité de date.

    `rows` est déjà trié par date desc à ce stade.
    """
    # Passe 1 — (title, url)
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

    # Passe 2 — dédup dossiers législatifs.
    # R17 (2026-04-22) — refonte : Cyril observait 4 occurrences d'un même
    # dossier (JO 2030) dans le top : 1 AN, 2 variantes Sénat, 1 loi
    # promulguée. Diagnostic :
    #   - les 2 variantes Sénat partagent la MÊME URL `pjl24-630.html` mais
    #     avec scheme différent (http:// vs https://) → dédup URL lowercase
    #     les laissait passer.
    #   - AN et Sénat du même projet ont 2 URLs distinctes (chambres
    #     différentes) → dédup URL ne peut pas les fusionner.
    #
    # Stratégie :
    #   a. Dédup URL normalisée : strip scheme + host + trailing slash →
    #      fusionne les 2 Sénat (pjl24-630.html).
    #   b. Dédup sémantique (clé bag-of-words sur titre) AVEC SEUIL STRICT :
    #      ≥ 4 mots significatifs ET clé ≥ 25 chars, pour éviter la casse
    #      R13-L où des dossiers courts collapsaient (« esport responsable »,
    #      « sport santé » avaient trop peu de mots signifiants → mêmes clés
    #      presque vides). En dessous du seuil, on NE dédupe pas.
    #   Tiebreak : date publication desc ; à égalité, chambre Sénat
    #   prioritaire (chaînage navette plus visible côté Sénat).
    import re as _re
    def _date_of(r: dict) -> str:
        return (r.get("published_at") or "")[:10]

    def _is_senat(r: dict) -> bool:
        return (r.get("chamber") or "").lower() in ("senat", "sénat")

    def _url_canon(u: str) -> str:
        """Canonicalise une URL pour dédup : strip scheme, lowercase host,
        enlève trailing slash et fragments. Rien de plus (pas de normalisation
        de query — qui distingue parfois des dossiers distincts)."""
        if not u:
            return ""
        s = u.strip().lower()
        s = _re.sub(r"^https?://", "", s)
        s = s.split("#", 1)[0]
        if s.endswith("/"):
            s = s[:-1]
        return s

    def _prefer(a: dict, b: dict) -> dict:
        """R18 (2026-04-22) — tiebreak uniformisé pour les dédups dosleg.
        Ordre de priorité :
          1. date de publication desc
          2. chambre Sénat (visibilité navette)
          3. URL dossier-législatif officielle (/dossier-legislatif/ ou /dossiers/)
          4. a (item rencontré le premier).
        """
        da, db = _date_of(a), _date_of(b)
        if da > db:
            return a
        if db > da:
            return b
        sa, sb = _is_senat(a), _is_senat(b)
        if sa and not sb:
            return a
        if sb and not sa:
            return b
        ua = _is_dosleg_url(a.get("url") or "")
        ub = _is_dosleg_url(b.get("url") or "")
        if ua and not ub:
            return a
        if ub and not ua:
            return b
        return a

    dosleg = [r for r in out if (r.get("category") or "") == "dossiers_legislatifs"]
    other = [r for r in out if (r.get("category") or "") != "dossiers_legislatifs"]

    # 2a) dédup URL canonicalisée
    by_url: dict[str, dict] = {}
    for r in dosleg:
        u = _url_canon(r.get("url") or "")
        if not u:
            u = f"__nourl__{r.get('uid','')}"
        prev = by_url.get(u)
        if prev is None:
            by_url[u] = r
            continue
        w = _prefer(prev, r)
        loser = r if w is prev else prev
        _merge_ids_into_winner(w, loser)  # R22a — préserver bridge AN↔Sénat
        by_url[u] = w
    step1 = list(by_url.values())
    dropped_url = len(dosleg) - len(step1)

    # 2b) R18 (2026-04-22) — dédup sémantique par INTERSECTION de mots.
    # Auparavant (R17) : clé bag-of-words triée, comparaison par égalité
    # stricte → manquait le cas où AN et Sénat ont quasi-les-mêmes mots
    # mais avec un substantif supplémentaire côté AN (« loi pour l'héritage
    # des jeux olympiques... » vs « jeux olympiques paralympiques 2030 »).
    # Nouveau : deux items sont considérés comme le même dossier si leurs
    # ensembles de mots significatifs partagent ≥ 3 mots ET qu'au moins
    # l'un des deux a ≥ 4 mots ET que la plus longue des deux clés fait
    # ≥ 25 chars (protège les dossiers courts contre les faux positifs).
    # Complexité O(n²) sur les dosleg — acceptable (quelques dizaines).
    INTERSECTION_MIN = 5
    WORDS_MIN = 4
    KEY_LEN_MIN = 25

    groups: list[list[dict]] = []
    word_sets: list[set[str]] = []
    keys: list[str] = []
    for r in step1:
        ws = _dosleg_word_set(r.get("title") or "")
        key = " ".join(sorted(ws))[:80]
        matched_idx = -1
        if len(ws) >= WORDS_MIN and len(key) >= KEY_LEN_MIN:
            # Cherche un groupe existant qui matche par intersection
            for i, gws in enumerate(word_sets):
                if len(gws) < WORDS_MIN:
                    continue
                if max(len(keys[i]), len(key)) < KEY_LEN_MIN:
                    continue
                inter = ws & gws
                if len(inter) >= INTERSECTION_MIN:
                    matched_idx = i
                    break
        if matched_idx == -1:
            groups.append([r])
            word_sets.append(ws)
            keys.append(key)
        else:
            groups[matched_idx].append(r)
            # Agrège le word_set du groupe (pour matcher les items suivants
            # même si un synonyme est introduit au fil des occurrences).
            word_sets[matched_idx] = word_sets[matched_idx] | ws

    dedup_dosleg: list[dict] = []
    for grp in groups:
        if len(grp) == 1:
            dedup_dosleg.append(grp[0])
            continue
        winner = grp[0]
        losers = []
        for cand in grp[1:]:
            new_w = _prefer(winner, cand)
            losers.append(cand if new_w is winner else winner)
            winner = new_w
        # R22a (2026-04-23) — cumul des IDs pour que la passe 2c les voit.
        for loser in losers:
            _merge_ids_into_winner(winner, loser)
        dedup_dosleg.append(winner)
    dropped_sem = len(step1) - len(dedup_dosleg)

    # 2c) R18+ (2026-04-22) — dédup par identifiant de procédure législative.
    # Plus robuste que la sémantique : si un item AN et un item Sénat
    # partagent un identifiant canonique (AN DLR5L17N52100, Sénat pjl24-630),
    # on les fusionne même si leurs titres diffèrent beaucoup. La
    # correspondance AN↔Sénat passe par `raw["url_an"]` (exposé par le
    # parser senat_akn via l'alias FRBR "url-AN" du .akn.xml). Algorithme :
    #   - Chaque item contribue un set d'IDs (raw.dossier_id, raw.signet,
    #     IDs extraits des URLs connues).
    #   - On regroupe par intersection non-vide, en parcours séquentiel.
    #   - Tiebreak : `_prefer()` (date desc → Sénat → URL dosleg).
    groups_id: list[list[dict]] = []
    group_ids: list[set[str]] = []
    for r in dedup_dosleg:
        ids = _item_dossier_ids(r)
        if not ids:
            # Pas d'ID exploitable — item garde sa place, pas de fusion ici.
            groups_id.append([r])
            group_ids.append(set())
            continue
        matched = -1
        for i, gids in enumerate(group_ids):
            if gids and (ids & gids):
                matched = i
                break
        if matched == -1:
            groups_id.append([r])
            group_ids.append(set(ids))
        else:
            groups_id[matched].append(r)
            group_ids[matched] |= ids

    final_dosleg: list[dict] = []
    for grp in groups_id:
        if len(grp) == 1:
            final_dosleg.append(grp[0])
            continue
        winner = grp[0]
        for cand in grp[1:]:
            winner = _prefer(winner, cand)
        final_dosleg.append(winner)
    dropped_id = len(dedup_dosleg) - len(final_dosleg)

    if dropped_url or dropped_sem or dropped_id:
        import logging
        logging.getLogger(__name__).info(
            "site_export : %d dosleg dédupés (URL canon) + %d (sémantique) + %d (dossier_id)",
            dropped_url, dropped_sem, dropped_id,
        )
    return _sort_by_date_desc(final_dosleg + other)


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
    # R22b (2026-04-23) : filtre les rows dont la source est marquée
    # disabled dans config/sources.yml. Sans ce filtre, les items ingérés
    # avant la désactivation d'une source (ex. alpes_2030_news depuis R17,
    # senat_theme_sport_rss depuis R19-B) continuent d'apparaître sur le
    # site pendant des semaines — jusqu'à expiration de la fenêtre de
    # publication visible. On le fait AVANT _fix_* (évite du travail inutile
    # sur des items qu'on va jeter de toute façon).
    rows = _filter_disabled_sources(rows)
    # R28 (2026-04-23) : dans la famille parlement x publications, on ne
    # garde que les rapports officiels (AN + Sénat). Les actualités RSS
    # Sénat (senat_rss) sont exclues de ce bucket (cf. docstring).
    rows = _filter_parlement_publications(rows)
    # R23-N (2026-04-23) : cache nom_auteur_normalisé → (photo, fiche) bâti
    # depuis les amendements Sénat. Utilisé pour enrichir les questions Sénat
    # qui, à l'ingestion, n'ont pas de colonne « Fiche Sénateur » exploitable.
    # Bâti une seule fois avant la boucle, coût mémoire négligeable (~quelques
    # centaines de clés).
    senat_photo_cache = _build_senat_photo_cache(rows)
    for r in rows:
        _fix_cr_row(r)
        _fix_question_row(r)
        _fix_agenda_row(r)
        _fix_dossier_row(r)
        _fix_amendement_row(r)
        _fix_chamber_row(r)
        _enrich_senat_question_photo(r, senat_photo_cache)
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
    # R17 (2026-04-22) : le cartouche chamber (badge "MINSPORTS") est affiché
    # à côté du titre dans la sidebar — pas besoin de répéter « MinSports — »
    # en tête du titre. Idem pour toute autre chambre qui se retrouverait
    # préfixée dans le title (Elysee, Senat, AN, etc.). On strippe un
    # préfixe exact « <Chamber> — » / « <Chamber> - » au début du titre.
    def _strip_chamber_prefix_tit(r: dict) -> dict:
        title = (r.get("title") or "").strip()
        chamber = (r.get("chamber") or "").strip()
        if not title or not chamber:
            return r
        for sep in (" — ", " – ", " - "):
            pref = f"{chamber}{sep}"
            if title.lower().startswith(pref.lower()):
                r = dict(r)
                r["title"] = title[len(pref):].lstrip()
                break
        return r
    upcoming = [_strip_chamber_prefix_tit(r) for r in upcoming]
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
    """Items publiés aujourd'hui ou hier (calendrier Paris), indépendamment
    de l'heure exacte. On couvre ainsi les publications matinales de la
    veille qui tombaient hors fenêtre stricte 24h selon l'heure du build.

    R24 (2026-04-23) — passage de fenêtre horaire stricte (24h glissantes)
    à fenêtre calendaire (jour J + jour J-1 en heure Paris). Titre
    « Dernières 24 h » conservé tel quel (demande Cyril).

    Catégories exclues (inchangé depuis R17) :
      - `agenda`      : déjà dans la sidebar.
      - `communiques` : volume élevé, bruite le haut de page.
    """
    import zoneinfo
    tz_paris = zoneinfo.ZoneInfo("Europe/Paris")
    today_paris = datetime.now(tz_paris).date()
    yesterday_paris = today_paris - timedelta(days=1)
    excluded_cats = {"agenda", "communiques"}
    out = []
    for r in rows:
        if (r.get("category") or "") in excluded_cats:
            continue
        dt = _parse_dt(r.get("published_at"))
        if dt:
            # Convertit en date Paris pour comparaison calendaire
            try:
                dt_paris = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(tz_paris).date()
            except Exception:
                dt_paris = dt.date()
            if dt_paris >= yesterday_paris:
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

    # Chambre : badge HTML avec data-chamber pour coloration AN/Senat distincte.
    # R36-M (2026-04-24) : rollback de R36-B — retour au cartouche texte
    # pour TOUTES les chambres. Seules les pages dédiées dosleg (cards 56x56)
    # et CR (22x22 inline dans comptes_rendus/list.html) rendent un logo
    # SVG, via leurs layouts spécifiques. Cyril a jugé le logo 22x22 trop
    # petit quand il était poussé en partial partout (capture 2026-04-24).
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
        f'title: "Veille Institutionnelle Sport — {now:%Y-%m-%d}"',
        f'date: {now:%Y-%m-%d}',
        'description: "Veille institutionnelle du sport — actualisée quotidiennement par Sideline Conseil."',
        "---",
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
        lines.append("_Aucune nouveauté depuis 24h._")
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
            # R36-F (2026-04-24) : affichage humain (ex. "Depuis 3 ans" plutôt
            # que "Depuis 1095 jours") aligné sur la page dédiée.
            window_label = f"Depuis {_format_window_human(display_window)}"
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
        # R13-M (2026-04-21) : réactive `type: dossiers_legislatifs` pour
        # bénéficier du layout cards dédié (logos AN/Sénat 56x56 ajoutés).
        # Si Hugo filtre à nouveau les items sans `date:` valide, on
        # retombera sur _default/list.html via SPECIFIC_LAYOUT_CATS=agenda
        # seul. À surveiller après le run R13-M.
        # R23-O (2026-04-23) : ajout `communiques`. Sans `type:
        # "communiques"` dans le frontmatter, Hugo dérivait .Type depuis
        # le premier segment sous content/ (`items`), et la condition
        # `{{ if eq .Type "communiques" }}` dans _default/list.html
        # tombait à faux -> filtre par famille de source invisible sur
        # /items/communiques/ (R23-H). Communiques n'a PAS de layout
        # dédié (layouts/communiques/list.html inexistant) : Hugo
        # retombera sur _default/list.html qui rend correctement la
        # page + le filtre une fois .Type résolu.
        SPECIFIC_LAYOUT_CATS = {"agenda", "dossiers_legislatifs", "communiques"}
        lines = [
            "---",
            f'title: "{label}"',
        ]
        if cat in SPECIFIC_LAYOUT_CATS:
            lines.append(f'type: "{cat}"')

        # R36-F / R36-J / R36-K (2026-04-24) — libellés de fenêtre spécifiques
        # par catégorie, avec durée exprimée en années quand > 1 an.
        # R36-F bis (2026-04-24) — « sur les 3 dernières années » (pas
        # « sur les 3 ans derniers »). `_format_window_recent` retourne
        # la locution entière avec adjectif accordé AVANT le nom.
        window_recent = _format_window_recent(window)
        if cat == "dossiers_legislatifs":
            description = (
                f"Veille {label.lower()} — {count} dossiers sur les "
                f"{window_recent}."
            )
            body_line = (
                f"{count} dossier{'s' if count > 1 else ''} législatif"
                f"{'s' if count > 1 else ''} dans la veille sur les "
                f"{window_recent}."
            )
        elif cat == "communiques":
            # R36-J : libellé générique qui couvre à la fois les communiqués
            # (90j) et les rapports parlementaires (2 ans via override
            # WINDOW_DAYS_BY_SOURCE_ID), sans laisser entendre que tous les
            # items respectent la même fenêtre.
            description = (
                f"Veille {label.lower()} — {count} publications, rapports "
                f"et communiqués récents."
            )
            body_line = (
                f"{count} publication{'s' if count > 1 else ''} — "
                "publications, rapports et communiqués récents."
            )
        elif cat == "jorf":
            # R36-K : fenêtre 90j + mention explicite "hors nominations"
            # avec un lien markdown vers la catégorie dédiée.
            description = (
                f"Veille {label.lower()} — {count} textes sur les "
                f"{window_recent}, hors nominations."
            )
            body_line = (
                f"{count} texte{'s' if count > 1 else ''} au JO sur les "
                f"{window_recent}, **hors nominations** "
                "([voir la page Nominations](/items/nominations/))."
            )
        else:
            description = (
                f"Veille {label.lower()} — {count} items sur les "
                f"{window_recent}."
            )
            body_line = (
                f"{count} publication{'s' if count > 1 else ''} dans cette "
                f"catégorie sur les {window_recent}."
            )

        lines += [
            f'description: "{description}"',
            "---",
            "",
            body_line,
            "",
        ]
        (d / "_index.md").write_text("\n".join(lines), encoding="utf-8")


def _amendement_chip(raw: dict) -> tuple[str, str]:
    """Calcule le libellé + slug du chip coloré pour un amendement AN.

    R23-A (2026-04-23) — extrait de `_write_item_pages` pour être testable
    en isolation. Priorité, du plus précis au plus transitoire :

        1. `raw.sort`        — libellé final en séance ("Tombé", "Adopté"…)
        2. `raw.sous_etat`   — sousEtat de etatDesTraitements (proxy fiable
                                quand `sort` est vide mais la décision est
                                prise ; ex : "Tombé", "Adopté sans modif")
        3. `raw.etat`        — état transitoire ("Discuté", "En traitement")
        4. `raw.statut`      — fallback historique pre-R13-J

    Retourne (label, slug). Les deux sont vides si aucun champ n'est
    renseigné. Le slug est normalisé (accents retirés, lowercase,
    kebab-case) pour ciblage CSS.
    """
    if not isinstance(raw, dict):
        return "", ""
    sort_label = (raw.get("sort") or "").strip()
    sous_etat_label = (raw.get("sous_etat") or "").strip()
    etat_label = (raw.get("etat") or "").strip()
    statut_legacy = (raw.get("statut") or "").strip()
    chip_label = (sort_label or sous_etat_label
                  or etat_label or statut_legacy)
    if not chip_label:
        return "", ""
    try:
        from unidecode import unidecode as _uni
        slug = _uni(chip_label).lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    except Exception:
        slug = re.sub(r"[^a-z0-9]+", "-", chip_label.lower()).strip("-")
    return chip_label, slug


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
        # R15 (2026-04-22) : on distingue désormais deux dates.
        #   - `published_at_real` : date officielle de la publication (si
        #     disponible). Utilisée pour les affichages templates via le
        #     champ `published_at_real` du frontmatter.
        #   - `frontmatter_date` : toujours renseigné — fallback sur
        #     `inserted_at` pour éviter le filtre silencieux Hugo qui
        #     masque les items `type: <cat>` sans `date:` dans le
        #     frontmatter (cf. audit agenda R15 : 6411/6412 items AN
        #     agenda étaient perdus à cause de ce filtre).
        # Le tri côté site_export (_sort_by_date_desc) continue à se
        # baser sur `published_at` brut de la DB — l'ordre d'affichage
        # reste donc inchangé pour les items qui en ont une vraie.
        published_at_real = r.get("published_at") or ""
        _inserted_at = r.get("inserted_at") or ""
        frontmatter_date = published_at_real or _inserted_at
        has_real_date = bool(published_at_real)
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
        # R13-D / R14 : la longueur du snippet est désormais imposée en
        # amont dans `_load` via `build_snippet(..., max_len=target)`.
        # Ici on ne fait plus que :
        #   1. Sanitizer pour le frontmatter YAML (guillemets + newlines).
        #   2. Gérer l'absence de clé dans SNIPPET_LEN_BY_CATEGORY →
        #      snippet vide (ex. dossiers_legislatifs : pas d'extrait sur la
        #      page dédiée, les cartes portent déjà titre/type/date/statut).
        # Le troncage défensif au dernier espace a été retiré : redondant
        # depuis que build_snippet respecte max_len par catégorie.
        _snip_raw = (r.get("snippet") or "").replace('"', "'").replace("\n", " ")
        snippet = _snip_raw if cat in SNIPPET_LEN_BY_CATEGORY else ""
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
        auteur_groupe_long = ""
        auteur_photo_url = ""
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
            # R23-B (2026-04-23) : libellé long pour tooltip hover.
            # Peuplé par parser AN (amendements + questions) ; vide sur
            # les items Sénat ou items AN ingérés avant R23-B (fallback
            # via amo_loader.resolve_groupe_long plus bas).
            auteur_groupe_long = (raw.get("groupe_long") or "").strip()
            # R23-C (2026-04-23) : URL portrait du député/sénateur.
            auteur_photo_url = (raw.get("auteur_photo_url") or "").strip()
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
            # R23-B (2026-04-23) : backfill du libellé long pour les items
            # AN ingérés avant le patch parser (raw.groupe_long inexistant).
            # On passe par amo_loader.resolve_groupe_long(PA) qui utilise
            # groupe_ref du cache AMO. Pas de réseau, pas de latence.
            if not auteur_groupe_long and auteur_ref.startswith("PA"):
                auteur_groupe_long = (
                    amo_loader.resolve_groupe_long(auteur_ref) or ""
                )
            # R23-C (2026-04-23) : backfill de l'URL photo pour les items
            # AN ingérés avant le patch parser. Pattern déterministe depuis
            # PAxxx → /dyn/static/tribun/17/photos/carre/<digits>.jpg
            # (corrigé en R23-C2 ; ancien pattern /tribun/LEG/photos/N.jpg
            # renvoyait 404). Pas de réseau.
            if not auteur_photo_url and auteur_ref.startswith("PA"):
                auteur_photo_url = (
                    amo_loader.build_photo_url_an(auteur_ref) or ""
                )
            # URL fiche député : reconstruit si manquant mais acteurRef connu.
            if not auteur_url and auteur_ref.startswith("PA") and auteur_ref[2:].isdigit():
                auteur_url = f"https://www.assemblee-nationale.fr/dyn/deputes/{auteur_ref}"
            # Titre des questions : si le titre embarqué contient encore le
            # code "Député PAxxxx" (item pre-patch), on le réécrit avec le
            # nom résolu. Évite un reset DB complet.
            if auteur_label and title:
                title = re.sub(r"Député PA\d+", auteur_label, title)
            # R22g (2026-04-23) — legacy format pré-R13-L :
            #   "M. Jean-François Coulomme | Question orale n°83 — PA795136 (LFI-NFP) : M."
            # Le patch R13-L avait simplifié le titre en "{qtype} : {sujet}"
            # mais les items déjà en base (cache GHA SQLite) gardent l'ancien
            # format et on les voit apparaître au-delà de la 5e question sur
            # la page /items/questions/. On réécrit à l'export en
            # reconstruisant depuis `raw.analyse` / `tete_analyse` / `rubrique`.
            # Détection large : tout titre de question contenant "PA\d+ (...)".
            if cat == "questions" and title and re.search(r"PA\d+\s*\([^)]+\)", title):
                # Extraction du qtype_label : première occurrence de
                # "Question <mot(s)>" avant "n°".
                qtype_m = re.search(
                    r"\b(Question[^|]*?)\s*n°\s*\d+",
                    title,
                    re.IGNORECASE,
                )
                qtype_label = (qtype_m.group(1).strip() if qtype_m else "Question")
                sujet_court = ""
                if isinstance(raw, dict):
                    sujet_court = (
                        (raw.get("analyse") or "").strip()
                        or (raw.get("tete_analyse") or "").strip()
                        or (raw.get("rubrique") or "").strip()
                    )
                if not sujet_court:
                    sujet_court = "Question"
                title = f"{qtype_label} : {sujet_court}"[:220]
        status_label = status_label.replace('"', "'")

        fm = [
            "---",
            f'title: "{title}"',
        ]
        # R15 : `date:` toujours émise (fallback `inserted_at`) pour
        # éviter le filtre silencieux Hugo sur les items sans date
        # officielle. Templates doivent lire `published_at_real` /
        # `has_real_date` pour savoir si la date affichée est fiable.
        if frontmatter_date:
            fm.append(f"date: {frontmatter_date}")
        if published_at_real:
            fm.append(f"published_at_real: {published_at_real}")
        fm.append(f"has_real_date: {str(has_real_date).lower()}")
        # R23-H (2026-04-23) : famille de source pour le filtre UI exposé
        # sur /items/agenda/ et /items/communiques/. 5 buckets stables :
        # parlement | gouvernement | autorites | operateurs | jorf
        # (fallback "autres"). cf. _source_family().
        family_source = _source_family(r.get("source_id"), r.get("chamber"))
        fm += [
            f"category: {cat}",
            f'chamber: "{r.get("chamber") or ""}"',
            f'source: "{r.get("source_id") or ""}"',
            f'family_source: "{family_source}"',
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
        # R23-B (2026-04-23) : exposé pour tooltip hover dans le template.
        # On ne l'émet que s'il diffère du sigle, pour éviter du bruit HTML
        # (ex : un item Sénat où groupe == groupe_long).
        if auteur_groupe_long and auteur_groupe_long != auteur_groupe:
            fm.append(
                f'auteur_groupe_long: "{auteur_groupe_long.replace(chr(34), chr(39))}"'
            )
        # R23-C (2026-04-23) : exposé pour rendu portrait miniature dans
        # le template list.html / single.html.
        if auteur_photo_url:
            fm.append(f'auteur_photo_url: "{auteur_photo_url}"')
        if auteur_url:
            fm.append(f'auteur_url: "{auteur_url}"')
        # R13-J (2026-04-21) — patch 16 : chip sort/état pour les amendements.
        # Priorité `sort` (libellé final en séance / commission), fallback
        # `etat` (transitoire). Slug normalisé pour ciblage CSS (accents
        # retirés, lowercase, kebab-case).
        # R13-L : fallback aussi sur `statut` pour les items legacy ingérés
        # avant la séparation sort/etat (Cyril ne voyait pas le badge vert
        # "Adopté" sur les anciens amendements).
        # R23-A (2026-04-23) : logique extraite dans `_amendement_chip()`
        # (testable en isolation). `sous_etat` inséré entre `sort` et
        # `etat` — beaucoup d'amendements ont `sort=""` mais
        # `etatDesTraitements.sousEtat.libelle="Tombé"` / "Adopté sans
        # modif" — on privilégie cette info à `etat` (transitoire, souvent
        # "Discuté") quand `sort` est vide.
        if cat == "amendements":
            chip_label, chip_slug = _amendement_chip(raw or {})
            if chip_label:
                fm.append(
                    f'sort_label: "{chip_label.replace(chr(34), chr(39))}"')
                fm.append(f'sort_slug: "{chip_slug}"')
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
