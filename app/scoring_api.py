"""HTTP API for lineups, gameweek scores, and division standings."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from app import scoring
from app.db import ENGINE
from app.league_models import Division, RosterSlot
from app.lineup_models import Lineup
from app.models import POSITION_BY_TYPE, Player

router = APIRouter(prefix="/api", tags=["scoring"])


class LineupIn(BaseModel):
    gameweek_id: int
    starters: list[int]  # 11 player ids
    bench: list[int]     # remaining owned players, in substitution order


def _division(session: Session, division_id: int) -> Division:
    d = session.get(Division, division_id)
    if d is None:
        raise HTTPException(404, f"No division {division_id}")
    return d


@router.put("/divisions/{division_id}/managers/{manager_id}/lineup")
def set_lineup(division_id: int, manager_id: int, body: LineupIn) -> dict:
    with Session(ENGINE) as s:
        _division(s, division_id)
        owned = set(s.exec(
            select(RosterSlot.player_id).where(
                RosterSlot.division_id == division_id,
                RosterSlot.manager_id == manager_id,
            )
        ).all())
        submitted = body.starters + body.bench
        if set(submitted) != owned:
            raise HTTPException(400, "Lineup must contain exactly this manager's owned players.")
        if len(body.starters) != 11:
            raise HTTPException(400, "A lineup needs exactly 11 starters.")
        positions = [POSITION_BY_TYPE[s.get(Player, pid).element_type] for pid in body.starters]
        if not scoring.valid_formation(positions):
            raise HTTPException(400, f"Invalid formation for starters: {positions}")

        # Replace any existing lineup for this gameweek.
        s.exec(delete(Lineup).where(
            Lineup.division_id == division_id,
            Lineup.manager_id == manager_id,
            Lineup.gameweek_id == body.gameweek_id,
        ))
        for pid in body.starters:
            s.add(Lineup(division_id=division_id, manager_id=manager_id,
                         gameweek_id=body.gameweek_id, player_id=pid, is_starter=True))
        for order, pid in enumerate(body.bench):
            s.add(Lineup(division_id=division_id, manager_id=manager_id,
                         gameweek_id=body.gameweek_id, player_id=pid,
                         is_starter=False, bench_order=order))
        s.commit()
        return {"ok": True, "starters": len(body.starters), "bench": len(body.bench)}


@router.get("/divisions/{division_id}/managers/{manager_id}/lineup")
def get_lineup(division_id: int, manager_id: int, gameweek_id: int) -> dict:
    with Session(ENGINE) as s:
        _division(s, division_id)
        starters, bench = scoring._resolve_lineup(s, division_id, manager_id, gameweek_id)
        return {
            "gameweek_id": gameweek_id,
            "starters": [{"id": p.player_id, "name": p.name, "pos": p.position} for p in starters],
            "bench": [{"id": p.player_id, "name": p.name, "pos": p.position} for p in bench],
        }


@router.get("/divisions/{division_id}/managers/{manager_id}/gameweek/{gameweek_id}")
def gameweek_score(division_id: int, manager_id: int, gameweek_id: int) -> dict:
    with Session(ENGINE) as s:
        _division(s, division_id)
        r = scoring.score_gameweek(s, division_id, manager_id, gameweek_id)
        return {
            "manager_id": r.manager_id,
            "gameweek_id": r.gameweek_id,
            "points": r.points,
            "starting_xi": r.starters_used,
            "auto_subs": r.subs_made,
        }


@router.get("/divisions/{division_id}/standings")
def standings(division_id: int) -> dict:
    with Session(ENGINE) as s:
        div = _division(s, division_id)
        table = scoring.division_standings(s, div)
        # Trim the per-gameweek map from the summary view for readability.
        summary = [{k: v for k, v in row.items() if k != "gameweek_points"} for row in table]
        return {"division": div.name, "stage": div.stage, "table": summary}
