"""HTTP API for the mid-season split and the combined season table."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import Session

from sqlmodel import select

from app import season as season_layer
from app.db import ENGINE
from app.league_models import Division, Season

router = APIRouter(prefix="/api/seasons", tags=["season"])


def _season(s: Session, season_id: int) -> Season:
    obj = s.get(Season, season_id)
    if obj is None:
        raise HTTPException(404, f"No season {season_id}")
    return obj


def _slim_members(members: list[dict]) -> list[dict]:
    return [{"manager_id": m["manager_id"], "manager": m["manager"], "stage1_points": m["points"]}
            for m in members]


@router.get("")
def list_seasons() -> list[dict]:
    with Session(ENGINE) as s:
        return [{"id": x.id, "name": x.name, "split_gameweek": x.split_gameweek,
                 "current_stage": x.current_stage}
                for x in s.exec(select(Season).order_by(Season.id)).all()]


@router.get("/{season_id}")
def get_season(season_id: int) -> dict:
    with Session(ENGINE) as s:
        season = _season(s, season_id)
        divs = s.exec(
            select(Division).where(Division.season_id == season_id)
            .order_by(Division.stage, Division.tier)
        ).all()
        return {
            "id": season.id, "name": season.name,
            "split_gameweek": season.split_gameweek, "current_stage": season.current_stage,
            "divisions": [{"id": d.id, "name": d.name, "stage": d.stage, "tier": d.tier,
                           "draft_status": d.draft_status.value} for d in divs],
        }


@router.get("/{season_id}/split/preview")
def preview_split(season_id: int, n_swap: int = 2) -> dict:
    """Show who would be promoted/relegated, without changing anything."""
    with Session(ENGINE) as s:
        season = _season(s, season_id)
        try:
            plan = season_layer.compute_split(s, season, n_swap)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {
            "promoted": plan["promoted"],
            "relegated": plan["relegated"],
            "stage2_tier1": _slim_members(plan["new_tier1"]),
            "stage2_tier2": _slim_members(plan["new_tier2"]),
        }


@router.post("/{season_id}/split/apply")
def apply_split(season_id: int, n_swap: int = 2) -> dict:
    """Create the stage-2 divisions (seeded worst-first) and advance the season.

    After this, start each new division's draft via the normal draft endpoints.
    """
    with Session(ENGINE) as s:
        season = _season(s, season_id)
        try:
            return season_layer.apply_split(s, season, n_swap)
        except ValueError as e:
            raise HTTPException(400, str(e))


@router.get("/{season_id}/table")
def combined_table(season_id: int) -> dict:
    """Overall standings across both stages (points carry over)."""
    with Session(ENGINE) as s:
        season = _season(s, season_id)
        return {
            "season": season.name,
            "current_stage": season.current_stage,
            "split_gameweek": season.split_gameweek,
            "table": season_layer.season_table(s, season),
        }
