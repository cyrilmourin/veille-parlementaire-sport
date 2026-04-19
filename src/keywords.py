"""Matching des mots-clés — normalisation accents + casse."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import yaml
from unidecode import unidecode


def _normalize(text: str) -> str:
    """Minuscules, sans accent, espaces simples."""
    if not text:
        return ""
    text = unidecode(text).lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


class KeywordMatcher:
    def __init__(self, path: str | Path):
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.families: dict[str, list[str]] = {
            family: [k for k in items] for family, items in raw.items()
        }
        # index plat {terme_normalisé: (terme_original, famille)}
        self.index: dict[str, tuple[str, str]] = {}
        for family, items in self.families.items():
            for term in items:
                self.index[_normalize(term)] = (term, family)

        # pré-compile une regex OR pour accélérer le matching
        # Tri par longueur desc pour privilégier le match le plus spécifique
        terms = sorted(self.index.keys(), key=len, reverse=True)
        escaped = [re.escape(t) for t in terms]
        # frontières \b uniquement si le terme commence/finit par un caractère word
        self._pattern = re.compile(
            r"(?<![a-z0-9])(" + "|".join(escaped) + r")(?![a-z0-9])"
        )

    def match(self, *texts: str) -> tuple[list[str], list[str]]:
        """Renvoie (mots-clés matchés, familles uniques)."""
        haystack = _normalize(" ".join(t or "" for t in texts))
        if not haystack:
            return [], []
        found_raw = set(self._pattern.findall(haystack))
        matched = []
        families = set()
        for t in found_raw:
            orig, fam = self.index.get(t, (t, ""))
            matched.append(orig)
            if fam:
                families.add(fam)
        return sorted(set(matched)), sorted(families)

    def build_snippet(self, original_text: str, window: int = 80) -> str:
        """Extrait ~window chars de part et d'autre du 1er mot-clé trouvé
        dans le texte ORIGINAL (avec accents / casse) — utilisé pour la
        page d'accueil du site et le digest email."""
        if not original_text:
            return ""
        haystack_norm = _normalize(original_text)
        m = self._pattern.search(haystack_norm)
        if not m:
            # Aucun mot-clé trouvé : renvoyer les premiers N chars
            return original_text.strip()[: 2 * window].strip()
        # Position approximative du match dans le texte original :
        # on se base sur la position dans la version normalisée,
        # qui est alignée char-for-char pour la plupart des cas
        # (unidecode conserve la taille sauf exceptions rares).
        pos = m.start()
        end = m.end()
        start_cut = max(0, pos - window)
        end_cut = min(len(original_text), end + window)
        snippet = original_text[start_cut:end_cut].strip()
        prefix = "…" if start_cut > 0 else ""
        suffix = "…" if end_cut < len(original_text) else ""
        return (prefix + snippet + suffix).replace("\n", " ").strip()

    def apply(self, items: Iterable):
        """Annote in-place une liste d'Item (keywords + snippet)."""
        for item in items:
            kws, fams = self.match(item.title, item.summary)
            item.matched_keywords = kws
            item.keyword_families = fams
            # Snippet : priorité au summary (plus riche), fallback sur le titre
            item.snippet = self.build_snippet(item.summary or item.title or "")
        return items
