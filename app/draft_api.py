"""HTTP API for league setup: seasons and divisions.

Drafting itself happens on the official FPL Draft site, so this app doesn't run
drafts - a division is just linked to a real FPL Draft mini-league (see
mirror_api.py). This module only creates/lists/deletes the season + division
scaffolding those links hang off.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, delete

from app import setup_league
from app.db import ENGINE
from app.league_models import (
    Division,
    DivisionMembership,
    DraftPick,
    RosterSlot,
)
from app.lineup_models import Lineup
from app.mirror_models import MirrorEntry, MirrorLink, MirrorMatch

router = APIRouter(prefix="/api", tags=["league"])


class SeasonIn(BaseModel):
    name: str
    split_gameweek: int = 19


class DivisionIn(BaseModel):
    season_id: int
    stage: int = 1
    tier: int = 1
    name: str


def _get_division(session: Session, division_id: int) -> Division:
    div = session.get(Division, division_id)
    if div is None:
        raise HTTPException(404, f"No division {division_id}")
    return div


@router.post("/seasons")
def create_season(body: SeasonIn) -> dict:
    with Session(ENGINE) as s:
        season = setup_league.create_season(s, body.name, body.split_gameweek)
        return {"id": season.id, "name": season.name, "split_gameweek": season.split_gameweek}


@router.get("/divisions/{division_id}")
def get_division(division_id: int) -> dict:
    with Session(ENGINE) as s:
        d = _get_division(s, division_id)
        return {"id": d.id, "name": d.name, "stage": d.stage,
                "tier": d.tier, "season_id": d.season_id}


@router.post("/divisions")
def create_division(body: DivisionIn) -> dict:
    with Session(ENGINE) as s:
        d = setup_league.create_division(s, body.season_id, body.stage, body.tier, body.name)
        return {"id": d.id, "name": d.name, "stage": d.stage, "tier": d.tier}


@router.delete("/divisions/{division_id}")
def delete_division(division_id: int) -> dict:
    with Session(ENGINE) as s:
        div = _get_division(s, division_id)
        # Clear every row that references this division before removing it.
        for table in (MirrorMatch, MirrorEntry, Lineup, RosterSlot, DraftPick, DivisionMembership):
            s.exec(delete(table).where(table.division_id == division_id))
        s.exec(delete(MirrorLink).where(MirrorLink.division_id == division_id))
        s.delete(div)
        s.commit()
        return {"deleted": division_id, "name": div.name}
