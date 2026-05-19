"""Tests R43-Y (2026-05-19) — Refonte page d'accueil : 3 modules tiers
harmonisés (Actualité 24h | Dernières publications | Spécial PPL Sport pro).

Demande Cyril 19/05 :
- 3 modules sur un tiers du main chacun
- Hauteurs harmonisées (tous à la hauteur du plus haut)
- Max 5 items visibles + dropdown pour 5 autres max
- "Dernières publications" sort de la sidebar (uniquement sur la home)

Sidebar continue d'afficher "Dernières publications" sur les autres
pages (test : modification du `{{ if ... }}` dans sidebar.html).
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_r43y_recent_module_format_card():
    """`_render_home_recent_module` doit retourner du HTML avec la
    structure card attendue (header, items, dropdown éventuel)."""
    from src.site_export import _render_home_recent_module

    recent_items = [
        {"chamber": "AN", "title": f"Item {i}", "url": f"https://x.com/{i}"}
        for i in range(7)
    ]
    html = "\n".join(_render_home_recent_module(recent_items))
    assert "home-tier-card--recent" in html
    assert "home-tier-card__title" in html
    assert "Actualité des dernières 24 h" in html
    # 5 visibles + dropdown contenant les 2 restants
    assert html.count('class="home-tier-card__item"') == 7  # tous rendus (5 head + 2 tail)
    assert "home-tier-card__fold" in html  # le dropdown
    assert "Voir les 2 suivantes" in html


def test_r43y_recent_module_pas_de_dropdown_si_5_ou_moins():
    """Si ≤ 5 items, pas de dropdown (toute la liste est visible direct)."""
    from src.site_export import _render_home_recent_module

    html = "\n".join(_render_home_recent_module([
        {"chamber": "AN", "title": f"Item {i}", "url": f"https://x.com/{i}"}
        for i in range(3)
    ]))
    assert "home-tier-card__fold" not in html
    assert "Voir les" not in html


def test_r43y_recent_module_limit_5_dans_dropdown():
    """Si plus de 10 items, le dropdown se limite à 5 supplémentaires
    (max total = 10)."""
    from src.site_export import _render_home_recent_module

    recent_items = [
        {"chamber": "AN", "title": f"Item {i}", "url": f"https://x.com/{i}"}
        for i in range(20)  # bien plus que 10
    ]
    html = "\n".join(_render_home_recent_module(recent_items))
    # 5 head + 5 tail = 10 items rendus, pas 20
    assert html.count('class="home-tier-card__item"') == 10
    assert "Voir les 5 suivantes" in html


def test_r43y_publications_module_format_card():
    """`_render_home_publications_module` doit utiliser le bucket
    `communiques` du `by_cat` et rendre une card cohérente avec le
    module 24h."""
    from src.site_export import _render_home_publications_module

    by_cat = {
        "communiques": [
            {"chamber": "MinSports", "title": f"Comm {i}",
             "url": f"https://x.com/c{i}", "published_at": "2026-05-19"}
            for i in range(8)
        ],
    }
    html = "\n".join(_render_home_publications_module(by_cat))
    assert "home-tier-card--publications" in html
    assert "Dernières publications" in html
    # Lien "Voir toutes les publications" obligatoire
    assert 'href="/items/communiques/"' in html


def test_r43y_publications_module_vide_si_pas_de_communiques():
    """Pas de communiqués dans `by_cat` → message vide propre."""
    from src.site_export import _render_home_publications_module

    html = "\n".join(_render_home_publications_module({"communiques": []}))
    assert "home-tier-card__empty" in html
    assert "Pas de publication" in html


def test_r43y_recent_item_html_security():
    """Le rendu d'item doit échapper le HTML pour éviter XSS depuis les
    titres scrapés."""
    from src.site_export import _render_home_tier_item

    html = "\n".join(_render_home_tier_item({
        "chamber": "AN",
        "title": "<script>alert('xss')</script> Titre",
        "url": "https://example.com/x",
    }))
    # Le < et > du <script> sont échappés
    assert "<script>" not in html
    assert "&lt;script&gt;" in html or "&lt;script" in html


def test_r43y_recent_item_skip_si_url_vide():
    """Item sans URL → skip (rien ne sort). Pas de lien mort dans le mail."""
    from src.site_export import _render_home_tier_item

    out = _render_home_tier_item({"chamber": "AN", "title": "Pas d'URL", "url": ""})
    assert out == []
    out = _render_home_tier_item({"chamber": "AN", "title": "", "url": "https://x"})
    assert out == []


def test_r43y_sidebar_publications_caché_sur_home():
    """Sidebar Hugo doit avoir le test `{{ if and (ne .Type "communiques") (not .IsHome) }}`
    pour le module "Dernières publications" (R43-Y déplace ce module
    dans le main de la home)."""
    sidebar = Path("site/layouts/partials/sidebar.html").read_text()
    # Le test combinant les 2 conditions doit être présent
    assert "not .IsHome" in sidebar
    # Et toujours "ne .Type \"communiques\"" pour la page Publications
    assert 'ne .Type "communiques"' in sidebar


def test_r43y_css_grid_3_colonnes_present():
    """style.css doit contenir le grid `.home-top-tier` 3 colonnes +
    align-items stretch (clé pour harmoniser les hauteurs)."""
    css = Path("site/static/style.css").read_text()
    assert ".home-top-tier" in css
    assert "grid-template-columns: 1fr 1fr 1fr" in css
    assert "align-items: stretch" in css
    # Responsive
    assert "max-width: 1100px" in css
