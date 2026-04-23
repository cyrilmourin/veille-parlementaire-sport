"""R32 (2026-04-24) — Nettoyage texte centralisé (audit §4.2).

Contexte : avant R32, la logique « strip HTML + unescape + collapse
whitespace + décodage bytes » vivait en trois copies presque identiques
(keywords._clean_html, senat._strip_html, senat_amendements._strip_html)
plus des variantes dans assemblee._strip_html_text (XHTML imbriqué
structuré en JSON) et senat._decode_payload (décodage bytes).

Problème : chaque incident d'encoding ou de markup (R13-C sur Sénat
amendements, R19-A encoding ISO-8859-15, entités `&#x00E9;`, espaces
insécables `\\u00a0` / `\\u202f` dans les dispositifs Sénat) devait être
corrigé dans chaque copie — ce qui n'a pas toujours été fait.

Ce module centralise les primitives. Les helpers par-source qui font
autre chose (walker XHTML-en-JSON AN, fallback Latin-9 via feedparser
en bytes, etc.) restent locaux mais peuvent déléguer pour la partie
strip/decode commune.

Primitives :
- `strip_html(text)` — retire les balises + décode entités + collapse
  whitespace + nettoie les espaces non-sécables.
- `decode_bytes(payload, candidates=...)` — tente plusieurs encodings
  dans l'ordre, retourne le premier qui passe. Dernier recours :
  utf-8 avec `errors="replace"` (jamais lève).
- `strip_technical_prefix(text, markers, max_prefix=600)` — retire un
  préambule technique si l'un des `markers` est trouvé dans les N
  premiers caractères. Généralise `_strip_cr_an_preamble`.
- `smart_truncate(text, max_len)` — tronque à la limite la plus proche
  d'un espace (pas de coupure mid-word), ajoute « … ».

Aucun import de ce module ne doit dépendre de pydantic, yaml ou
d'autres modules du projet : `textclean` doit rester une brique utilitaire
pure, sans side-effects, testable unitairement.
"""
from __future__ import annotations

import html as _html
import re
from typing import Sequence


# ---------------------------------------------------------------------------
# HTML strip / entity unescape / whitespace
# ---------------------------------------------------------------------------


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTISPACE_RE = re.compile(r"\s+")

# Espaces Unicode invisibles qu'on normalise en espace classique :
# - U+00A0 : espace insécable (NBSP, très courant Sénat amendements)
# - U+202F : narrow no-break space (usité par les dispositifs Sénat récents)
# - U+2009 : thin space
# - U+200B : zero-width space (parfois injecté par les CMS)
# - U+FEFF : BOM inséré au milieu du texte par Word / export Sénat
_INVISIBLE_TO_SPACE = str.maketrans({
    "\u00a0": " ",
    "\u202f": " ",
    "\u2009": " ",
    "\u200b": " ",
    "\ufeff": " ",
})


def strip_html(text: str | None) -> str:
    """Retire les balises HTML, décode les entités (`&#x00E9;` → `é`,
    `&amp;` → `&`), normalise les espaces invisibles (NBSP, narrow NBSP,
    BOM, zero-width) en espaces classiques puis collapse les whitespaces.

    Idempotent : appliquer deux fois renvoie la même chaîne.

    Zéro import lourd : uniquement `html.unescape` et `re`.
    """
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    text = text.translate(_INVISIBLE_TO_SPACE)
    text = _MULTISPACE_RE.sub(" ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Décodage bytes → str
# ---------------------------------------------------------------------------


# Ordre conseillé pour les sources FR :
# 1. utf-8-sig (dé-BOM si présent) — ouvre la porte aux fichiers Sénat récents
# 2. cp1252 (Windows-1252, alias Latin-1 étendu) — historique Sénat/Assemblée
# 3. iso-8859-15 (Latin-9) — flux RSS Sénat thème sport (R19-A)
# 4. utf-8 errors="replace" — dernier filet, ne lève jamais
DEFAULT_ENCODING_CANDIDATES: tuple[str, ...] = (
    "utf-8-sig", "cp1252", "iso-8859-15",
)


def decode_bytes(
    payload: bytes,
    candidates: Sequence[str] = DEFAULT_ENCODING_CANDIDATES,
) -> tuple[str, str]:
    """Décode `payload` en essayant plusieurs encodings dans l'ordre.

    Retourne (texte, encoding_utilisé). Si aucun candidat ne passe en
    mode strict, tombe sur `utf-8` avec `errors="replace"` (indique
    `utf-8+replace` comme encoding pour tracer que le texte peut
    contenir des `\\ufffd`).

    Ne lève jamais sur des bytes : ce module est utilisé sur des flux
    externes dont on ne contrôle pas l'encoding.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError(f"decode_bytes attend bytes, reçu {type(payload).__name__}")
    for enc in candidates:
        try:
            return payload.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return payload.decode("utf-8", errors="replace"), "utf-8+replace"


# ---------------------------------------------------------------------------
# Strip préambule technique (Syceron, etc.)
# ---------------------------------------------------------------------------


def strip_technical_prefix(
    text: str,
    markers: Sequence[str],
    max_prefix: int = 600,
) -> str:
    """Retire un préambule technique en début de `text` si l'un des
    `markers` est trouvé dans les `max_prefix` premiers caractères.

    Utilisé pour le préambule Syceron des CR AN (R19-G/R23-F) :
    identifiants CRSANR5L17…, libellés techniques, numéros de séance
    isolés — tout ce qui précède le vrai début de débat (« Présidence
    de … », « Questions au gouvernement », etc.).

    Idempotent : appliquer deux fois renvoie la même chaîne.

    Aucun marqueur trouvé → on renvoie le texte tel quel (pas de coupe
    hasardeuse à mi-chemin, contrairement à une heuristique sur la
    position d'un `.` lointain).
    """
    if not text:
        return text
    best_idx = -1
    for marker in markers:
        idx = text.find(marker)
        # idx == 0 (marqueur en tête après une re-application) est accepté
        # pour bloquer une coupe ultérieure sur un marqueur suivant.
        if 0 <= idx <= max_prefix and (best_idx < 0 or idx < best_idx):
            best_idx = idx
    if best_idx > 0:
        return text[best_idx:]
    return text


# ---------------------------------------------------------------------------
# Truncate propre
# ---------------------------------------------------------------------------


def smart_truncate(text: str, max_len: int, *, ellipsis: str = "…") -> str:
    """Tronque `text` à `max_len` caractères sans couper au milieu d'un
    mot. Cherche le dernier espace <= max_len ; si absent (mot unique
    monstrueux), coupe net.

    Ajoute `ellipsis` (par défaut « … ») si on a effectivement tronqué.
    N'ajoute rien si `text` passe déjà sous la limite.
    """
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rstrip()
    space = cut.rfind(" ")
    if space > 0 and space > int(max_len * 0.5):
        cut = cut[:space].rstrip()
    return cut + ellipsis
