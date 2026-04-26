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
            return _normalize_rss(src, fetch_bytes(src["url"]))
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
        if fmt == "senat_agenda_daily":
            # R15 (2026-04-22) : scraper dédié qui itère sur une fenêtre
            # de dates et récupère les pages quotidiennes de l'agenda
            # Sénat (`/agenda/<Section>/agl{DDMMYYYY}.html`).
            # NB : le serveur bloque actuellement ces paths en sandbox
            # (404 + "Accès restreint") — cf. audit R15 §1.2. Le handler
            # loggue l'échec et retourne 0 item plutôt que de crasher,
            # pour qu'on puisse déployer sans bloquer le reste du pipeline.
            return _fetch_agenda_daily(src)
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
                # R40-G (2026-04-26) : haystack 200k chars consommé par
                # KeywordMatcher.apply pour scanner le contenu complet
                # de la séance plénière (pas juste summary[:2000]). Pour
                # une séance plénière qui couvre 5-10 sujets sur 200-400k
                # chars, le bloc sport peut tomber à la position 200k —
                # hors fenêtre 2000 chars → CR ignoré à tort. Aligné sur
                # le budget des CR commissions (an/senat_cr_commissions).
                "haystack_body": text[:200000],
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
            # R18+ (2026-04-22) : identifiant canonique Sénat (ex. pjl24-630).
            # Permet à site_export._dedup (passe 2c) de fusionner les CSV
            # avec leurs contreparties senat_akn ou AN. Pas de url_an côté
            # CSV (absent des colonnes) — le mapping AN se fera via
            # senat_akn qui a l'alias url-AN dans le FRBR.
            raw["dossier_id"] = str(uid)
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
            # R23-D (2026-04-23) : le CSV `senat_questions_1an` liste les
            # questions écrites sans réponse depuis +1 an, mais Cyril constate
            # que la date de dépôt affichée est souvent très récente (re-dépôt
            # automatique côté Sénat ?). Du coup préfixer le titre par
            # "Question de +1 an sans réponse" était trompeur. On retombe sur
            # le libellé neutre "Question écrite" ; le sid distinct reste utile
            # en interne (dedup, compteurs digest) mais n'apparaît plus dans
            # le titre affiché.
            # R25b-C (2026-04-23) : le CSV `senat_questions_1an` liste des
            # questions de +1 an sans réponse TOUTES NATURES confondues. Il
            # mélange donc QE (question écrite), QOSD (question orale sans
            # débat) et QG (question au gouvernement rétroactive). L'ancien
            # mappage figé `senat_questions_1an → "Question écrite"` classait
            # à tort des questions orales (ex. 1054S : Nature='QOSD'). On lit
            # désormais la colonne `Nature` du CSV en priorité ; fallback sur
            # le label associé au sid pour les sources qui n'ont pas `Nature`.
            nature_csv = (_pick(r, "Nature", "nature") or "").strip().upper()
            _NATURE_LABELS = {
                "QE": "Question écrite",
                "QOSD": "Question orale sans débat",
                "QG": "Question au gouvernement",
                "QO": "Question orale",
            }
            sid_label = {
                "senat_questions": "Question écrite",
                "senat_qg": "Question au gouvernement",
                "senat_questions_1an": "Question écrite",
            }.get(sid, "Question")
            qtype_label = _NATURE_LABELS.get(nature_csv, sid_label)
            # R25b-B (2026-04-23) : retrait de « n°{uid} » du titre pour
            # harmoniser avec l'AN (`Question écrite : sujet`). Le numéro
            # reste disponible pour dédup via `raw.Numéro` et reste affiché
            # en badge côté template si besoin. Format final :
            # "Question <nature> : sujet".
            title_bits = [qtype_label]
            title_bits.append(f": {sujet}")
            # R39-L (2026-04-25) — summary ré-ordonné : corps de la question
            # EN PREMIER (`texte`), puis sujet/titre, puis métadonnées en
            # queue. Avant R39-L, l'ordre était `auteur — groupe —
            # Destinataire — Rubrique — Sort — texte — titre` ce qui
            # produisait des snippets redondants côté UI : le snippet
            # affichait `M. Hervé Maurey — UC — Destinataire : Sports —
            # Sort : En cours — …` alors que toutes ces infos figurent
            # déjà sur la card (chip groupe, badge chambre, sous-titre).
            # Côté matcher, il scanne title + summary + raw, donc les
            # métadonnées restent atteignables même reléguées en queue.
            # Quand le keyword n'est que dans le titre (texte vide côté
            # CSV Sénat), le summary tombe sur titre/sujet — déjà visible
            # mais sans les méta polluantes.
            summary = " — ".join(p for p in [
                texte, sujet, titre,
                # Métadonnées en queue (utiles seulement au matcher).
                auteur, groupe,
                f"Destinataire : {ministere}" if ministere else "",
                f"Rubrique : {rubrique}" if rubrique else "",
                f"Sort : {sort}" if sort else "",
            ] if p)[:2000]
            # R22i (2026-04-23) : la colonne CSV Sénat s'appelle exactement
            # `URL Question` et livre du `http://…/base/YYYY/qSEQYYMM<num>.html`.
            # Avant R22i, `_pick(r, "URL", "url", "lien")` ne matchait pas et on
            # tombait sur le fallback `.../base/{uid}.html` qui renvoie un 404
            # côté senat.fr (pattern inexistant). On lit donc la bonne colonne
            # en priorité et on force https://. Le fallback historique est
            # conservé pour ceintures-bretelles mais ne devrait plus servir.
            url_csv = _pick(r, "URL Question", "URL", "url", "lien") or ""
            if url_csv.startswith("http://"):
                url_csv = "https://" + url_csv[len("http://"):]
            # R23-D2 (2026-04-23) : clé stable `texte_question` exposée côté
            # site_export pour construire le snippet depuis le corps réel
            # (et non depuis `summary` qui préfixe Destinataire/Rubrique/Sort
            # et écrasait souvent l'extrait affiché). Le CSV Sénat livre le
            # corps dans la colonne "Texte".
            if texte:
                r["texte_question"] = texte
            # R38-G (2026-04-24) / R39-D (2026-04-25) : clés minuscules
            # `auteur` / `groupe` exposées au frontmatter Hugo. Le code
            # `site_export._write_item_pages` lit `raw["auteur"]` et
            # `raw["groupe"]` (sans préfixe) pour construire les variables
            # frontmatter `auteur:` et `auteur_groupe:`. On harmonise donc
            # sur les MÊMES noms de clé que ceux posés par les parseurs AN
            # (assemblee.py:_normalize_amendement / _normalize_question).
            # R38-G initial avait posé `auteur_groupe` au lieu de `groupe`,
            # ce qui faisait que la chip de groupe (DEM, SOC, UC, SER…)
            # s'affichait côté AN mais pas côté Sénat — cf. capture Cyril
            # du 2026-04-25 sur /items/questions/.
            if auteur:
                r["auteur"] = auteur
            if groupe:
                r["groupe"] = groupe
            yield Item(
                source_id=sid, uid=str(uid), category=cat, chamber="Senat",
                title=" ".join(title_bits)[:220],
                url=(url_csv
                     or f"https://www.senat.fr/questions/base/{uid}.html"),
                published_at=parse_iso(_pick(r, "Date de publication JO",
                                              "date", "datePublication",
                                              "date_publication")),
                summary=summary, raw=r,
            )

    # NB : senat_debats / senat_cri ne passent PAS par _normalize_rows.
    # Leurs zips ne contiennent pas de CSV — ce sont des milliers de
    # fichiers texte/HTML/XML par session. Voir _fetch_debats_zip().


def _normalize_rss(src, text) -> list[Item]:
    """R19-A (2026-04-23) : accepte bytes OU str. feedparser.parse(bytes)
    sait lire la PI XML `<?xml encoding="ISO-8859-15"?>` (flux thème Sénat)
    et décode correctement. En str, le décodage a déjà eu lieu en amont
    et on perd l'info encoding (d'où les 'nï¿œ 733' observés avant R19-A).

    R19-B (2026-04-23) : pour `category=dossiers_legislatifs`, on filtre
    les URLs pour ne garder que les textes INITIAUX :
      - `/leg/pjl*` (projets de loi) et `/leg/ppl*` (propositions de loi).
    Les `tas` (textes adoptés — étapes), `rap` (rapports), `a` (avis) et
    `notice-rapport` sont des pièces *dans* un dossier, pas un dossier.
    Cyril voit 8+ lignes pour la loi JOP Alpes 2030 alors qu'une seule
    suffit (la première occurrence). Filtrage strict côté scrape.
    """
    d = feedparser.parse(text)
    out = []
    is_dosleg = src.get("category") == "dossiers_legislatifs"
    # Regex URL : on matche `/leg/pjlXX-YYY.html` et `/leg/ppljXX-YYY.html`.
    _INITIAL_TEXT_RE = re.compile(r"/leg/(pjl|ppl)[a-z]*\d", re.IGNORECASE)
    for e in d.entries:
        uid = getattr(e, "id", None) or getattr(e, "link", "")
        if not uid:
            continue
        link = getattr(e, "link", "") or ""
        if is_dosleg and link and not _INITIAL_TEXT_RE.search(link):
            log.debug("senat_rss: skip non-initial %s", link)
            continue
        dt = None
        if getattr(e, "published_parsed", None):
            dt = datetime(*e.published_parsed[:6])
        out.append(Item(
            source_id=src["id"], uid=uid, category=src["category"], chamber="Senat",
            title=(getattr(e, "title", "") or "")[:220],
            url=link,
            published_at=dt,
            summary=(getattr(e, "summary", "") or "")[:500],
            raw={},
        ))
    return out


# -----------------------------------------------------------------------------
# R15 (2026-04-22) — Scraper Agenda Sénat quotidien
# -----------------------------------------------------------------------------
#
# L'index `https://www.senat.fr/agenda` est une SPA AngularJS qui charge :
#   - `cal.json` (index jours avec items par section)
#   - `{Section}/agl{DDMMYYYY}.html` (page par section et par jour)
#   - `Global/agl{DDMMYYYY}Print.html` (vue imprimable tout-en-un)
#
# Sections observées dans le menu SPA :
#   Seance, Commissions, Missions, Delegation, Senat (bureau +
#   conférence des Présidents), GroupesPolitiques, Divers, President,
#   International, Delai
#
# Approche du handler :
#   1. Itère une fenêtre [J - before_days, J + after_days].
#   2. Pour chaque jour, tente de récupérer la page "print" globale
#      (1 requête/jour au lieu de 1/section).
#   3. Parse le HTML → extrait les blocs d'évènements (titre, heure, lieu,
#      organe) et produit un Item par évènement avec published_at = jour.
#
# Contrainte serveur actuelle (avril 2026) : le Sénat renvoie 404 +
# "Accès restreint" sur ces sub-paths depuis les IP non reconnues.
# Le handler ne crashe pas — il loggue en warning et retourne une liste
# vide. Une fois le blocage levé (test en CI ou Chrome MCP local), les
# items seront émis sans autre changement.
# -----------------------------------------------------------------------------

# Sections agenda Sénat par ordre de priorité métier pour le sport.
# `Commissions` + `Missions` = cœur de cible (sport-santé, dopage, etc.).
# `Delegation` = délégations thématiques (dont délégation droits des
# femmes, parfois sport). `Seance` = séance publique plénière.
_SENAT_AGENDA_SECTIONS = (
    "Seance",
    "Commissions",
    "Missions",
    "Delegation",
    "Senat",
    "GroupesPolitiques",
    "Divers",
)


def _senat_agenda_url(section: str, dt: datetime, *, printable: bool = False) -> str:
    """Construit l'URL d'une page d'agenda quotidienne Sénat.

    Format `DDMMYYYY` (ex. `22042026` pour le 22 avril 2026).
    Si `printable=True`, retourne la variante `/Global/agl{date}Print.html`
    qui agrège toutes les sections en une page.
    """
    stamp = dt.strftime("%d%m%Y")
    if printable:
        return f"https://www.senat.fr/agenda/Global/agl{stamp}Print.html"
    return f"https://www.senat.fr/agenda/{section}/agl{stamp}.html"


# Bloc contenu principal de la page quotidienne Sénat. Le HTML encapsule
# la liste d'évènements dans `<div id="content">…</div>`.
_SENAT_CONTENT_RE = re.compile(
    r'<div[^>]+id=["\']content["\'][^>]*>(?P<body>.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)

# Événement unitaire : on accepte toute section `<section class="event">`
# ou `<div class="event-item">` (deux variantes observées sur miroirs).
_SENAT_EVENT_RE = re.compile(
    r'<(?:section|div|article)[^>]+class=["\'][^"\']*(?:event|reunion|seance)[^"\']*["\'][^>]*>'
    r'(?P<body>.*?)'
    r'</(?:section|div|article)>',
    re.DOTALL | re.IGNORECASE,
)

# Titre événement : <h[2-4]>…</h[2-4]> ou <a class="titre">…</a>
_SENAT_EVENT_TITLE_RE = re.compile(
    r'<(?:h[234]|a[^>]+class=["\'][^"\']*titre[^"\']*["\'])[^>]*>(?P<t>.*?)</(?:h[234]|a)>',
    re.DOTALL | re.IGNORECASE,
)

# Horaire dans un libellé : "14h30", "14:30", "de 14h à 16h".
_SENAT_TIME_RE = re.compile(
    r'(?P<h>\d{1,2})\s*[h:]\s*(?P<m>\d{0,2})'
)

# Lieu — on cherche les balises `class="lieu"`, `<address>`, `<em>Salle…`.
_SENAT_LIEU_RE = re.compile(
    r'<(?:[a-z]+)[^>]+class=["\'][^"\']*lieu[^"\']*["\'][^>]*>(?P<l>.*?)</',
    re.DOTALL | re.IGNORECASE,
)

# R32 (2026-04-24) : délégué à `src.textclean.strip_html` (audit §4.2).
# Wrapper conservé pour compat (imports locaux par `_parse_senat_event_block`).
from .. import textclean as _textclean  # noqa: E402


def _strip_html(text: str) -> str:
    """Alias de `textclean.strip_html` (R32)."""
    return _textclean.strip_html(text)


def _parse_senat_event_block(body: str, day: datetime, section: str) -> dict | None:
    """Extrait {title, lieu, heure, summary} d'un bloc `<section class="event">`.

    Tolérant aux variations : si le titre est absent ou trop court,
    on retourne None (bloc inutilisable, ex. bandeau de navigation
    capturé par erreur).
    """
    title_m = _SENAT_EVENT_TITLE_RE.search(body)
    title = _strip_html(title_m.group("t")) if title_m else ""
    if len(title) < 5:
        return None

    # Heure : on cherche dans le texte dépouillé pour éviter les faux positifs
    # dans les attributs HTML.
    text = _strip_html(body)
    time_m = _SENAT_TIME_RE.search(text)
    time_str = ""
    event_dt = day
    if time_m:
        try:
            h = int(time_m.group("h"))
            mm = int(time_m.group("m") or "0")
            if 0 <= h <= 23 and 0 <= mm <= 59:
                time_str = f"{h:02d}h{mm:02d}"
                event_dt = day.replace(hour=h, minute=mm)
        except ValueError:
            pass

    lieu_m = _SENAT_LIEU_RE.search(body)
    lieu = _strip_html(lieu_m.group("l")) if lieu_m else ""

    summary_parts = []
    if time_str:
        summary_parts.append(time_str)
    if lieu:
        summary_parts.append(f"Lieu : {lieu}")
    summary_parts.append(text[:800])
    summary = " — ".join(summary_parts)

    return {
        "title": title[:220],
        "lieu": lieu,
        "heure": time_str,
        "section": section,
        "summary": summary[:2000],
        "event_dt": event_dt,
    }


def _parse_senat_agenda_page(html_body: str, day: datetime, section: str) -> list[dict]:
    """Parse une page quotidienne Sénat et renvoie les events extraits."""
    m = _SENAT_CONTENT_RE.search(html_body)
    if not m:
        return []
    content = m.group("body")
    events = []
    for evt_m in _SENAT_EVENT_RE.finditer(content):
        parsed = _parse_senat_event_block(evt_m.group("body"), day, section)
        if parsed:
            events.append(parsed)
    return events


def _iter_date_window(before_days: int, after_days: int):
    """Yield les datetime jour par jour sur la fenêtre glissante."""
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for delta in range(-before_days, after_days + 1):
        yield base + timedelta(days=delta)


def _fetch_agenda_daily(src: dict) -> list[Item]:
    """Itère la fenêtre de dates et scrape l'agenda Sénat jour par jour.

    Paramètres YAML supportés (cf. `sources.yml`) :
        before_days: 7     # J-7 par défaut
        after_days:  30    # J+30 par défaut
        sections: [...]    # filtre de sections, par défaut toutes
        printable: true    # utilise la vue `Global/agl*Print.html`
                           # (1 requête/jour au lieu de 1/section)

    En cas d'échec HTTP (404/403/timeout), on continue sur les autres
    jours : on préfère une fenêtre partielle plutôt que rien. Le handler
    loggue le nombre de fetches réussis vs échoués pour faciliter le
    diagnostic en CI.
    """
    sid = src["id"]
    cat = src.get("category", "agenda")
    before_days = int(src.get("before_days", 7))
    after_days = int(src.get("after_days", 30))
    sections = tuple(src.get("sections") or _SENAT_AGENDA_SECTIONS)
    printable = bool(src.get("printable", True))

    items: list[Item] = []
    ok, ko = 0, 0

    for day in _iter_date_window(before_days, after_days):
        day_iso = day.date().isoformat()
        if printable:
            urls = [(None, _senat_agenda_url("", day, printable=True))]
        else:
            urls = [(s, _senat_agenda_url(s, day)) for s in sections]

        for section, url in urls:
            try:
                body = fetch_text(url)
            except Exception as e:
                ko += 1
                log.debug("Sénat agenda %s — fetch KO %s : %s", sid, url, e)
                continue
            # Détection page "Accès restreint" (retournée en HTTP 404
            # avec 101Ko de template TYPO3) — on traite comme un KO
            # silencieux sans polluer les stats.
            if "Accès restreint" in body or "Accès non autorisé" in body:
                ko += 1
                log.debug("Sénat agenda %s — accès restreint %s", sid, url)
                continue
            events = _parse_senat_agenda_page(
                body, day, section or "Global",
            )
            ok += 1 if events else 0
            for idx, ev in enumerate(events):
                uid = hashlib.sha1(
                    f"{sid}:{day_iso}:{ev['section']}:{idx}:{ev['title']}"
                    .encode("utf-8")
                ).hexdigest()[:16]
                items.append(Item(
                    source_id=sid,
                    uid=uid,
                    category=cat,
                    chamber="Senat",
                    title=ev["title"],
                    url=url,
                    published_at=ev["event_dt"],
                    summary=ev["summary"],
                    raw={
                        "path": "senat:agenda_daily",
                        "section": ev["section"],
                        "lieu": ev["lieu"],
                        "heure": ev["heure"],
                        "day": day_iso,
                    },
                ))

    log.info(
        "Sénat %s : %d items (fenêtre %dj + %dj, %d pages ok, %d échecs)",
        sid, len(items), before_days, after_days, ok, ko,
    )
    return items
