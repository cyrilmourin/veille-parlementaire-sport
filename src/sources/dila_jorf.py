"""Connecteur JORF via le dump XML DILA OPENDATA — pas de credentials.

Source : https://echanges.dila.gouv.fr/OPENDATA/JORF/
Format : fichiers `JORF_YYYYMMDD-HHMMSS.tar.gz` (1 à 2 éditions par jour,
matin et soir) contenant les XML LEGIPUBLI.

Avantage sur l'API PISTE : aucune authentification, flux stable, données identiques.

On télécharge les N dernières éditions (param `days_back` dans sources.yml,
sémantiquement = nombre d'éditions car le flux peut publier 2 fois/jour),
on extrait les XML à la volée, on ne retient que les natures pertinentes :
ARRETE, DECRET, DECISION, LOI, ORDONNANCE. Les arrêtés de nomination sont
reclassés dans la catégorie "nominations".
"""
from __future__ import annotations

import io
import logging
import re
import tarfile
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from lxml import etree

from ..models import Item
from ._common import fetch_bytes, fetch_text, parse_iso

log = logging.getLogger(__name__)

BASE_INDEX = "https://echanges.dila.gouv.fr/OPENDATA/JORF/"
# Éditions quotidiennes : JORF_YYYYMMDD-HHMMSS.tar.gz (parfois 2/jour)
# On capture séparément date + heure pour pouvoir trier précisément — sinon
# deux éditions du même jour apparaissent indistinctement.
_FILE_PAT = re.compile(
    r"^JORF_(?P<date>\d{8})-(?P<time>\d{6})\.tar\.gz$", re.IGNORECASE
)

# Natures que l'on garde (les plus fréquentes dans la veille sport)
KEEP_NATURES = {"ARRETE", "DECRET", "DECISION", "LOI", "ORDONNANCE"}


def _list_recent_dumps(n: int = 8) -> list[tuple[str, datetime]]:
    """Parse l'index Apache et renvoie [(url, datetime)…] triés du plus
    récent au plus ancien.

    On intègre l'heure pour distinguer les deux éditions quotidiennes
    éventuelles (matin ~00:30 UTC, soir ~20:00 UTC).
    """
    try:
        html = fetch_text(BASE_INDEX)
    except Exception as e:
        log.error("DILA index KO: %s", e)
        return []
    soup = BeautifulSoup(html, "html.parser")
    entries: list[tuple[str, datetime]] = []
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        m = _FILE_PAT.match(href)
        if not m:
            continue
        try:
            dt = datetime.strptime(
                m.group("date") + m.group("time"), "%Y%m%d%H%M%S"
            )
        except ValueError:
            continue
        entries.append((urljoin(BASE_INDEX, href), dt))
    entries.sort(key=lambda x: x[1], reverse=True)
    if not entries:
        log.warning(
            "DILA JORF : aucune entrée ne matche _FILE_PAT dans l'index "
            "(index HTML de %d chars). Vérifie le format des noms de fichier.",
            len(html or ""),
        )
    return entries[:n]


def _ns_strip(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text(el) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _find(root, *paths):
    """Cherche le premier élément matchant l'un des chemins XPath locaux."""
    for p in paths:
        res = root.find(p)
        if res is not None and (res.text or len(res) > 0):
            return res
    return None


def _collect_inner_text(el, max_len: int = 6000) -> str:
    """Concatène le texte de tous les descendants d'un nœud (ignore tags,
    attributs, commentaires) en respectant l'ordre. Utilisé pour aplatir
    <NOTICE>, <TEXTE>, <VISAS>, <ARTICLE>… en texte brut.

    `max_len` coupe de façon défensive quand le corps d'un arrêté est
    très long (cas des conventions collectives étendues qui pèsent
    plusieurs centaines de ko). La fenêtre 6000c suffit au haystack
    (3000c) et au summary (400c) sans mobiliser de mémoire inutile.
    """
    if el is None:
        return ""
    try:
        # `itertext()` de lxml renvoie tous les .text + .tail dans l'ordre
        parts: list[str] = []
        total = 0
        for t in el.itertext():
            if not t:
                continue
            s = t.strip()
            if not s:
                continue
            parts.append(s)
            total += len(s) + 1
            if total >= max_len:
                break
        # Normalise les blancs (retours chariot XML + espaces multiples).
        raw = " ".join(parts)
        return re.sub(r"\s+", " ", raw).strip()
    except Exception:  # pragma: no cover — défensif
        return ""


def _parse_texte_version(xml_bytes: bytes) -> dict | None:
    """Extrait les champs utiles d'un fichier TEXTE_VERSION_xxx.xml.

    R26 (2026-04-23) : extraction additionnelle de la NOTICE (résumé officiel
    DILA de l'acte, 1-3 phrases quand présent) et d'un haystack du corps
    (visas + articles aplati en texte brut, max 3000c). Ces deux champs sont
    remontés dans `raw` pour alimenter respectivement le `summary` (NOTICE
    prioritaire, fallback début du corps) et la recherche keywords
    (`matcher.apply(haystack_extra=…)`). Permet de capter les arrêtés où
    le mot « sport » n'apparaît que dans le corps (ex. nomination au
    cabinet du MinSports dont le titre est générique « portant
    nomination de Mme X »).
    """
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None

    # Commun
    id_text = _text(_find(root, ".//ID", ".//META_COMMUN/ID"))
    nature = _text(_find(root, ".//NATURE", ".//META_COMMUN/NATURE")).upper()
    if nature and nature not in KEEP_NATURES:
        return None

    titre = _text(_find(root, ".//TITREFULL", ".//TITRE"))
    date_publi = _text(_find(root, ".//DATE_PUBLI"))
    date_sign = _text(_find(root, ".//DATE_SIGNATURE"))
    if not id_text or not titre:
        return None

    # R26 — NOTICE : résumé DILA officiel (champ META_SPEC/META_TEXTE_VERSION/
    # NOTICE dans les schemas récents, parfois META_COMMUN/NOTICE sur les
    # textes anciens). Texte brut avec balises inline (<it>, <sup>…) : on
    # aplatit via itertext().
    notice_el = _find(root, ".//NOTICE", ".//META_TEXTE_VERSION/NOTICE")
    notice = _collect_inner_text(notice_el, max_len=1200)

    # R26 — corps du texte pour haystack keywords + fallback summary.
    # Le conteneur <TEXTE> regroupe <VISAS> + <ARTICLE>+ (+ parfois <NOTA>,
    # <SIGNATAIRES>). itertext() préserve l'ordre visuel. On cape à 3000c
    # pour la recherche keywords (suffisant pour les visas ministériels
    # et le premier article) et on reserve 400c pour le summary fallback.
    texte_el = _find(root, ".//TEXTE", ".//CORPS")
    body_full = _collect_inner_text(texte_el, max_len=6000)
    body_head = body_full[:3000]

    # URL Legifrance publique
    url = f"https://www.legifrance.gouv.fr/jorf/id/{id_text}"
    return {
        "id": id_text,
        "nature": nature or "ARRETE",
        "title": titre,
        "url": url,
        "date": parse_iso(date_publi) or parse_iso(date_sign),
        "notice": notice,
        "body_head": body_head,
    }


def _iter_texte_versions(tarball_bytes: bytes):
    """Itère sur les octets des fichiers TEXTE_VERSION_*.xml dans un .taz."""
    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tf:
        for member in tf:
            if not member.isfile():
                continue
            name = member.name.lower()
            if "/texte/version/" not in name and "texte_version" not in name:
                # Tolère différentes arborescences selon les années
                if not name.endswith(".xml"):
                    continue
            f = tf.extractfile(member)
            if f is None:
                continue
            data = f.read()
            if b"<TEXTE_VERSION" in data:
                yield data


def _index_articles_by_cid(tarball_bytes: bytes) -> dict[str, str]:
    """R35-A (2026-04-24) — indexe le corps de chaque JORFARTI*.xml par son
    cid parent (JORFTEXT...).

    Contexte : sur le dump DILA JORF, le fichier `.../texte/version/.../
    JORFTEXT*.xml` contient les métadonnées (titre, nature, dates, ministère)
    mais pas le corps du texte. Le corps réel — visas, articles numérotés,
    listes de nominations — est éclaté dans des fichiers `.../article/.../
    JORFARTI*.xml` distincts. Le rattachement au texte parent se fait via
    `<CONTEXTE><TEXTE cid="JORFTEXT..."/>` à l'intérieur de chaque article.

    R26 cherchait `<TEXTE>` / `<CORPS>` dans le TEXTE_VERSION → presque
    toujours vide (tous les `<CONTENU/>` des `<NOTICE>`, `<VISAS>`, `<SM>`,
    etc. sont vides dans TEXTE_VERSION). Conséquence : `haystack_body=""`
    pour 100 % des décrets → le matcher ne voyait que le titre. Ex. du
    cas remonté par Cyril : JORFTEXT000053930076 « Décret du 22 avril 2026
    portant promotion et nomination… Légion d'honneur » dont le corps
    énumère Fillon-Maillet, Jeanmonnot, Perrot… en biathlon, ski, Jeux
    Olympiques de Milan-Cortina — aucun mot « sport » dans le titre mais
    dense dans l'article 1 (JORFARTI000053930077).

    On concatène tous les articles d'un même cid parent (la liste peut
    contenir plusieurs articles pour les décrets longs) et on cape à
    8000 c par texte (le matcher lit `body_head[:3000]` mais on réserve
    du rab pour un éventuel fallback summary).
    """
    by_cid: dict[str, list[str]] = {}
    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tf:
        for member in tf:
            if not member.isfile():
                continue
            name = member.name.lower()
            if "/article/" not in name or not name.endswith(".xml"):
                continue
            f = tf.extractfile(member)
            if f is None:
                continue
            data = f.read()
            if b"<ARTICLE" not in data:
                continue
            try:
                root = etree.fromstring(data)
            except etree.XMLSyntaxError:
                continue
            # Rattachement au texte parent : <CONTEXTE><TEXTE cid="…"/>
            texte_el = root.find(".//CONTEXTE/TEXTE")
            cid = (texte_el.get("cid") if texte_el is not None else "") or ""
            if not cid:
                continue
            # Corps : <BLOC_TEXTUEL><CONTENU> — contient des <p>, <br/> et
            # parfois des listes <ul>/<li>. itertext() aplatit en texte brut.
            bloc = root.find(".//BLOC_TEXTUEL/CONTENU")
            body_txt = _collect_inner_text(bloc, max_len=8000)
            if not body_txt:
                continue
            by_cid.setdefault(cid, []).append(body_txt)
    # Concatène par cid et cape à 8000 c
    out: dict[str, str] = {}
    for cid, parts in by_cid.items():
        merged = " ".join(parts)
        out[cid] = merged[:8000]
    return out


def fetch_source(src: dict) -> list[Item]:
    days_back = int(src.get("days_back", 8))
    dumps = _list_recent_dumps(n=days_back)
    if not dumps:
        log.warning("DILA JORF : aucun dump récent trouvé")
        return []

    out: list[Item] = []
    seen: set[str] = set()
    for url, dt in dumps:
        try:
            raw = fetch_bytes(url)
        except Exception as e:
            log.warning("DILA %s KO: %s", url, e)
            continue

        # R35-A : index (cid → body concaténé) des articles du tarball. On
        # le construit une fois par dump pour éviter 2 passes tarball.
        articles_by_cid = _index_articles_by_cid(raw)

        for xml_bytes in _iter_texte_versions(raw):
            info = _parse_texte_version(xml_bytes)
            if not info:
                continue
            if info["id"] in seen:
                continue
            seen.add(info["id"])

            # R35-A : si le corps n'a pas été trouvé dans TEXTE_VERSION
            # (cas ultra-majoritaire : le TEXTE_VERSION ne contient que
            # des <CONTENU/> vides), on prend le corps des fichiers
            # ARTICLE rattachés via le cid.
            if not info.get("body_head"):
                article_body = articles_by_cid.get(info["id"], "")
                if article_body:
                    info["body_head"] = article_body[:3000]

            # Catégorisation : nomination si le titre OU le corps le suggère.
            # On élargit le pattern pour capter aussi : "portant nomination",
            # "fin de fonctions", "renouvellement du mandat", "désignation",
            # formulations courantes dans les décrets JORF sport.
            # R36-L (2026-04-24) : le corps du texte (body_head) est aussi
            # scruté, parce que beaucoup d'arrêtés portant nomination ont un
            # titre générique "Arrêté du <date> fixant…" et ne disent
            # explicitement "M. X est chargé des fonctions de …" que dans le
            # corps. Cas concret : arrêté CREPS qui charge une personne des
            # fonctions de directeur — titre neutre, corps explicite.
            title_low = info["title"].lower()
            body_head_low = (info.get("body_head") or "").lower()
            cat = src["category"]
            _NOM_HINTS = (
                "nomination", "nommé", "nommée",
                "désigné", "désignée", "désignation",
                "cessation de fonctions", "fin de fonctions",
                "renouvellement du mandat", "renouvellement de mandat",
                # R36-L : formulations "est chargé(e) des fonctions de" /
                # "est chargé(e) de la direction" typiques des arrêtés
                # nominations de directeurs d'établissements publics sport
                # (CREPS, INSEP, ENVSN…). Masculin et féminin.
                "est chargé des fonctions",
                "est chargée des fonctions",
                "est chargé de la direction",
                "est chargée de la direction",
            )
            if any(h in title_low for h in _NOM_HINTS) or any(
                h in body_head_low for h in _NOM_HINTS
            ):
                cat = "nominations"

            # R26 — summary : NOTICE DILA si présente (résumé officiel,
            # 1-3 phrases), sinon premier segment du corps (visas + début
            # article 1er), fallback final sur le libellé nature. 400c
            # suffit à la vignette site. La première phrase doit être
            # self-contained pour le digest email aussi.
            notice = info.get("notice") or ""
            body_head = info.get("body_head") or ""
            if notice:
                summary = notice[:400]
            elif body_head:
                summary = body_head[:400]
            else:
                summary = f"{info['nature'].capitalize()} publié au JORF."

            out.append(Item(
                source_id=src["id"],
                uid=info["id"],
                category=cat,
                chamber="JORF",
                title=info["title"][:220],
                url=info["url"],
                published_at=info["date"],
                summary=summary,
                raw={
                    "nature": info["nature"],
                    "dump": url,
                    "notice": notice,
                    # R26 — haystack body (3000c). Lu par matcher.apply via
                    # le paramètre `haystack_extra` pour élargir la
                    # couverture keywords aux textes dont le titre est
                    # générique mais le corps parle de sport.
                    "haystack_body": body_head,
                },
            ))
    log.info("DILA JORF : %d items uniques sur %d dumps", len(out), len(dumps))
    return out
