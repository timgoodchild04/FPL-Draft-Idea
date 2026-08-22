"""Our own league structures: seasons and divisions.

A *Division* is a fixed real-world FPL Draft mini-league for a season, mapped
to its entries via mirror_models.MirrorEntry (not drafted or rostered in this
app - that all happens on the official FPL Draft site).
"""
from __future__ import annotations

from sqlmodel import Field, SQLModel


class Season(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    # Stage 1 covers gameweeks 1..split_gameweek; stage 2 covers the rest.
    split_gameweek: int = 19
    current_stage: int = 1
    # ISO UTC timestamp once this season is closed out and superseded by a new
    # one; None means this is the active season. See custom_league.finished_gameweeks
    # for why archived seasons snapshot their own "finished" state instead of
    # trusting the (globally shared, id-reused-every-year) Gameweek table forever.
    archived_at: str | None = None


class Division(SQLModel, table=True):
    """One of the two fixed divisions within a season."""

    id: int | None = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="season.id", index=True)
    stage: int = Field(index=True)  # 1 or 2
    tier: int  # 1 (top) or 2
    name: str  # e.g. "Division A"
