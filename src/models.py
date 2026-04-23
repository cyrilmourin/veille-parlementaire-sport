"""Modèle pivot commun à toutes les sources."""
from __future__ import annotations

from datetime import datetime, date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Category = Literal[
    "dossiers_legislatifs",
    "jorf",
    "amendements",
    "questions",
    "comptes_rendus",
    "publications",
    "nominations",
    "agenda",
    "communiques",
]


class Item(BaseModel):
    """Un item unitaire (amendement, question, communiqué, etc.)."""

    # Identité
    source_id: str              # ex. "an_amendements", "min_sports_presse"
    uid: str                    # identifiant local à la source (amendement n°, id news…)

    # Classement
    category: Category          # catégorie Follaw.sv
    chamber: Optional[str] = None  # "AN" | "Senat" | "Elysee" | "Matignon" | "MinSports"…

    # Contenu
    title: str
    url: str
    published_at: Optional[datetime] = None
    summary: str = ""           # résumé court, ≤ 500 chars idéalement

    # Matching
    matched_keywords: list[str] = Field(default_factory=list)
    keyword_families: list[str] = Field(default_factory=list)
    snippet: str = ""           # extrait contextuel autour du 1er match (~160 chars)

    # R33 (2026-04-24) — Persistance des champs recalculés (audit §4.5).
    # Ces colonnes sont nullable en DB et optionnelles sur l'Item :
    # elles sont renseignées par les parseurs ou les fixups quand
    # l'information est disponible, consommées directement par l'export
    # (plus besoin de recalculer à chaque build). Non-renseignées =
    # fallback vers la logique actuelle (raw.* ou recalcul).
    dossier_id: Optional[str] = None      # clé canonique dosleg : "pjl24-630"
    canonical_url: Optional[str] = None   # URL dossier quand elle existe, sinon URL source
    status_label: Optional[str] = None    # état dosleg : "Adopté", "Retiré", etc.
    content_hash: Optional[str] = None    # sha1(title+summary) pour détecter un refresh silencieux

    # Brut
    raw: dict = Field(default_factory=dict, repr=False)

    @property
    def hash_key(self) -> str:
        return f"{self.source_id}::{self.uid}"

    @property
    def day(self) -> Optional[date]:
        return self.published_at.date() if self.published_at else None


class RunStats(BaseModel):
    source_id: str
    fetched: int = 0
    new: int = 0
    matched: int = 0
    errors: list[str] = Field(default_factory=list)
