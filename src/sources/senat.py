"""Connecteur Sénat — open data (CSV/ZIP) + RSS actualités."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Iterable

import feedparser

from ..models import Item
from ._common import (
    extract_cr_theme,
    fetch_bytes,
    fetch_bytes_heavy,
    fetch_text,
    parse_iso,
    unzip_members,
    unzip_members_since,
)

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


# Mois français pour formater "11 février 2026" sans dépendre de la locale
# système (les CI GitHub Actions n'ont pas forcément fr_FR.UTF-8).
_FR_MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _fmt_fr_date(dt: datetime) -> str:
    """Formate un datetime en "11 février 2026"."""
    return f"{dt.day} {_FR_MONTHS[dt.month - 1]} {dt.year}"


# Noms de fichiers CR Sénat : préfixe (1 lettre) + date AAAAMMJJ + suffixe.
# Exemples observés : d20260211.xml (séance), s20260315_001.html (CRI),
# a20260315.html (analytique). On capture la lettre + la date séparément.
_CR_NAME_RE = re.compile(
    r"^([a-z])(\d{8})(?:[_\-].*)?\.(?:xml|html?|txt)$", re.IGNORECASE
)

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
            # Comptes rendus (debats, cri) : zips massifs avec des milliers
            # de fichiers texte par session. Filtre par date (ZipInfo.date_time)
            # et traitement fichier-par-fichier pour éviter l'OOM.
            if sid in ("senat_debats", "senat_cri"):
                return _fetch_debats_zip(src)
            # ameli.zip + questions.zip + debats.zip sont des dumps lourds
            # (100-300 Mo), retry lourd + timeout read 120s.
            data = fetch_bytes_heavy(src["url"])
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
        if fmt == "akn_index":
            # Flux Akoma Ntoso (depots.xml / adoptions.xml) : route vers
            # le parser dédié. Import local pour ne pas charger le module
            # XML si la source n'est pas configurée.
            from .senat_akn import fetch_akn_index
            return fetch_akn_index(src)
        if fmt == "akn_discussion":
            # Amendements per-texte : itère depots.xml et fetche les CSV
            # `jeu_complet_<session>_<num>.csv` (séance + commission). Le
            # dump bulk ameli.zip est un dump PostgreSQL, non exploitable.
            from .senat_amendements import fetch_source as fetch_amdt_per_texte
            return fetch_amdt_per_texte(src)
    except Exception as e:
        log.exception("Sénat %s KO: %s", sid, e)
    return []


# Nombre de jours à conserver pour les zips de débats/CRI (config via
# env var SENAT_DEBATS_SINCE_DAYS ou src["since_days"], défaut 30).
_DEFAULT_DEBATS_SINCE_DAYS = 30


def _fetch_debats_zip(src: dict) -> list[Item]:
    """Fetch + normalise un zip de comptes rendus Sénat (debats / cri).

    Approche :
    1. Télécharge le zip en mémoire
    2. Itère les entrées via unzip_members_since (filtre par ZipInfo.date_time)
    3. Pour chaque fichier récent, génère un Item (UID = sha1 du nom,
       summary = texte décodé brut, date = date_time de l'entrée)

    Évite de décompresser les milliers de fichiers anciens (le cri.zip
    complet fait 2803 fichiers / plusieurs Go décompressé).
    """
    sid = src["id"]
    cat = src["category"]
    # Fenêtre : config source > env var > défaut 30j
    since_days = int(
        src.get("since_days")
        or os.environ.get("SENAT_DEBATS_SINCE_DAYS")
        or _DEFAULT_DEBATS_SINCE_DAYS
    )
    since = datetime.utcnow() - timedelta(days=since_days)
    log.info(
        "Sénat %s : fetch zip + filtre date >= %s (fenêtre %d jours)",
        sid, since.date().isoformat(), since_days,
    )
    # CRI.zip = 537 Mo, debats.zip = 33 Mo : retry lourd + read 120s.
    data = fetch_bytes_heavy(src["url"])
    items: list[Item] = []
    ext_counts: dict[str, int] = {}

    for name, dt, payload in unzip_members_since(data, since=since):
        ext = os.path.splitext(name)[1].lower().lstrip(".") or "no-ext"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

        # Décode le contenu en texte (cp1252 fréquent côté Sénat)
        text, _enc = _decode_payload(payload)
        # Si c'est du HTML/XML, on déballe les tags pour exposer le texte
        # brut au matcher sans s'embêter avec un parser.
        if ext in ("html", "htm", "xml"):
            text = re.sub(r"<[^>]+>", " ", text)
        # Décode les entités HTML (&#233; → é, &#160; → espace insécable) —
        # sans ça, le snippet affiché sur le site contient du bruit moche
        # comme « Pr&#233;sidence de M.&#160;G&#233;rard Larcher ».
        text = html.unescape(text)
        # Normalise les espaces insécables et autres séparateurs typographiques
        # en espace simple (plus lisible dans l'extrait).
        text = text.replace("\u00a0", " ").replace("\u202f", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue

        # UID déterministe (hash du nom complet dans le zip) — stable
        # d'un run à l'autre, donc la dédoublonnage via store fonctionne.
        uid = hashlib.sha1(f"{sid}:{name}".encode()).hexdigest()[:16]
        base = os.path.basename(name)

        # Extraction de la date réelle de séance depuis le nom de fichier
        # (préfixe lettre + AAAAMMJJ). La date ZipInfo.date_time reflète
        # l'archivage, pas la date de séance — elle peut déplacer tous les
        # CR du mois sur la même journée. Quand le pattern ne matche pas,
        # on retombe sur la date ZipInfo.
        seance_dt = dt
        m_name = _CR_NAME_RE.match(base)
        if m_name:
            try:
                seance_dt = datetime.strptime(m_name.group(2), "%Y%m%d")
            except ValueError:
                pass

        # Titre humain : on veut évoquer le thème du débat quand possible
        # (« Séance du 11 février 2026 — Discussion du projet de loi relatif
        # au sport amateur »). À défaut, on garde la mention analytique/intégral.
        # report_type expose la distinction aux templates dédiés sans avoir
        # à parser le titre.
        report_type = "analytique" if sid == "senat_debats" else "integral"
        label = (
            "Compte rendu analytique"
            if report_type == "analytique"
            else "Compte rendu intégral"
        )
        theme = extract_cr_theme(text)
        date_label = _fmt_fr_date(seance_dt) if (m_name and seance_dt.year > 2000) else ""
        if date_label and theme:
            title = f"Séance du {date_label} — {theme}"[:220]
        elif date_label:
            title = f"Séance du {date_label} — {label}"[:220]
        elif theme:
            title = f"{label} — {theme}"[:220]
        else:
            # Fallback : ancien format (nom de fichier non reconnu)
            title = f"{label} — {base}"[:220]

        # URL du sommaire de la séance (validé en live avril 2026) :
        # https://www.senat.fr/seances/sAAAAMM/sAAAAMMJJ/ → listing HTML
        # Plus précis que l'URL mensuelle précédente qui renvoyait un 403.
        if seance_dt.year > 2000:
            url = (
                f"https://www.senat.fr/seances/s{seance_dt:%Y%m}"
                f"/s{seance_dt:%Y%m%d}/"
            )
        else:
            url = "https://www.senat.fr/seances/"

        summary = text[:2000]

        items.append(Item(
            source_id=sid,
            uid=uid,
            category=cat,
            chamber="Senat",
            title=title,
            url=url,
            published_at=seance_dt,
            summary=summary,
            raw={
                "path": f"senat:{sid}",
                "zip_member": name,
                "size": len(payload),
                # Exposés au template comptes_rendus/list.html pour rendre
                # le type ("analytique" vs "intégral") sous forme de badge
                # et offrir un regroupement / tri visuel.
                "report_type": report_type,
                "report_label": label,
                "seance_date_iso": (
                    seance_dt.date().isoformat() if seance_dt.year > 2000 else ""
                ),
                # Thème extrait du corps du CR (utile pour diagnostic + pour
                # enrichir le titre côté export si celui en base est vieux).
                "theme": theme,
            },
        ))

    log.info(
        "Sénat %s : %d items produits (extensions : %s)",
        sid, len(items),
        ", ".join(f"{k}={v}" for k, v in sorted(ext_counts.items())) or "aucun",
    )
    return items


_NORM_RE = re.compile(r"[\s_\-\.]+")


def _parse_date_any(s: str | None) -> datetime | None:
    """Parse une date ISO (YYYY-MM-DD) ou française (DD/MM/YYYY).

    Les CSV du Sénat (dosleg, ppl, promulguees…) mélangent les deux
    formats selon les fichiers. parse_iso seul rejette DD/MM/YYYY et
    laissait beaucoup de lignes sans date (les dossiers apparaissaient
    alors sans date sur le site).

    Comme `_common.parse_iso` (R11f), normalise en naïf UTC pour rester
    comparable aux autres `published_at` du pipeline.
    """
    from datetime import timezone as _tz
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    # ISO 8601 (avec ou sans T)
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(_tz.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass
    # DD/MM/YYYY ou D/M/YYYY
    m = re.match(r"^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})$", s)
    if m:
        d, mo, y = m.groups()
        y = int(y)
        if y < 100:
            y += 2000
        try:
            return datetime(int(y), int(mo), int(d))
        except ValueError:
            return None
    # DD mois YYYY (ex : "18 février 2026")
    parts = s.lower().split()
    if len(parts) == 3 and parts[1] in _FR_MONTHS:
        try:
            return datetime(int(parts[2]), _FR_MONTHS.index(parts[1]) + 1,
                            int(parts[0]))
        except (ValueError, IndexError):
            return None
    return None


def _cap_first(text: str) -> str:
    """Met en majuscule la 1re lettre, préserve le reste (sigles : SNCF, CNIL).
    Aligné sur senat_akn._fetch_akn_index (ligne 391) — cohérence dossiers
    législatifs entre sources CSV et AKN."""
    if not text:
        return text
    return text[0].upper() + text[1:]


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
            # Date : CSV Sénat historiquement en DD/MM/YYYY. _parse_date_any
            # couvre ISO + DD/MM/YYYY + "DD mois YYYY". Sans ça, tous les
            # dossiers issus des CSV arrivaient sans date sur le site.
            date = _parse_date_any(_pick(
                r, "Date de dépôt", "Date de promulgation",
                "Date initiale", "Date de la décision",
                "date_depot", "dateDepot",
                "datePromulgation", "datePublication",
                "date_publication", "date",
            ))
            if not uid or not titre:
                continue
            url = (_pick(r, "URL du dossier", "url", "lien")
                   or f"https://www.senat.fr/dossier-legislatif/{uid}.html")
            # Titre : CSV Sénat remontent souvent le libellé en minuscule
            # ("projet de loi relatif à…"). On aligne la capitalisation sur
            # senat_akn pour cohérence visuelle des dossiers législatifs.
            titre_disp = _cap_first(titre)
            # Contenu utile pour matching : on ajoute tout le texte du row
            extras = " ".join(v for v in r.values() if isinstance(v, str) and len(v) > 3)
            # Si la source est "promulguees", on marque le flag pour que le
            # badge passe en vert côté template.
            raw = dict(r)
            if sid == "senat_promulguees":
                raw["is_promulgated"] = True
                raw.setdefault("status_label", "Promulguée")
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=titre_disp[:220], url=url,
                published_at=date,
                summary=(titre_disp + " — " + extras)[:2000],
                raw=raw,
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

    # NB : senat_debats / senat_cri ne passent PAS par _normalize_rows.
    # Leurs zips ne contiennent pas de CSV — ce sont des milliers de
    # fichiers texte/HTML/XML par session. Voir _fetch_debats_zip().


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
