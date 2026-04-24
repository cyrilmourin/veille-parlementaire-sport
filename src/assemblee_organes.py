"""Whitelist d'organes AN pertinents pour la veille sport / JOP.

R27 (2026-04-23) — Certaines réunions de commissions, groupes d'études,
missions d'information ou commissions d'enquête sont pertinentes pour la
veille même quand leur titre d'agenda ne contient aucun mot-clé sport.
Exemple concret : la Commission des affaires culturelles et de l'éducation
(PO419604) traite régulièrement de sport sans que le libellé "sport"
apparaisse dans le nom de la commission ni dans l'ordre du jour affiché.

Mécanique : `src/main._apply_organe_bypass` injecte un pseudo-keyword
`(organe sport/JOP)` sur les items `an_agenda` (et potentiellement
`an_syceron` si on enrichit le parser plus tard) dont `raw.organe`
appartient à ce set. Cela suffit à passer le filtre
`matched_keywords != '[]'` dans `store.fetch_matched_since()` et à faire
remonter l'item au digest + site.

Codes identifiés via audit `data/amo_resolved.json` (2026-04-23) sur
législature 17 + archives récentes :

Commissions permanentes (toujours actives) :
- PO419604 : Commission des affaires culturelles et de l'éducation (AN)
             — traite sport/EPS, COJO, conventions internationales sportives

R35-D (2026-04-24) : Commissions "Affaires sociales" retirées du bypass.
Cyril : « dans l'agenda j'ai des occurrences de commission qui ne semblent
pas connectées à mes sujets (la commission des affaires sociales…) ».
Ces commissions traitent majoritairement retraites, santé générale,
assurance maladie, droit du travail et politiques sociales — le volume
de réunions est très supérieur aux rares sujets sport (dopage, droit du
travail sportif, santé sportive). Le bypass générait donc >90% de bruit
off-topic. Les réunions genuinement sport continuent à remonter via le
matching keyword standard quand « sport », « dopage », « ANS », « JO »,
etc. apparaissent dans le titre ou l'ordre du jour. Codes retirés :
- PO420120 : Commission des affaires sociales (AN)
- PO211493 : Commission des affaires sociales (Sénat)

Commissions permanentes Sénat (incluses pour symétrie, même si l'agenda
AN n'utilise PAS ces codes — utile si on étend au dump Sénat plus tard) :
- PO211490 : Commission culture/éducation/communication/sport (Sénat)

Groupes d'études (non actifs législature 17 pour l'instant, mais codes
historiquement actifs sous 15/16 — laissés dans le set pour robustesse
sur les archives et en prévision d'une réactivation) :
- PO285103 : GE Sport et éducation sportive
- PO746821 : GE Économie du sport
- PO402925 : GE Éthique et dopage dans le sport

Missions d'information et commission d'enquête :
- PO804929 : MI Retombées JOP 2024 sur le tissu économique/associatif
- PO825884 : MI Femmes et sport
- PO806169 : MI Géopolitique du sport
- PO695919 : MI Soutien au sport professionnel / amateur
- PO825320 : Commission d'enquête Fédérations françaises de sport

Note de maintenance : si une nouvelle mission/CE sport émerge, ajouter
son code PO ici (récupérable via `data/amo_resolved.json["organes"]`
en grepant "sport|olymp|jop|dopage").
"""
from __future__ import annotations

# Codes PO d'organes dont toute réunion / activité agenda doit remonter
# même sans match keyword dans le titre. Conservé en set pour lookup O(1).
SPORT_RELEVANT_ORGANES: set[str] = {
    # Commissions permanentes AN
    "PO419604",  # Affaires culturelles et éducation AN
    # R35-D : PO420120 (Affaires sociales AN) retiré — trop de bruit,
    # les réunions sport remontent désormais via matching keyword.
    # Commissions permanentes Sénat (symétrie / extension future)
    "PO211490",  # Culture/éducation/communication/sport Sénat
    # R35-D : PO211493 (Affaires sociales Sénat) retiré — idem AN.
    # Groupes d'études (souvent en sommeil entre législatures)
    "PO285103",  # Sport et éducation sportive
    "PO746821",  # Économie du sport
    "PO402925",  # Éthique et dopage dans le sport
    # Missions d'information / commission d'enquête sport
    "PO804929",  # MI retombées JOP 2024
    "PO825884",  # MI femmes et sport
    "PO806169",  # MI géopolitique du sport
    "PO695919",  # MI soutien au sport professionnel/amateur
    "PO825320",  # CE fédérations françaises de sport
}

# Libellé injecté comme pseudo-keyword pour les items passant par le
# bypass organe. Visible côté site comme un kw-tag — volontairement
# distinct de `(flux complet)` (R25-H) pour qu'on puisse distinguer :
#   - (flux complet)     → source 100% sport (ANS, CNOSF, MinSports…)
#   - (organe sport/JOP) → réunion/agenda d'un organe pertinent
BYPASS_ORGANE_LABEL = "(organe sport/JOP)"


def is_sport_relevant_organe(organe_ref: str | None) -> bool:
    """True si le code organe est dans la whitelist sport/JOP.

    Tolère `None`, chaîne vide, espaces. Case-sensitive (les codes PO
    sont canoniquement en majuscules dans AMO).
    """
    if not organe_ref:
        return False
    return organe_ref.strip() in SPORT_RELEVANT_ORGANES
