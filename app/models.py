"""Reference-data models mirrored from the FPL API.

These tables hold the read-only football data we pull from FPL: teams,
players, gameweeks, and each player's per-gameweek points. Our own
league/draft tables will be added in a later milestone and will reference
Player.id here.
"""
from __future__ import annotations

from sqlmodel import Field, SQLModel

# FPL element_type -> position label.
POSITION_BY_TYPE = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


class Team(SQLModel, table=True):
    """A Premier League club."""

    id: int = Field(primary_key=True)  # FPL team id
    name: str
    short_name: str
    code: int


class Player(SQLModel, table=True):
    """A player (FPL 'element'). Holds season-aggregate stats."""

    id: int = Field(primary_key=True)  # FPL element id
    web_name: str
    first_name: str
    second_name: str
    team_id: int = Field(foreign_key="team.id", index=True)
    element_type: int = Field(index=True)  # 1 GK, 2 DEF, 3 MID, 4 FWD
    total_points: int = 0
    minutes: int = 0
    goals_scored: int = 0
    assists: int = 0
    clean_sheets: int = 0
    now_cost: int = 0  # in tenths of a million; informational only for draft
    status: str = "a"  # a=available, i=injured, s=suspended, u=unavailable

    @property
    def position(self) -> str:
        return POSITION_BY_TYPE.get(self.element_type, "UNK")


class Gameweek(SQLModel, table=True):
    """An FPL 'event' (gameweek 1-38)."""

    id: int = Field(primary_key=True)
    name: str
    deadline_time: str | None = None
    finished: bool = False
    is_current: bool = False
    is_next: bool = False


class PlayerGameweekStats(SQLModel, table=True):
    """A player's actual points and key stats for one gameweek.

    This is the raw material our scoring uses: to score a manager's squad in
    a gameweek we sum total_points here for the players they fielded.
    """

    player_id: int = Field(foreign_key="player.id", primary_key=True)
    gameweek_id: int = Field(foreign_key="gameweek.id", primary_key=True)
    total_points: int = 0
    minutes: int = 0
    goals_scored: int = 0
    assists: int = 0
    clean_sheets: int = 0
    bonus: int = 0
