"""Per-gameweek lineups: which of a manager's 15 start, and the bench order."""
from __future__ import annotations

from sqlmodel import Field, SQLModel, UniqueConstraint

# Valid FPL formation bounds for the 11 starters (GK is always exactly 1).
FORMATION_BOUNDS = {"GK": (1, 1), "DEF": (3, 5), "MID": (2, 5), "FWD": (1, 3)}


class Lineup(SQLModel, table=True):
    """One row per owned player per gameweek.

    is_starter=True for the 11 that start; bench players carry bench_order
    (0-indexed) which drives auto-substitution priority.
    """

    __table_args__ = (
        UniqueConstraint(
            "division_id", "manager_id", "gameweek_id", "player_id", name="uq_lineup_slot"
        ),
    )
    id: int | None = Field(default=None, primary_key=True)
    division_id: int = Field(foreign_key="division.id", index=True)
    manager_id: int = Field(foreign_key="manager.id", index=True)
    gameweek_id: int = Field(foreign_key="gameweek.id", index=True)
    player_id: int = Field(foreign_key="player.id", index=True)
    is_starter: bool = True
    bench_order: int | None = None  # None for starters; 0..3 for bench
