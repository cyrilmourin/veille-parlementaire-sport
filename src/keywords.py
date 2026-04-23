"""Matching des mots-clés — normalisation accents + casse."""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Iterable

import yaml
from unidecode import unidecode


# R13-C (2026-04-21) : certaines sources remontent des summaries contenant
# du HTML brut (balises + entités numériques). Ex. Sénat amendements via
# `senat_amendements` : `<p style="text-align: justify;">Par cet amendement,
# les d&#x00E9;put&#x00E9;.es du groupe…`. On dépollue à la construction du
# snippet pour que le site et le digest affichent du texte propre.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTISPACE_RE = re.compile(r"\s+")


def _clean_html(text: str) -> str:
    """Strip HTML tags + décode les entités (&#x00E9; → é, &amp; → &)."""
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _MULTISPACE_RE.sub(" ", text).strip()
    return text


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
        # "First wins" : quand plusieurs variantes se normalisent pareil
        # (ex. "Activité physique adaptée" + "Activite physique adaptee"),
        # on garde la première rencontrée. Convention yaml : la variante
        # accentuée est toujours listée en premier pour que le libellé
        # affichable (R13-B) soit la forme typographiquement correcte.
        self.index: dict[str, tuple[str, str]] = {}
        for family, items in self.families.items():
            for term in items:
                key = _normalize(term)
                if key not in self.index:
                    self.index[key] = (term, family)

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

    def recapitalize(self, keywords: Iterable[str]) -> list[str]:
        """Remappe chaque kw déjà matché sur sa forme affichable du yaml.

        R13-B (2026-04-21) : les items ingérés avant la capitalisation du
        `config/keywords.yml` ont une liste `matched_keywords` persistée
        en minuscules non-accentuées ("jeux olympiques", "activite
        physique adaptee"). On remappe à l'export via l'index normalisé
        sans re-matcher le texte. Idempotent : si la forme passée est
        déjà canonique (égale à celle du yaml), renvoie telle quelle.

        Préserve l'ordre d'apparition et déduplique.
        """
        out: list[str] = []
        seen: set[str] = set()
        for kw in keywords or []:
            entry = self.index.get(_normalize(kw))
            canonical = entry[0] if entry else kw
            if canonical not in seen:
                seen.add(canonical)
                out.append(canonical)
        return out

    def build_snippet(self, original_text: str, window: int | None = None,
                      max_len: int = 800) -> str:
        """Extrait une phrase contenant le 1er mot-clé trouvé.

        On vise un extrait de longueur ~`max_len` centré sur le match,
        en raffinant aux bornes de phrase (`. `, `! `, `? `, `\\n`) quand
        elles existent. `window` (si fourni) fixe la fenêtre brute
        initiale de part et d'autre du match avant raffinage ; par défaut
        on prend `max_len // 2` pour que les textes peu/pas ponctués
        (CR AN, dumps plats) livrent tout de même un extrait proche de
        `max_len`. Cap final à `max_len`.

        R17 (2026-04-22) — fix extraits CR trop courts. Avant : `window`
        fixe à 80 chars ; pour les comptes rendus qui contiennent peu
        de points/!/?, le raffinage aux bornes de phrase ne trouvait
        rien à étendre → l'extrait restait à ~160 chars même avec
        `max_len=500`. Désormais la fenêtre brute suit `max_len`, donc
        un appel `max_len=500` livre réellement ~500 chars quand la
        matière est là.

        R14 : défaut remonté de 320 → 800 pour laisser de la marge aux
        appelants qui veulent des extraits longs (ex. `comptes_rendus`
        demandé à 500 chars par Cyril en R13-K). Les appelants conscients
        de leur catégorie (site_export, digest) passent explicitement
        `max_len=SNIPPET_LEN_BY_CATEGORY[cat]` pour éviter de payer les
        800 chars systématiquement. Le défaut 800 sert de filet pour les
        appels sans contexte (p.ex. `KeywordMatcher.apply`).

        R13-C : dépollue l'HTML en amont (tags + entités) — sinon le
        snippet affiche `&#x00E9;put&#x00E9;.es` au lieu de `député.es`
        (cas Sénat amendements).
        """
        original_text = _clean_html(original_text)
        if not original_text:
            return ""
        # Fenêtre brute = max_len/2 par défaut. On garde ce calcul même quand
        # `window` est passé explicitement (pour les tests / appels legacy) :
        # dans ce cas on prend le max des deux pour ne pas régresser les
        # extraits courts.
        effective_window = max(window or 0, max_len // 2)
        haystack_norm = _normalize(original_text)
        m = self._pattern.search(haystack_norm)
        if not m:
            return original_text.strip()[: max_len].strip()
        pos = m.start()
        end = m.end()
        # Fenêtre initiale large
        start_cut = max(0, pos - effective_window)
        end_cut = min(len(original_text), end + effective_window)

        # Raffinage : si on trouve une borne de phrase dans la fenêtre,
        # on la préfère (évite de couper en plein milieu d'un mot). On
        # garde la PREMIÈRE borne (la plus éloignée en arrière) qui laisse
        # au moins 60% de la fenêtre — les suivantes seraient trop proches
        # du match et donneraient un extrait court sur des textes peu
        # ponctués (CR AN avec « M. Dupond »).
        back_limit = max(0, pos - effective_window)
        min_back_span = int(effective_window * 0.6)
        for boundary in re.finditer(r"[\.\!\?\n]\s+", original_text[back_limit:pos]):
            candidate_start = back_limit + boundary.end()
            if pos - candidate_start >= min_back_span:
                start_cut = candidate_start
                break  # première borne "lointaine" suffit

        fwd_limit = min(len(original_text), end + effective_window)
        fwd_match = re.search(r"[\.\!\?](?:\s|$)", original_text[end:fwd_limit])
        if fwd_match:
            # R17 (2026-04-22) : n'accepte la borne de phrase que si elle
            # tombe après une proportion suffisante de max_len. Sinon on
            # a affaire à une fausse coupure type « M. Dupond » (abréviation)
            # qui flingue les CR AN. Seuil : au moins 60% de max_len
            # atteint — en dessous, on préfère la fenêtre brute.
            candidate_end = end + fwd_match.end()
            approx_len = candidate_end - start_cut
            if approx_len >= int(max_len * 0.6):
                end_cut = candidate_end
            # sinon on reste sur end_cut = end + effective_window

        snippet = original_text[start_cut:end_cut].strip()
        # Cap final (au cas où une phrase gigantesque)
        if len(snippet) > max_len:
            snippet = snippet[:max_len].rstrip() + "…"
        prefix = "…" if start_cut > 0 else ""
        suffix = "…" if end_cut < len(original_text) and not snippet.endswith(("…", ".", "!", "?")) else ""
        return (prefix + snippet + suffix).replace("\n", " ").strip()

    def apply(self, items: Iterable):
        """Annote in-place une liste d'Item (keywords + snippet).

        R26 (2026-04-23) : si `item.raw` contient une clé `haystack_body`
        (JORF notamment), on l'ajoute au match — permet de capter des
        textes dont le titre est générique mais dont le corps mentionne
        les thèmes surveillés. Le snippet reste construit depuis
        `summary` pour rester court côté affichage.
        """
        for item in items:
            extra_haystack = ""
            raw = getattr(item, "raw", None)
            if isinstance(raw, dict):
                extra_haystack = raw.get("haystack_body") or ""
            kws, fams = self.match(item.title, item.summary, extra_haystack)
            item.matched_keywords = kws
            item.keyword_families = fams
            # Snippet : priorité au summary (plus riche), fallback sur le titre
            item.snippet = self.build_snippet(item.summary or item.title or "")
        return items
