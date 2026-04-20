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

    def build_snippet(self, original_text: str, window: int = 80,
                      max_len: int = 320) -> str:
        """Extrait une phrase contenant le 1er mot-clé trouvé.

        On repart d'une fenêtre brute ~`window` chars de part et d'autre
        du match, puis on étend aux bornes de phrase les plus proches
        (`. `, `! `, `? `, `\\n`) pour produire un extrait lisible plutôt
        qu'une troncation arbitraire. Cap à `max_len` pour éviter les
        paragraphes entiers.
        """
        if not original_text:
            return ""
        haystack_norm = _normalize(original_text)
        m = self._pattern.search(haystack_norm)
        if not m:
            return original_text.strip()[: 2 * window].strip()
        pos = m.start()
        end = m.end()
        # Fenêtre initiale large
        start_cut = max(0, pos - window)
        end_cut = min(len(original_text), end + window)

        # Étend start_cut en arrière jusqu'à la dernière borne de phrase
        # (on ne dépasse pas pos - max_len/2 pour garder le contexte proche).
        back_limit = max(0, pos - max_len // 2)
        for boundary in re.finditer(r"[\.\!\?\n]\s+", original_text[back_limit:pos]):
            # On garde la dernière occurrence avant pos → on met à jour à chaque itération
            start_cut = back_limit + boundary.end()

        # Étend end_cut en avant jusqu'à la prochaine borne de phrase
        # (cap à end + max_len/2 pour éviter d'engloutir tout le texte).
        fwd_limit = min(len(original_text), end + max_len // 2)
        fwd_match = re.search(r"[\.\!\?](?:\s|$)", original_text[end:fwd_limit])
        if fwd_match:
            end_cut = end + fwd_match.end()

        snippet = original_text[start_cut:end_cut].strip()
        # Cap final (au cas où une phrase gigantesque)
        if len(snippet) > max_len:
            snippet = snippet[:max_len].rstrip() + "…"
        prefix = "…" if start_cut > 0 else ""
        suffix = "…" if end_cut < len(original_text) and not snippet.endswith(("…", ".", "!", "?")) else ""
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
