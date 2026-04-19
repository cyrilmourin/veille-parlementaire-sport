"""Connecteur Sénat — open data (CSV/ZIP) + RSS actualités."""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime
from typing import Iterable

import feedparser

from ..models import Item
from ._common import fetch_bytes, fetch_text, parse_iso, unzip_members

log = logging.getLogger(__name__)


def _first_sentence(text: str, max_len: int = 140) -> str:
    """Renvoie la 1re phrase du texte, tronquée à max_len."""
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"[\.\!\?]\s", clean[:max_len])
    if m:
        return clean[: m.end()].strip()
    return clean[:max_len].rstrip() + ("…" if len(clean) > max_len else "")

# --- mapping CSV ------------------------------------------------------------

# Les CSV Sénat sont en UTF-8 avec délimiteur ';' et guillemets double.
# Colonnes courantes (varient selon le fichier — on lit par nom).
SEP = ";"


def _read_csv(payload: bytes, sid: str = "") -> Iterable[dict]:
    """Lit un CSV Sénat. Les fichiers open data Sénat ont parfois basculé
    entre ';' et ',' selon les datasets. On teste les deux séparateurs et
    on retient celui qui produit le plus de colonnes."""
    text = payload.decode("utf-8-sig", errors="replace")
    # Sniff délimiteur
    first_line = text.split("\n", 1)[0] if text else ""
    sep = SEP
    if first_line.count(",") > first_line.count(";"):
        sep = ","
    reader = csv.DictReader(io.StringIO(text), delimiter=sep)
    rows = []
    for row in reader:
        rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
    # Log diagnostic : nombre de lignes + noms des colonnes
    if rows:
        cols = list(rows[0].keys())
        log.info("Sénat %s : CSV lu (%d lignes, sep=%r) — colonnes : %s",
                 sid, len(rows), sep, cols[:12])
    else:
        log.warning("Sénat %s : CSV vide ou illisible (sep=%r, len=%d)",
                    sid, sep, len(text))
    return iter(rows)


def fetch_source(src: dict) -> list[Item]:
    fmt = src.get("format")
    sid = src["id"]
    log.info("Fetch Sénat %s (%s) %s", sid, fmt, src["url"])
    try:
        if fmt == "csv":
            payload = fetch_bytes(src["url"])
            rows = list(_read_csv(payload, sid))
            items = list(_normalize_rows(src, rows))
            log.info("Sénat %s : %d items normalisés (sur %d lignes CSV)",
                     sid, len(items), len(rows))
            return items
        if fmt == "csv_zip":
            data = fetch_bytes(src["url"])
            items: list[Item] = []
            members = list(unzip_members(data))
            log.info("Sénat %s : ZIP contient %d fichiers", sid, len(members))
            for name, payload in members:
                if not name.lower().endswith(".csv"):
                    continue
                rows = list(_read_csv(payload, f"{sid}:{name}"))
                batch = list(_normalize_rows(src, rows, csv_name=name))
                items.extend(batch)
                log.info("Sénat %s/%s : %d items (sur %d lignes)",
                         sid, name, len(batch), len(rows))
            return items
        if fmt == "rss":
            return _normalize_rss(src, fetch_text(src["url"]))
    except Exception as e:
        log.exception("Sénat %s KO: %s", sid, e)
    return []


def _pick(row: dict, *names, default: str = "") -> str:
    """Premier champ non-vide parmi une liste de noms probables (tolérant sur la casse)."""
    low = {k.lower(): v for k, v in row.items()}
    for n in names:
        v = low.get(n.lower())
        if v:
            return v
    return default


def _normalize_rows(src: dict, rows: list[dict], csv_name: str = "") -> Iterable[Item]:
    sid = src["id"]
    cat = src["category"]

    # Dossiers législatifs — colonnes qui ont bougé : numero_initiative,
    # numeroInitiative, id_dosleg, etc. On liste large.
    if sid in ("senat_dosleg", "senat_ppl", "senat_promulguees"):
        for r in rows:
            uid = _pick(r, "numero_initiative", "numeroInitiative",
                         "numero", "num", "id_dosleg", "id", "uid")
            titre = _pick(r, "titre", "intitule", "libelle", "intituleLong")
            date = parse_iso(_pick(r, "date_depot", "dateDepot",
                                     "datePromulgation", "datePublication",
                                     "date_publication", "date"))
            if not uid or not titre:
                continue
            url = _pick(r, "url", "lien") or f"https://www.senat.fr/dossier-legislatif/{uid}.html"
            # Contenu utile pour matching : on ajoute tout le texte du row
            extras = " ".join(v for v in r.values() if isinstance(v, str) and len(v) > 3)
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=titre[:220], url=url,
                published_at=date,
                summary=(titre + " — " + extras)[:2000],
                raw=r,
            )

    elif sid == "senat_rapports":
        for r in rows:
            uid = _pick(r, "numero", "num", "id", "uid")
            titre = _pick(r, "titre", "intitule", "libelle")
            if not uid or not titre:
                continue
            extras = " ".join(v for v in r.values() if isinstance(v, str) and len(v) > 3)
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=f"Rapport n°{uid} — {titre}"[:220],
                url=_pick(r, "url", "lien") or f"https://www.senat.fr/rap/{uid}.html",
                published_at=parse_iso(_pick(r, "date", "datePublication", "date_publication")),
                summary=extras[:2000], raw=r,
            )

    elif sid in ("senat_ameli",):
        for r in rows:
            uid = _pick(r, "num_amdt", "numero", "id", "uid", "numeroAmendement")
            obj = _pick(r, "objet", "titre", "libelle")
            disp = _pick(r, "dispositif", "texteAmendement", "texte")
            auteur = _pick(r, "auteur", "nomAuteur", "signataire")
            sort = _pick(r, "sort", "statut", "etatAmendement")
            if not uid:
                continue
            title_bits = [f"Amendement n°{uid}"]
            if sort:
                title_bits.append(f"[{sort}]")
            if auteur:
                title_bits.append(f"— {auteur}")
            summary_parts = [f"Auteur : {auteur}" if auteur else "",
                             f"Sort : {sort}" if sort else "",
                             obj, disp]
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=" ".join(title_bits)[:220],
                url=_pick(r, "url", "lien") or f"https://www.senat.fr/enseance/{uid}.html",
                published_at=parse_iso(_pick(r, "date", "datePublication")),
                summary=" — ".join(p for p in summary_parts if p)[:2000], raw=r,
            )

    elif sid in ("senat_questions", "senat_qg", "senat_questions_1an"):
        for r in rows:
            uid = _pick(r, "numQuestion", "numero", "num", "id", "uid")
            titre = _pick(r, "titre", "objet", "intitule")
            texte = _pick(r, "texte", "texteQuestion", "libelle")
            rubrique = _pick(r, "rubrique", "theme")
            auteur = _pick(r, "auteur", "nomAuteur", "senateur", "signataire")
            ministere = _pick(r, "ministere", "ministereAttributaire",
                               "minInt", "destinataire")
            if not uid:
                continue
            sujet = (titre or rubrique or _first_sentence(texte, 100) or "Question").strip()
            qtype_label = {
                "senat_questions": "Question écrite",
                "senat_qg": "Question au gouvernement",
                "senat_questions_1an": "Question de +1 an sans réponse",
            }.get(sid, "Question")
            title_bits = [f"{qtype_label} n°{uid}"]
            if auteur:
                title_bits.append(f"— {auteur}")
            if ministere:
                title_bits.append(f"→ {ministere}")
            title_bits.append(f": {sujet}")
            summary = " — ".join(p for p in [
                auteur, f"Destinataire : {ministere}" if ministere else "",
                f"Rubrique : {rubrique}" if rubrique else "",
                texte, titre,
            ] if p)[:2000]
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=" ".join(title_bits)[:220],
                url=_pick(r, "url", "lien") or f"https://www.senat.fr/questions/base/{uid}.html",
                published_at=parse_iso(_pick(r, "date", "datePublication", "date_publication")),
                summary=summary, raw=r,
            )

    elif sid in ("senat_debats", "senat_cri"):
        for r in rows:
            uid = _pick(r, "id", "numero", "uid", "date")
            titre = _pick(r, "titre", "sujet", "libelle") or "Séance publique"
            if not uid:
                continue
            extras = " ".join(v for v in r.values() if isinstance(v, str) and len(v) > 3)
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=titre[:220],
                url=_pick(r, "url", "lien") or "https://www.senat.fr/seances/",
                published_at=parse_iso(_pick(r, "date", "datePublication")),
                summary=extras[:2000], raw=r,
            )


def _normalize_rss(src, text: str) -> list[Item]:
    d = feedparser.parse(text)
    out = []
    for e in d.entries:
        uid = getattr(e, "id", None) or getattr(e, "link", "")
        if not uid:
            continue
        dt = None
        if getattr(e, "published_parsed", None):
            dt = datetime(*e.published_parsed[:6])
        out.append(Item(
            source_id=src["id"], uid=uid, category=src["category"], chamber="Senat",
            title=(getattr(e, "title", "") or "")[:220],
            url=getattr(e, "link", ""),
            published_at=dt,
            summary=(getattr(e, "summary", "") or "")[:500],
            raw={},
        ))
    return out
