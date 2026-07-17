"""Persisted fixtures and weekly points for the custom two-division format.

A Fixture is one of our own head-to-head games (the official site doesn't create
cross-division games). EntryPoints stores each team's per-gameweek FPL score,
pulled from the draft site; our H2H results are computed by comparing those.
"""
from __future__ import annotations

from sqlmodel import Field, SQLModel, UniqueConstraint


class Fixture(SQLModel, table=True):
    """One custom H2H game. home/away are global FPL Draft entry ids."""

    id: int | None = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="season.id", index=True)
    gameweek: int = Field(index=True)
    home_entry: int
    away_entry: int
    kind: str = "division"  # "division" or "cross"


class LeagueMeta(SQLModel, table=True):
    """Season-level metadata, e.g. when the schedule was locked in."""

    season_id: int = Field(foreign_key="season.id", primary_key=True)
    fixtures_generated_at: str | None = None  # ISO UTC timestamp


class Rivalry(SQLModel, table=True):
    """A derby pairing (two teams who play an extra 'rivalry' game)."""

    __table_args__ = (
        UniqueConstraint("season_id", "entry_a", "entry_b", name="uq_rivalry"),
    )
    id: int | None = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="season.id", index=True)
    entry_a: int
    entry_b: int


class EntryPoints(SQLModel, table=True):
    """A team's actual FPL points for one gameweek (from /entry/{id}/history)."""

    __table_args__ = (
        UniqueConstraint("season_id", "entry_id", "gameweek", name="uq_entry_points"),
    )
    id: int | None = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="season.id", index=True)
    entry_id: int = Field(index=True)
    gameweek: int = Field(index=True)
    points: int = 0
