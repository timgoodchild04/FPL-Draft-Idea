"""Our own league structures: seasons, managers, divisions, drafts, rosters.

Design note (the mid-season split):
A *Division* is the single draftable unit - one standalone snake draft with its
own rosters. A *Season* has two *Stages*; each stage has its own Divisions.
"Re-drafting at the split" is therefore not a special mode: stage 2 is just a
fresh set of Divisions with fresh Drafts. The season-level maths (carry-over
points + promotion/relegation) lives above these tables and is the only thing
that connects stage 1 to stage 2. That layer arrives in a later milestone.
"""
from __future__ import annotations

from enum import Enum

from sqlmodel import Field, SQLModel, UniqueConstraint

# A valid squad: exactly these counts, summing to SQUAD_SIZE.
POSITION_CAPS = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
SQUAD_SIZE = sum(POSITION_CAPS.values())  # 15


class DraftStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    complete = "complete"


class Season(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    # Stage 1 covers gameweeks 1..split_gameweek; stage 2 covers the rest.
    split_gameweek: int = 19
    current_stage: int = 1


class Manager(SQLModel, table=True):
    """A person. Persists across stages and seasons."""

    id: int | None = Field(default=None, primary_key=True)
    name: str


class Division(SQLModel, table=True):
    """One draftable league within a season+stage.

    tier 1 = top division, tier 2 = second division (feeds promotion/relegation).
    """

    id: int | None = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="season.id", index=True)
    stage: int = Field(index=True)  # 1 or 2
    tier: int  # 1 (top) or 2
    name: str  # e.g. "Division A - Stage 1"
    draft_status: DraftStatus = DraftStatus.pending


class DivisionMembership(SQLModel, table=True):
    """Which managers are in a division, and their draft seed order."""

    __table_args__ = (
        UniqueConstraint("division_id", "manager_id", name="uq_div_manager"),
        UniqueConstraint("division_id", "seed", name="uq_div_seed"),
    )
    id: int | None = Field(default=None, primary_key=True)
    division_id: int = Field(foreign_key="division.id", index=True)
    manager_id: int = Field(foreign_key="manager.id", index=True)
    seed: int  # 1..N; snake draft order


class DraftPick(SQLModel, table=True):
    """Immutable record of the draft: pick N was manager M taking player P."""

    __table_args__ = (
        UniqueConstraint("division_id", "pick_number", name="uq_div_pick"),
        UniqueConstraint("division_id", "player_id", name="uq_div_pick_player"),
    )
    id: int | None = Field(default=None, primary_key=True)
    division_id: int = Field(foreign_key="division.id", index=True)
    pick_number: int  # 1-indexed, global across the draft
    round_number: int
    manager_id: int = Field(foreign_key="manager.id", index=True)
    player_id: int = Field(foreign_key="player.id", index=True)


class RosterSlot(SQLModel, table=True):
    """A player currently owned by a manager in a division.

    Seeded from the draft, then mutated by waivers/transfers later. Kept separate
    from DraftPick so trades don't rewrite draft history.
    """

    __table_args__ = (
        UniqueConstraint("division_id", "player_id", name="uq_roster_player"),
    )
    id: int | None = Field(default=None, primary_key=True)
    division_id: int = Field(foreign_key="division.id", index=True)
    manager_id: int = Field(foreign_key="manager.id", index=True)
    player_id: int = Field(foreign_key="player.id", index=True)
