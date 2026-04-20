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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .digest import CATEGORY_LABELS, CATEGORY_ORDER

# Fenêtre de publication visible sur le site (jours) — par défaut pour les
# flux à forte rotation (questions, CR, amendements, communiqués, agenda).
WINDOW_DAYS = 30

# Fenêtre spécifique par catégorie pour les flux à cycle long (dossiers
# législatifs : navettes de plusieurs mois à plusieurs années). Le dict prime
# sur WINDOW_DAYS pour les catégories listées.
WINDOW_DAYS_BY_CATEGORY: dict[str, int] = {
    "dossiers_legislatifs": 730,   # 2 ans — cycle législatif complet
}

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
    """Parse best-effort d'un datetime stocké en string ISO."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        # fallback : juste la date
        try:
            return datetime.fromisoformat(s[:10])
        except Exception:
            return None


def _load(rows: list[dict]) -> list[dict]:
    """Parse les colonnes JSON-string vers des objets."""
    out = []
    for r in rows:
        r = dict(r)
        try:
            r["matched_keywords"] = json.loads(r.get("matched_keywords") or "[]")
        except Exception:
            r["matched_keywords"] = []
        try:
            r["keyword_families"] = json.loads(r.get("keyword_families") or "[]")
        except Exception:
            r["keyword_families"] = []
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

    Stratégie stricte : on n'utilise plus `inserted_at` comme fallback pour
    les items datés, afin d'éviter qu'un item sans date officielle se voie
    attribuer la date du jour. Un item sans `published_at` est conservé
    uniquement s'il a été inséré récemment (dans la fenêtre de sa catégorie).
    """
    now = datetime.utcnow()
    kept = []
    for r in rows:
        window = _window_for(r.get("category"))
        cutoff = now - timedelta(days=window)
        dt = _parse_dt(r.get("published_at"))
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
    items_dir.mkdir(parents=True, exist_ok=True)

    # Charge + filtre 30 jours glissants + tri date desc + dédup
    rows = _load(rows)
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

def _fmt_item_line(it: dict, with_tags: bool = True) -> str:
    """Ligne Markdown d'un item (home / catégorie). Layout :

    - **[Titre](url)** <span class="chamber" data-chamber="AN">AN</span> · Date · tags inline
      <snippet éventuel>

    Si url est vide (typiquement catégorie agenda, cf. `_normalize_agenda`),
    le titre est rendu en texte simple, sans lien cliquable — alignement
    sur Follaw qui affiche les réunions sans hypertexte.

    `with_tags=False` : n'affiche pas les mots-clés. Utilisé par la section
    "Dernières 24 h" pour ne garder que titre + chambre + date (demande
    utilisateur : zone très compacte, les tags encombrent).
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

    # Mots-clés : inline (pas de retour à la ligne), sur la même ligne que
    # la meta. Coloration via CSS .kw-tag[data-family=...].
    tags_html = ""
    if kws and with_tags:
        tags_html = " ".join(
            f'<span class="kw-tag" data-family="{_escape(dominant_fam)}">'
            f'{_escape(k)}</span>'
            for k in kws[:12]
        )

    meta_parts = [p for p in [chamber_html, status_html, date_html, tags_html] if p]
    meta_inline = (" · ".join(meta_parts)) if meta_parts else ""
    meta_html = (
        f' <span class="item-meta">{meta_inline}</span>' if meta_inline else ""
    )

    # Titre : hypertexte uniquement si on a une URL exploitable.
    # Sinon (ex. réunions AN : pas d'URL publique stable), on affiche
    # le titre en texte gras simple — cf. Follaw.
    if url:
        line = f"- **[{title}]({url})**{meta_html}"
    else:
        line = f"- **{title}**{meta_html}"

    if snippet:
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
        window = _window_for(cat)
        # Tri explicite du bucket par date desc (plus récent en haut)
        bucket = _sort_by_date_desc(by_cat[cat])
        count = len(bucket)
        # <details> HTML brut — rendu nativement par tous les navigateurs,
        # pas de JS. `open` n'est PAS positionné par défaut → tout est plié.
        # Le summary contient le compteur et la fenêtre.
        lines.append(f'<details class="cat-fold" data-cat="{_escape(cat)}">')
        lines.append(
            f'<summary><span class="cat-label">{_escape(label)}</span>'
            f' <span class="cat-count">{count}</span>'
            f' <span class="cat-window">fenêtre {window} j</span>'
            f' <a class="cat-all" href="/items/{cat}/">voir tout →</a>'
            f'</summary>'
        )
        lines.append("")
        for it in bucket[:10]:
            lines.append(_fmt_item_line(it))
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
        lines = [
            "---",
            f'title: "{label}"',
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
        snippet = (r.get("snippet") or "").replace('"', "'").replace("\n", " ")
        # Remonte les champs enrichis depuis `raw` pour les dossiers
        # législatifs (status_label + is_promulgated injectés par
        # assemblee._normalize_dosleg). Permet à list.html d'afficher le
        # badge de statut sur /items/dossiers_legislatifs/.
        raw = r.get("raw") or {}
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
