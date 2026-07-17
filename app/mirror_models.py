"""Tables for 'mirror mode': a Division linked to a real FPL Draft mini-league.

In this mode the official site is the source of truth (drafting, lineups, weekly
scoring, H2H results). We ingest each linked league's entries and matches, then
build our custom two-division / promotion-relegation tables on top.

These are all new tables, so they don't disturb the internal-draft schema; the
two modes can coexist.
"""
from __future__ import annotations

from sqlmodel import Field, SQLModel, UniqueConstraint


class MirrorLink(SQLModel, table=True):
    """Links one of our Divisions to a real FPL Draft league id."""

    division_id: int = Field(foreign_key="division.id", primary_key=True)
    external_league_id: int = Field(index=True)
    league_name: str = ""


class MirrorEntry(SQLModel, table=True):
    """A team in a linked league.

    league_entry_id is the *league-local* id that matches reference; entry_id is
    the global team id found in a team URL and stable across that manager's
    leagues (used to follow a person across the mid-season split).
    """

    __table_args__ = (
        UniqueConstraint("division_id", "league_entry_id", name="uq_mirror_entry"),
    )
    id: int | None = Field(default=None, primary_key=True)
    division_id: int = Field(foreign_key="division.id", index=True)
    league_entry_id: int = Field(index=True)  # local id used by matches
    entry_id: int | None = Field(default=None, index=True)  # global team id
    team_name: str = ""
    manager_name: str = ""


class MirrorMatch(SQLModel, table=True):
    """One H2H fixture in a linked league. e1/e2 are league_entry_id (local)."""

    __table_args__ = (
        UniqueConstraint("division_id", "event", "e1", "e2", name="uq_mirror_match"),
    )
    id: int | None = Field(default=None, primary_key=True)
    division_id: int = Field(foreign_key="division.id", index=True)
    event: int = Field(index=True)  # gameweek
    e1: int
    e1_points: int = 0
    e2: int
    e2_points: int = 0
    finished: bool = False
