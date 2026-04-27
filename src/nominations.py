"""R41-D (2026-04-27) — Extraction structurée des nominations + dédup.

Demande Cyril : sur les sources presse spécialisée sport business
(Olbia, Café du Sport Business, Sport Stratégies, etc.), homogénéiser
les nominations sous un format canonique « <Personne> devient <Fonction>
de <Structure> » pour :
1. Lisibilité (titre clair au lieu d'une accroche presse type
   « Cette semaine, Olbia a appris que… »).
2. Déduplication inter-sources : si Eric Woerth est nommé à la tête
   du PMU et que l'info est relayée par Olbia + Café + Sport Stratégies,
   on n'affiche qu'une fois (clé canonique nom+fonction+structure).
3. Pour les sources OFFICIELLES (JORF, ministères, fédérations…), on
   PRÉSERVE le titre original et l'URL — l'utilisateur a besoin du lien
   pour vérifier la source faisant foi (décret, communiqué officiel).

Approche en 2 passes (plus robuste qu'une regex monolithique) :
1. Localiser un verbe performatif dans le texte (`a été nommé`, `est
   élu`, `devient`, etc.)
2. Avant : remonter pour extraire la personne (1-4 mots Maj, civilité
   optionnelle).
3. Après : extraire la fonction (whitelist) puis la structure (après
   préposition « de / du / d' / à la tête de / au sein de »).

Robustesse :
- Tolérant aux virgules / adverbes entre personne et verbe (« Camille
  Emié, fraîchement nommée directrice… »).
- Plusieurs prépositions de structure (« de la FFF », « au sein du
  cabinet Eventeam », « à la tête du PMU »).
- Si pas de match : retourne None.
"""
from __future__ import annotations

import re
import unicodedata


# ---------------------------------------------------------------------------
# Sources « officielles » : titre + URL préservés à l'export.
# Pour les autres (presse), on remplace par un titre normalisé.
# ---------------------------------------------------------------------------

OFFICIAL_NOMINATION_SOURCES: frozenset[str] = frozenset({
    # JORF — décrets / arrêtés de nomination
    "dila_jorf",
    # Ministères et présidence — communiqués officiels
    "min_sports_actualites", "min_sports_presse", "elysee",
    "matignon_actualites", "min_sante", "min_travail",
    # Opérateurs publics (établissements de rattachement MinSports)
    "ans", "insep", "injep", "afld", "igesr",
    # Mouvement sportif national (instances de tutelle)
    "cnosf", "france_paralympique", "fdsf",
    # Fédérations sportives officielles (R41-B)
    "fff_actualites", "fft_actualites", "ffa_actualites",
    "ffr_actualites", "ffbb_actualites", "ffhb_actualites",
})


# ---------------------------------------------------------------------------
# Verbes performatifs — un par regex pour pouvoir itérer dans l'ordre
# ---------------------------------------------------------------------------

# Note : on capture le mot de civilité du sujet (« nommée » avec accord
# féminin) en option pour aider à reconnaître le genre. Le matching
# est fait via re.IGNORECASE donc « Nommée » et « nommée » matchent.
_VERB_PATTERNS = [
    # Forme « a été nommé(e) » / « a été élu(e) » — la plus précise
    re.compile(r"\ba\s+été\s+nommé[e]?\b", re.IGNORECASE),
    re.compile(r"\ba\s+été\s+élu[e]?\b", re.IGNORECASE),
    re.compile(r"\ba\s+été\s+désigné[e]?\b", re.IGNORECASE),
    # Formes « est nommé(e) » / « est élu(e) »
    re.compile(r"\best\s+nommé[e]?\b", re.IGNORECASE),
    re.compile(r"\best\s+élu[e]?\b", re.IGNORECASE),
    # Formes simples « nommé(e) » / « élu(e) » (participe passé sans
    # auxiliaire — apparaît souvent après une virgule, ex.
    # « X, fraîchement nommée directrice… »)
    re.compile(r"\b(?:fraîchement\s+|nouvellement\s+)?nommé[e]?\b",
               re.IGNORECASE),
    re.compile(r"\b(?:fraîchement\s+|nouvellement\s+)?(?:réélu[e]?|élu[e]?)\b",
               re.IGNORECASE),
    re.compile(r"\bdésigné[e]?\b", re.IGNORECASE),
    # « sera nommé(e) » / « va être nommé(e) » / « prendra ses fonctions »
    re.compile(r"\bsera\s+nommé[e]?\b", re.IGNORECASE),
    re.compile(r"\bva\s+être\s+nommé[e]?\b", re.IGNORECASE),
    re.compile(r"\bprend(?:ra)?\s+ses\s+fonctions\s+(?:de|à)\b",
               re.IGNORECASE),
    re.compile(r"\bprend(?:ra)?\s+la\s+(?:tête|présidence)\s+(?:de|du|des|d['']\s*)",
               re.IGNORECASE),
    # « devient » + fonction
    re.compile(r"\bdevient\b", re.IGNORECASE),
    # « succède à <X> » — patternplus complexe géré séparément si besoin
]


# ---------------------------------------------------------------------------
# Whitelist fonctions stratégiques (Cyril : pas de fonctions support)
# ---------------------------------------------------------------------------

# Patterns capture-friendly : on retourne le match le plus long.
_FUNCTION_PATTERN = re.compile(
    r"(?<![\w])"
    r"(?:"
    # Composés en premier (ordre = priorité longest-match)
    r"président(?:e)?[\s-]*(?:directeur|directrice)\s+général(?:e)?|"
    r"(?:directeur|directrice)\s+général(?:e)?\s+adjoint(?:e)?|"
    r"(?:directeur|directrice)\s+général(?:e)?|"
    r"(?:directeur|directrice)\s+technique\s+national(?:e)?|"
    r"(?:directeur|directrice)\s+technique|"
    r"(?:directeur|directrice)\s+sportif(?:ve)?|"
    r"(?:directeur|directrice)\s+de\s+cabinet|"
    r"(?:directeur|directrice)\s+conseil|"
    r"(?:directeur|directrice)\s+de\s+la\s+communication|"
    r"directrice\s+de\s+la\s+communication|"
    r"secrétaire\s+général(?:e)?|"
    r"vice[\s-]+président(?:e)?|"
    r"président(?:e)?|"
    # Sigles (en majuscules ; on cast en minuscule via _clean_function)
    r"PDG|DTN|DG|DGA"
    r")"
    r"(?![\w-])",
    re.IGNORECASE | re.UNICODE,
)


# ---------------------------------------------------------------------------
# Extraction de la personne (en remontant depuis le verbe)
# ---------------------------------------------------------------------------

# Personne = 2-4 mots commençant par majuscule. Civilités préfixes
# acceptées. Apostrophes & traits d'union dans les noms (ex. « Oudéa-Castéra »).
_PERSON_RE = re.compile(
    r"(?P<person>"
    r"(?:M\.\s+|Mme\s+|Mlle\s+|Mr\.?\s+|Madame\s+|Monsieur\s+|Mademoiselle\s+)?"
    r"(?:[A-ZÉÈÀÂÊÎÔÛÇÏÄËÜŸÑÆŒ][a-zéèàâêîôûçïäëüÿñæœ'-]+\s+){1,3}"
    r"[A-ZÉÈÀÂÊÎÔÛÇÏÄËÜŸÑÆŒ][a-zéèàâêîôûçïäëüÿñæœ'-]+"
    r")"
    r"(?:\s*,\s*(?:[\wÀ-ſ' -]{1,80}?))?\s*$"
)


# ---------------------------------------------------------------------------
# Extraction de la structure (après fonction)
# ---------------------------------------------------------------------------

# Prépositions « de la suite » : « de », « du », « des », « d' », « à la
# tête de », « au sein de », « au sein du », « auprès de », etc.
_PREP_STRUCT_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"à\s+la\s+(?:tête|présidence)\s+(?:de\s+|du\s+|des\s+|d['']\s*)|"
    r"au\s+sein\s+(?:de\s+|du\s+|des\s+|d['']\s*)|"
    r"auprès\s+(?:de\s+|du\s+|des\s+|d['']\s*)|"
    r"de\s+la\s+|de\s+l['']\s*|de\s+|du\s+|des\s+|d['']\s*"
    r")",
    re.IGNORECASE,
)

# Structure = ce qui suit jusqu'à fin de phrase / virgule forte / parenthèse
_STRUCT_RE = re.compile(
    r"^"
    r"(?P<organization>"
    r"(?:cabinet\s+|groupe\s+|société\s+|fondation\s+)?"
    r"(?:[A-ZÉÈÀÂÊÎÔÛÇ][\wÀ-ÿ'\-]*\s*){1,8}"
    r")",
    re.IGNORECASE,  # IGNORECASE pour matcher « Fédération » accentuée tolérante
)


# ---------------------------------------------------------------------------
# Helpers de normalisation
# ---------------------------------------------------------------------------


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def _clean_person(s: str) -> str:
    s = re.sub(
        r"^(?:M\.|Mme|Mlle|Mr\.?|Madame|Monsieur|Mademoiselle)\s+",
        "", s.strip(), flags=re.IGNORECASE,
    )
    s = re.sub(r"\s+", " ", s).strip()
    # Strip mot de fin si c'est un verbe-form connu (cas où la regex
    # de personne englobe le verbe). En pratique on laisse le caller
    # éviter ça via passage avant verbe.
    return s


def _clean_function(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip().lower())
    canonical = {
        "dg": "directeur général",
        "dga": "directeur général adjoint",
        "pdg": "président-directeur général",
        "dtn": "directeur technique national",
    }
    return canonical.get(s, s)


def _clean_organization(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().rstrip(".,;:!?»\"'")
    s = re.sub(r"\s+", " ", s).strip()
    # Fin de structure typique : avant un point, virgule, parenthèse.
    s = re.split(r"[.,;()«»]", s, maxsplit=1)[0].strip()
    # Si l'org commence par un sigle tout-majuscule, le préserver.
    return s[:120]


# ---------------------------------------------------------------------------
# Extraction principale
# ---------------------------------------------------------------------------


def extract_nomination_facts(text: str) -> dict | None:
    """Extrait `(person, function, organization)` depuis un texte.

    Renvoie un dict ou None. Algorithme en 2 passes :
    1. Localiser le 1er verbe performatif ;
    2. Avant : extraire la personne ;
    3. Après : extraire la fonction puis la structure.
    """
    if not text or not isinstance(text, str):
        return None
    # Normalise les apostrophes typographiques + espaces
    txt = (
        text.replace("’", "'")
            .replace("ʼ", "'")
            .replace(" ", " ")
    )

    for verb_re in _VERB_PATTERNS:
        m = verb_re.search(txt)
        if not m:
            continue
        before = txt[:m.start()].rstrip()
        after = txt[m.end():].lstrip()

        # PERSONNE — recherche dans la fin de `before` (max 100 chars
        # avant le verbe). On strippe la dernière virgule + ce qui suit
        # (cas « X, fraîchement, est nommée »).
        before_window = before[-100:]
        # Coupe à la dernière virgule pour exclure les incises
        # adverbiales (« X, fraîchement, est nommée »).
        if "," in before_window:
            # Conserve le segment AVANT la dernière virgule, qui contient
            # le sujet principal.
            parts = before_window.rsplit(",", 1)
            # On essaie d'abord la fin (segment APRÈS la virgule, court),
            # puis remonte si pas de match.
            search_segments = [parts[0], before_window]
        else:
            search_segments = [before_window]

        person = ""
        for seg in search_segments:
            pm = _PERSON_RE.search(seg.strip() + " ")
            if pm:
                cand = _clean_person(pm.group("person"))
                # Doit avoir au moins 2 mots
                if len(cand.split()) >= 2:
                    person = cand
                    break
        if not person:
            continue

        # FONCTION — cherche dans les 200 chars suivants
        after_window = after[:200]
        fm = _FUNCTION_PATTERN.search(after_window)
        if not fm:
            continue
        function = _clean_function(fm.group(0))
        # STRUCTURE — chez ce qui suit la fonction, après préposition
        post_func = after_window[fm.end():]
        prep_m = _PREP_STRUCT_RE.match(post_func)
        if prep_m:
            post_prep = post_func[prep_m.end():]
            org_m = _STRUCT_RE.match(post_prep)
            if org_m:
                org = _clean_organization(org_m.group("organization"))
            else:
                org = ""
        else:
            org = ""

        return {
            "person": person,
            "function": function,
            "organization": org,
        }
    return None


# ---------------------------------------------------------------------------
# Dédup canonique
# ---------------------------------------------------------------------------


def canonical_key(facts: dict) -> str:
    """Clé `<person>|<function>|<organization>` normalisée pour dédup
    inter-sources. Tokens triés pour ordre-insensibilité."""
    def _norm(s: str) -> str:
        if not s:
            return ""
        s = _strip_accents(s).lower()
        s = re.sub(r"[^\w\s-]", " ", s)
        toks = sorted(t for t in s.split() if t)
        return " ".join(toks)
    return "|".join((
        _norm(facts.get("person", "")),
        _norm(facts.get("function", "")),
        _norm(facts.get("organization", "")),
    ))


# ---------------------------------------------------------------------------
# Format canonique du titre
# ---------------------------------------------------------------------------


def format_normalized_title(facts: dict) -> str:
    """« <Personne> devient <Fonction> de <Structure> » canonique."""
    person = (facts.get("person") or "").strip()
    function = (facts.get("function") or "").strip()
    org = (facts.get("organization") or "").strip()
    if not person or not function:
        return ""
    if not org:
        return f"{person} devient {function}"[:220]
    # Choix de la préposition. Heuristique simple :
    # - sigle 2-6 lettres tout-maj → "du <SIGLE>"
    # - début par voyelle → "de <Nom>"
    # - sinon → "de <Nom>"
    first_word = org.split()[0] if org else ""
    if re.match(r"^[A-Z]{2,6}$", first_word):
        prep = "du "
    else:
        prep = "de "
    return f"{person} devient {function} {prep}{org}"[:220]


def is_official_source(source_id: str) -> bool:
    """True si la source garde son titre + URL d'origine à l'export.
    Sinon, l'extraction structurée + dédup s'applique."""
    return (source_id or "").strip() in OFFICIAL_NOMINATION_SOURCES
