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
