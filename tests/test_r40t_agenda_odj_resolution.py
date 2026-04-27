"""R40-T (2026-04-27) — Titre agenda AN par item ODJ qui matche le keyword.

Bug identifié par Cyril 2026-04-27 sur la réunion `RUANR5L17S2026IDC459749`
(commission affaires culturelles AN, 28/04 16:30) : 2 items à l'ODJ —
1. audition à huis clos sur les ingérences étrangères dans les processus
   électoraux (avec SGDSN/Viginum)
2. désignation du rapporteur sur la PPL « relative à l'organisation, à
   la gestion et au financement du sport professionnel » (n°1560)

Le pipeline prenait `odj_items[0]` (item 1) comme titre → la veille
sport remontait cette réunion avec un titre sur les ingérences
étrangères, alors que c'est l'item 2 qui matche le keyword sport
(« sport professionnel », ajouté en R40-R).

Fix R40-T :
1. `_normalize_agenda` stocke TOUS les items ODJ dans `raw.odj_items`
   (pas seulement le premier comme fallback main_title).
2. `_resolve_agenda_odj_item(odj_items, kws)` parcourt les items et
   retourne celui qui contient un keyword matché.
3. `_load` (export) réécrit `r["title"]` avec ce point ODJ.
4. Capitalisation 1ère lettre (préserve les sigles internes via
   `s[:1].upper() + s[1:]`, contrairement à `.capitalize()` qui mettrait
   en minuscule le reste).

Si aucun keyword ne matche dans les odj_items (cas rare où le match
vient du summary ou d'un autre champ), pas de modification — fallback
sur le titre actuel.
"""
from __future__ import annotations

from src.site_export import _resolve_agenda_odj_item


# ---------------------------------------------------------------------------
# 1. _resolve_agenda_odj_item — sélection du bon item
# ---------------------------------------------------------------------------


def test_resolve_choisit_litem_qui_matche_keyword():
    """Cas réel : le keyword 'sport professionnel' tombe dans le 2ᵉ item."""
    odj = [
        "audition à huis clos sur les ingérences étrangères dans les processus électoraux",
        "désignation du rapporteur sur la PPL relative au sport professionnel n°1560",
    ]
    title = _resolve_agenda_odj_item(odj, ["sport professionnel"])
    assert title.startswith("Désignation du rapporteur")
    assert "sport professionnel" in title


def test_resolve_capitalise_premiere_lettre():
    """Le titre retourné doit avoir sa 1ère lettre en majuscule —
    important pour les ODJ qui commencent par minuscule (« audition… »,
    « désignation… »)."""
    odj = ["audition de M. Dupont sur le dopage."]
    title = _resolve_agenda_odj_item(odj, ["dopage"])
    assert title.startswith("Audition")


def test_resolve_preserve_sigles_internes():
    """La capitalisation NE DOIT PAS mettre en minuscule les sigles
    internes (PSG, JOP, AFLD…). On utilise `s[:1].upper() + s[1:]`,
    pas `.capitalize()` qui casserait."""
    odj = [
        "présentation du rapport AFLD sur le dopage et le PSG en Ligue des champions"
    ]
    title = _resolve_agenda_odj_item(odj, ["dopage"])
    assert "AFLD" in title  # sigle préservé
    assert "PSG" in title   # sigle préservé
    assert "Ligue des champions" in title  # capitalisation propre préservée
    assert title.startswith("Présentation")  # 1ère lettre uppercase


def test_resolve_strip_leading_punctuation():
    """Les puces / tirets en début d'item sont retirés (cohérent avec
    le strip dans _normalize_agenda)."""
    odj = ["– audition de M. Dupont sur le dopage", "•examen de l'amendement n°1"]
    title = _resolve_agenda_odj_item(odj, ["dopage"])
    assert title.startswith("Audition")
    assert "–" not in title[:5]


def test_resolve_no_match_renvoie_chaine_vide():
    """Aucun keyword dans les odj_items → no change."""
    odj = ["audition sur l'agriculture", "examen du PJL santé"]
    assert _resolve_agenda_odj_item(odj, ["dopage"]) == ""


def test_resolve_odj_vide_renvoie_chaine_vide():
    assert _resolve_agenda_odj_item([], ["dopage"]) == ""
    assert _resolve_agenda_odj_item(None, ["dopage"]) == ""


def test_resolve_keywords_vides_renvoie_chaine_vide():
    odj = ["audition sur le dopage"]
    assert _resolve_agenda_odj_item(odj, []) == ""
    assert _resolve_agenda_odj_item(odj, None) == ""


def test_resolve_filtre_pseudo_keywords_R39G():
    """Les pseudo-keywords commençant par '(' (R39-G : '(flux complet)',
    etc.) ne doivent pas déclencher de match — ils ne figurent jamais
    dans le contenu ODJ et matcheraient artificiellement."""
    odj = ["audition sur le sport amateur"]
    # Si un pseudo-kw fait matcher, le résultat serait non vide.
    # On vérifie qu'avec uniquement des pseudo-kw, c'est no-op.
    assert _resolve_agenda_odj_item(odj, ["(flux complet)"]) == ""
    # Mix : ignore le pseudo, prend le vrai keyword
    title = _resolve_agenda_odj_item(odj, ["(flux complet)", "sport amateur"])
    assert title.startswith("Audition")


def test_resolve_premier_item_qui_matche_si_plusieurs_kws():
    """Si plusieurs items contiennent des keywords, on prend le premier
    de la liste (ordre du parser)."""
    odj = [
        "audition sur le dopage",
        "examen du PJL sport amateur",
    ]
    # Les deux items contiennent un keyword. On prend le 1er.
    title = _resolve_agenda_odj_item(odj, ["dopage", "sport amateur"])
    assert "dopage" in title


def test_resolve_kw_recherche_case_insensitive():
    """Le matching ignore la casse pour comparer keywords ↔ items."""
    odj = ["Audition SUR le DOPAGE et la lutte AFLD"]
    title = _resolve_agenda_odj_item(odj, ["dopage"])
    assert "DOPAGE" in title  # casse originale préservée dans le titre


def test_resolve_tronque_220_chars():
    """Le titre est borné à 220 chars (cohérence avec Item.title cap)."""
    long_item = "audition " + ("très détaillée " * 30) + "sur le dopage"
    title = _resolve_agenda_odj_item([long_item], ["dopage"])
    assert len(title) <= 220


def test_resolve_item_non_string_skip():
    """Robustesse : un item non-string dans odj_items (corruption DB,
    JSON inattendu) ne doit pas crasher."""
    odj = [None, 42, ["nested"], "audition sur le dopage"]
    title = _resolve_agenda_odj_item(odj, ["dopage"])
    assert title.startswith("Audition")


# ---------------------------------------------------------------------------
# 2. Régression : _normalize_agenda pose raw.odj_items
# ---------------------------------------------------------------------------


def test_normalize_agenda_pose_odj_items():
    """`raw.odj_items` doit être ajouté par `_normalize_agenda`."""
    from src.sources import assemblee as an_mod
    src_path = an_mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src_code = f.read()
    assert '"odj_items"' in src_code, (
        "_normalize_agenda doit poser raw.odj_items (R40-T)"
    )


# ---------------------------------------------------------------------------
# 3. Régression sur le cas réel reproduit par Cyril
# ---------------------------------------------------------------------------


def test_cas_reel_reunion_28_04_sport_pro():
    """Reproduction de la réunion RUANR5L17S2026IDC459749 — affaires
    culturelles AN, 28/04. Avant R40-T : titre = audition ingérences.
    Après R40-T : titre = désignation rapporteur PPL sport pro."""
    odj = [
        ("audition, à huis clos, conjointe avec la commission des "
         "affaires étrangères, de M. Nicolas Roche, secrétaire général "
         "de la défense et de la sécurité nationale (SGDSN), et de "
         "M. Marc-Antoine Brillant, chef du service de vigilance et de "
         "protection contre les ingérences numériques étrangères "
         "(Viginum), sur la lutte contre les ingérences étrangères "
         "dans les processus électoraux."),
        ("désignation du rapporteur sur la proposition de loi, "
         "adoptée par le Sénat après engagement de la procédure "
         "accélérée, relative à l'organisation, à la gestion et au "
         "financement du sport professionnel (no 1560)."),
    ]
    title = _resolve_agenda_odj_item(odj, ["sport professionnel"])
    # Premier mot capitalisé
    assert title[0] == "D"
    assert title.startswith("Désignation du rapporteur")
    # Bon item retenu
    assert "sport professionnel" in title
    # Item ingérences NON retenu
    assert "Viginum" not in title
    assert "ingérences" not in title
