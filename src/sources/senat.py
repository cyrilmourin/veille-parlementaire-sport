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


def _decode_payload(payload: bytes) -> tuple[str, str]:
    """Décode un CSV Sénat : les fichiers sont historiquement en Latin-1
    (cp1252), pas UTF-8 — les logs montraient 'Num�ro', 'D�cision', etc.
    On tente UTF-8 strict, puis cp1252 en fallback."""
    try:
        return payload.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return payload.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        pass
    return payload.decode("utf-8", errors="replace"), "utf-8+replace"


def _read_csv(payload: bytes, sid: str = "") -> Iterable[dict]:
    """Lit un CSV Sénat. Les fichiers open data Sénat ont parfois basculé
    entre ';' et ',' selon les datasets. On teste les deux séparateurs et
    on retient celui qui produit le plus de colonnes."""
    text, enc = _decode_payload(payload)
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
        log.info("Sénat %s : CSV lu (%d lignes, sep=%r, enc=%s) — colonnes : %s",
                 sid, len(rows), sep, enc, cols[:12])
    else:
        log.warning("Sénat %s : CSV vide ou illisible (sep=%r, enc=%s, len=%d)",
                    sid, sep, enc, len(text))
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


_NORM_RE = re.compile(r"[\s_\-\.]+")

def _norm_key(s: str) -> str:
    """Normalise un nom de colonne : minuscule, sans accents, sans espaces/_-."""
    if not s:
        return ""
    import unicodedata
    # Supprime les diacritiques (é→e, à→a, ç→c…)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    s = _NORM_RE.sub("", s)
    return s


def _pick(row: dict, *names, default: str = "") -> str:
    """Premier champ non-vide parmi une liste de noms probables. Tolérant
    à la casse, aux accents et aux espaces : 'numero de texte', 'Numéro de
    texte', 'numero_texte', 'numeroTexte' matchent tous la même clé."""
    normed = {_norm_key(k): v for k, v in row.items()}
    for n in names:
        v = normed.get(_norm_key(n))
        if v:
            return v
    return default


def _normalize_rows(src: dict, rows: list[dict], csv_name: str = "") -> Iterable[Item]:
    sid = src["id"]
    cat = src["category"]

    # Dossiers législatifs — colonnes réelles (CSV en cp1252) :
    # ppl       : 'Numéro de texte', 'Titre', 'Date de dépôt', 'URL du dossier', ...
    # promulguees : 'Titre', 'Numéro de la loi', 'Date de promulgation', 'URL du dossier'
    # dosleg    : format legacy / peut varier
    if sid in ("senat_dosleg", "senat_ppl", "senat_promulguees"):
        for r in rows:
            uid = _pick(r, "Numéro de texte", "Numéro de la loi",
                         "numero_initiative", "numeroInitiative",
                         "numero", "num", "id_dosleg", "id", "uid")
            titre = _pick(r, "Titre", "intitule", "libelle", "intituleLong")
            date = parse_iso(_pick(r, "Date de dépôt", "Date de promulgation",
                                     "Date initiale", "Date de la décision",
                                     "date_depot", "dateDepot",
                                     "datePromulgation", "datePublication",
                                     "date_publication", "date"))
            if not uid or not titre:
                continue
            url = (_pick(r, "URL du dossier", "url", "lien")
                   or f"https://www.senat.fr/dossier-legislatif/{uid}.html")
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
        # Colonnes réelles : Session, Numéro, Tome, Type de rapport, Auteurs,
        # Organismes, Titre court, Titre long, Résumé, Date de dépôt, URL, Thèmes
        for r in rows:
            uid = _pick(r, "Numéro", "numero", "num", "id", "uid")
            titre = (_pick(r, "Titre long", "Titre court", "titre",
                           "intitule", "libelle"))
            resume = _pick(r, "Résumé", "resume")
            auteurs = _pick(r, "Auteurs", "auteurs", "auteur")
            themes = _pick(r, "Thèmes", "themes")
            organismes = _pick(r, "Organismes", "organismes")
            if not uid or not titre:
                continue
            extras = " — ".join(p for p in [titre, resume, auteurs, themes,
                                             organismes] if p)
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=f"Rapport n°{uid} — {titre}"[:220],
                url=(_pick(r, "URL", "url", "lien")
                     or f"https://www.senat.fr/rap/{uid}.html"),
                published_at=parse_iso(_pick(r, "Date de dépôt", "date",
                                              "datePublication", "date_publication")),
                summary=extras[:2000], raw=r,
            )

    elif sid in ("senat_ameli",):
        for r in rows:
            uid = _pick(r, "Numéro", "num_amdt", "numero", "id", "uid",
                         "numeroAmendement")
            obj = _pick(r, "Objet", "objet", "titre", "libelle")
            disp = _pick(r, "Dispositif", "dispositif", "texteAmendement",
                           "texte")
            # Auteur reconstitué si colonnes séparées
            civ = _pick(r, "Civilité", "civilite")
            prenom = _pick(r, "Prénom", "prenom")
            nom = _pick(r, "Nom", "nom", "Auteur", "auteur", "nomAuteur",
                         "signataire")
            auteur = " ".join(p for p in [civ, prenom, nom] if p).strip() or nom
            groupe = _pick(r, "Groupe", "groupe")
            sort = _pick(r, "Sort", "sort", "Statut", "statut", "État",
                           "etatAmendement")
            if not uid:
                continue
            title_bits = [f"Amendement n°{uid}"]
            if sort:
                title_bits.append(f"[{sort}]")
            if auteur:
                title_bits.append(f"— {auteur}")
            if groupe:
                title_bits.append(f"({groupe})")
            summary_parts = [f"Auteur : {auteur}" if auteur else "",
                             f"Groupe : {groupe}" if groupe else "",
                             f"Sort : {sort}" if sort else "",
                             obj, disp]
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=" ".join(title_bits)[:220],
                url=(_pick(r, "URL", "url", "lien")
                     or f"https://www.senat.fr/enseance/{uid}.html"),
                published_at=parse_iso(_pick(r, "Date", "date",
                                              "Date de publication JO",
                                              "datePublication")),
                summary=" — ".join(p for p in summary_parts if p)[:2000],
                raw=r,
            )

    elif sid in ("senat_questions", "senat_qg", "senat_questions_1an"):
        # Colonnes réelles (qg / questions_1an) : Numéro, Référence, Titre,
        # Nom, Prénom, Civilité, Circonscription, Groupe, Type Appartenance,
        # Date de publication JO, Ministère de dépôt, Ministère de réponse
        # questions_1an ajoute : Sort, Nature
        for r in rows:
            uid = _pick(r, "Numéro", "Référence", "numQuestion",
                         "numero", "num", "id", "uid")
            titre = _pick(r, "Titre", "titre", "objet", "intitule")
            texte = _pick(r, "Texte", "texte", "texteQuestion", "libelle")
            rubrique = _pick(r, "Rubrique", "Thème", "rubrique", "theme")
            # Auteur reconstitué à partir de civilité/prénom/nom
            civ = _pick(r, "Civilité", "civilite")
            prenom = _pick(r, "Prénom", "prenom")
            nom = _pick(r, "Nom", "nom", "nomAuteur", "senateur", "signataire")
            auteur = " ".join(p for p in [civ, prenom, nom] if p).strip()
            groupe = _pick(r, "Groupe", "groupe")
            ministere = _pick(r, "Ministère de dépôt", "Ministère de réponse",
                               "Ministère", "ministere", "ministereAttributaire",
                               "minInt", "destinataire")
            sort = _pick(r, "Sort", "sort", "statut")
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
            if groupe:
                title_bits.append(f"({groupe})")
            if ministere:
                title_bits.append(f"→ {ministere}")
            if sort and sid == "senat_questions_1an":
                title_bits.append(f"[{sort}]")
            title_bits.append(f": {sujet}")
            summary = " — ".join(p for p in [
                auteur, groupe,
                f"Destinataire : {ministere}" if ministere else "",
                f"Rubrique : {rubrique}" if rubrique else "",
                f"Sort : {sort}" if sort else "",
                texte, titre,
            ] if p)[:2000]
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=" ".join(title_bits)[:220],
                url=(_pick(r, "URL", "url", "lien")
                     or f"https://www.senat.fr/questions/base/{uid}.html"),
                published_at=parse_iso(_pick(r, "Date de publication JO",
                                              "date", "datePublication",
                                              "date_publication")),
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
