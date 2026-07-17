"""HTTP API for the custom two-division format.

Two routers:
  * `router`        - season-scoped endpoints (explicit season id)
  * `current_router` - /api/custom/* convenience layer that operates on the
                        single current season, which is what the UI drives.
"""
from __future__ import annotations

import base64
import os
import secrets

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from app import custom_league, fpldraft_client, setup_league
from app.db import ENGINE
from app.league_models import Division, Season
from app.mirror_models import MirrorEntry, MirrorLink
from app.models import Gameweek
from app.schedule_models import EntryPoints, Fixture, LeagueMeta, Rivalry

router = APIRouter(prefix="/api/seasons", tags=["custom-league"])
current_router = APIRouter(prefix="/api/custom", tags=["custom-current"])


def require_admin(authorization: str | None = Header(default=None)) -> bool:
    """HTTP Basic gate for admin (setup) actions. Credentials from env, default admin/admin."""
    user = os.environ.get("ADMIN_USER", "admin")
    pw = os.environ.get("ADMIN_PASS", "admin")
    unauth = HTTPException(401, "Admin login required", headers={"WWW-Authenticate": "Basic"})
    if not authorization or not authorization.startswith("Basic "):
        raise unauth
    try:
        u, p = base64.b64decode(authorization.split(" ", 1)[1]).decode().split(":", 1)
    except Exception:
        raise unauth
    if not (secrets.compare_digest(u, user) and secrets.compare_digest(p, pw)):
        raise unauth
    return True


# ========================= season-scoped ================================
def _season(s: Session, season_id: int) -> Season:
    obj = s.get(Season, season_id)
    if obj is None:
        raise HTTPException(404, f"No season {season_id}")
    return obj


@router.post("/{season_id}/schedule/generate")
def generate_schedule(season_id: int, seed: int | None = None) -> dict:
    with Session(ENGINE) as s:
        try:
            return custom_league.generate_and_store_schedule(s, _season(s, season_id), seed)
        except ValueError as e:
            raise HTTPException(400, str(e))


@router.get("/{season_id}/custom-table")
def custom_table(season_id: int) -> dict:
    with Session(ENGINE) as s:
        try:
            return custom_league.standings(s, _season(s, season_id))
        except ValueError as e:
            raise HTTPException(400, str(e))


# ========================= current-season layer =========================
def _current(s: Session) -> Season | None:
    return s.exec(select(Season).order_by(Season.id.desc())).first()


def _ensure_season(s: Session) -> Season:
    cur = _current(s)
    return cur or setup_league.create_season(s, "Draft League", split_gameweek=35)


def _stage1_divisions(s: Session, season: Season) -> list[Division]:
    return s.exec(
        select(Division).where(Division.season_id == season.id, Division.stage == 1)
        .order_by(Division.tier)
    ).all()


def _ensure_divisions(s: Session, season: Season) -> tuple[Division, Division]:
    divs = _stage1_divisions(s, season)
    names = ["League 1 (Division A)", "League 2 (Division B)"]
    while len(divs) < 2:
        tier = len(divs) + 1
        setup_league.create_division(s, season.id, stage=1, tier=tier, name=names[tier - 1])
        divs = _stage1_divisions(s, season)
    return divs[0], divs[1]


def _division_entries(s: Session, division_id: int) -> list[dict]:
    rows = s.exec(
        select(MirrorEntry).where(MirrorEntry.division_id == division_id)
        .order_by(MirrorEntry.id)
    ).all()
    return [{"name": e.manager_name, "entry_id": e.entry_id}
            for e in rows if e.entry_id is not None]


def _status(s: Session, season: Season) -> dict:
    divs = _stage1_divisions(s, season)
    divisions = []
    for d in divs:
        entries = _division_entries(s, d.id)
        divisions.append({"division_id": d.id, "division_name": d.name,
                          "teams": len(entries), "entries": entries})
    fixtures_generated = s.exec(
        select(Fixture).where(Fixture.season_id == season.id).limit(1)).first() is not None
    points_synced = s.exec(
        select(EntryPoints).where(EntryPoints.season_id == season.id).limit(1)).first() is not None
    sizes = [x["teams"] for x in divisions]
    both_filled = len(divisions) == 2 and all(n > 0 for n in sizes)
    equal = both_filled and len(set(sizes)) == 1 and sizes[0] >= 2

    all_entries = {e["entry_id"]: e["name"] for d in divisions for e in d["entries"]}
    rivs = s.exec(select(Rivalry).where(Rivalry.season_id == season.id)).all()
    rivalries = [{"a": r.entry_a, "b": r.entry_b,
                  "a_name": all_entries.get(r.entry_a, str(r.entry_a)),
                  "b_name": all_entries.get(r.entry_b, str(r.entry_b))} for r in rivs]
    # Valid when they pair every team exactly once.
    flat = [x for r in rivs for x in (r.entry_a, r.entry_b)]
    rivalries_valid = (both_filled and equal and len(flat) == len(all_entries)
                       and sorted(flat) == sorted(all_entries.keys()))
    meta = s.get(LeagueMeta, season.id)
    return {
        "has_season": True, "season_id": season.id,
        "divisions": divisions, "both_filled": both_filled, "sizes_equal": equal,
        "rivalries": rivalries, "rivalries_valid": rivalries_valid,
        "fixtures_generated": fixtures_generated, "points_synced": points_synced,
        "fixtures_generated_at": meta.fixtures_generated_at if meta else None,
        "can_generate": equal and not fixtures_generated,
    }


class TeamsIn(BaseModel):
    division_a: list[int]  # FPL Draft team (entry) ids
    division_b: list[int]


def _entry_name(pub: dict, entry_id: int) -> str:
    e = pub.get("entry") or pub
    nm = f"{e.get('player_first_name', '')} {e.get('player_last_name', '')}".strip()
    return nm or e.get("name") or f"Team {entry_id}"


@current_router.get("/status")
def status() -> dict:
    with Session(ENGINE) as s:
        season = _current(s)
        if season is None:
            return {"has_season": False}
        return _status(s, season)


@current_router.post("/teams")
def set_teams(body: TeamsIn, _admin: bool = Depends(require_admin)) -> dict:
    """Set each division's roster from team ids. Names are pulled from the site;
    every id is validated first, so an invalid id blocks the save (not silently)."""
    a, b = body.division_a, body.division_b
    if len(a) != len(b):
        raise HTTPException(400, f"Divisions must be equal size (got {len(a)} and {len(b)}).")
    if len(a) < 2:
        raise HTTPException(400, "Enter at least 2 team ids per division.")
    ids = a + b
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Team ids must be unique - a team can't be in both divisions.")

    # Validate + resolve names up front. A bad id aborts the whole save.
    names: dict[int, str] = {}
    bad: list[int] = []
    with httpx.Client() as client:
        for eid in ids:
            try:
                names[eid] = _entry_name(fpldraft_client.fetch_entry_public(client, eid), eid)
            except Exception:
                bad.append(eid)
    if bad:
        raise HTTPException(400, f"These team ids aren't valid FPL Draft teams: "
                                 f"{', '.join(map(str, bad))}. Check and re-enter them.")

    with Session(ENGINE) as s:
        season = _ensure_season(s)
        if s.exec(select(Fixture).where(Fixture.season_id == season.id).limit(1)).first():
            raise HTTPException(400, "Fixtures already generated and locked - use 'Start over' to change teams.")
        div_a, div_b = _ensure_divisions(s, season)
        for div, lst in ((div_a, a), (div_b, b)):
            s.exec(delete(MirrorEntry).where(MirrorEntry.division_id == div.id))
            s.exec(delete(MirrorLink).where(MirrorLink.division_id == div.id))
            for eid in lst:
                s.add(MirrorEntry(division_id=div.id, league_entry_id=eid,
                                  entry_id=eid, manager_name=names[eid], team_name=""))
        # Teams changed - any existing rivalries reference old ids, so clear them.
        s.exec(delete(Rivalry).where(Rivalry.season_id == season.id))
        s.commit()
        return _status(s, season)


class RivalriesIn(BaseModel):
    pairs: list[list[int]]  # e.g. [[254949, 254948], ...] - each team exactly once


@current_router.post("/rivalries")
def set_rivalries(body: RivalriesIn, _admin: bool = Depends(require_admin)) -> dict:
    """Set the derby pairs. Must pair every team exactly once (a perfect matching)."""
    with Session(ENGINE) as s:
        season = _current(s)
        if season is None:
            raise HTTPException(400, "Set up your teams first.")
        if s.exec(select(Fixture).where(Fixture.season_id == season.id).limit(1)).first():
            raise HTTPException(400, "Fixtures already generated and locked - use 'Start over' to change rivalries.")
        team_ids = {e.entry_id for d in _stage1_divisions(s, season)
                    for e in s.exec(select(MirrorEntry).where(MirrorEntry.division_id == d.id)).all()}
        flat = [x for pair in body.pairs for x in pair]
        if any(len(p) != 2 for p in body.pairs):
            raise HTTPException(400, "Each rivalry must have exactly two teams.")
        if any(a == b for a, b in body.pairs):
            raise HTTPException(400, "A team can't be its own rival.")
        if sorted(flat) != sorted(team_ids):
            raise HTTPException(400, "Rivalries must pair every team exactly once "
                                     "(each player in one and only one pair).")
        s.exec(delete(Rivalry).where(Rivalry.season_id == season.id))
        for aid, bid in body.pairs:
            s.add(Rivalry(season_id=season.id, entry_a=aid, entry_b=bid))
        s.commit()
        return _status(s, season)


@current_router.get("/auth-check")
def auth_check(_admin: bool = Depends(require_admin)) -> dict:
    return {"ok": True}


@current_router.get("/lookup")
def lookup(entry_id: int) -> dict:
    """Resolve a single team id to its manager name (for live feedback in Setup)."""
    with httpx.Client() as client:
        try:
            pub = fpldraft_client.fetch_entry_public(client, entry_id)
        except Exception:
            raise HTTPException(404, f"No FPL Draft team with id {entry_id}.")
    return {"entry_id": entry_id, "name": _entry_name(pub, entry_id)}


@current_router.post("/reset")
def reset(_admin: bool = Depends(require_admin)) -> dict:
    """Clear fixtures, points and teams for the current season so it can be redone."""
    with Session(ENGINE) as s:
        season = _current(s)
        if season is None:
            return {"has_season": False}
        for d in _stage1_divisions(s, season):
            s.exec(delete(MirrorEntry).where(MirrorEntry.division_id == d.id))
            s.exec(delete(MirrorLink).where(MirrorLink.division_id == d.id))
        s.exec(delete(Fixture).where(Fixture.season_id == season.id))
        s.exec(delete(EntryPoints).where(EntryPoints.season_id == season.id))
        s.exec(delete(Rivalry).where(Rivalry.season_id == season.id))
        meta = s.get(LeagueMeta, season.id)
        if meta:
            s.delete(meta)
        s.commit()
        return _status(s, season)


@current_router.get("/sample-ids")
def sample_ids(n: int = 14, _admin: bool = Depends(require_admin)) -> dict:
    """Find some real, valid FPL Draft team ids (for quick testing/filling)."""
    import random
    candidates = list(range(254800, 255260))
    random.shuffle(candidates)
    out, attempts = [], 0
    with httpx.Client() as client:
        for eid in candidates:
            if len(out) >= n or attempts >= 60:
                break
            attempts += 1
            try:
                out.append({"entry_id": eid,
                            "name": _entry_name(fpldraft_client.fetch_entry_public(client, eid), eid)})
            except Exception:
                continue
    return {"ids": out}


@current_router.post("/generate")
def generate(seed: int | None = None, _admin: bool = Depends(require_admin)) -> dict:
    with Session(ENGINE) as s:
        season = _current(s)
        if season is None:
            raise HTTPException(400, "Set up your two leagues first.")
        try:
            summary = custom_league.generate_and_store_schedule(s, season, seed)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {**summary, **_status(s, season)}


@current_router.post("/sync-points")
def sync_points(_admin: bool = Depends(require_admin)) -> dict:
    with Session(ENGINE) as s:
        season = _current(s)
        if season is None:
            raise HTTPException(400, "Set up your two leagues first.")
        try:
            from app.sync import sync_gameweeks_only
            sync_gameweeks_only()  # keep 'finished' flags current before scoring
            return custom_league.sync_points(s, season)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"Failed to fetch points: {e}")


@current_router.get("/fixtures")
def fixtures() -> dict:
    with Session(ENGINE) as s:
        season = _current(s)
        if season is None:
            return {"gameweeks": []}
        a, b = custom_league.collect_divisions(s, season)
        names = {e.entry_id: e.manager_name for e in a + b}
        deadlines = {g.id: g.deadline_time
                     for g in s.exec(select(Gameweek)).all()}
        rows = s.exec(
            select(Fixture).where(Fixture.season_id == season.id).order_by(Fixture.gameweek)).all()
        weeks: dict[int, list] = {}
        for f in rows:
            weeks.setdefault(f.gameweek, []).append({
                "home": names.get(f.home_entry, str(f.home_entry)),
                "away": names.get(f.away_entry, str(f.away_entry)),
                "home_id": f.home_entry, "away_id": f.away_entry,
                "kind": f.kind,
            })
        meta = s.get(LeagueMeta, season.id)
        return {
            "generated_at": meta.fixtures_generated_at if meta else None,
            "gameweeks": [{"gameweek": gw, "deadline": deadlines.get(gw), "matches": m}
                          for gw, m in sorted(weeks.items())],
        }


@current_router.get("/table")
def table() -> dict:
    with Session(ENGINE) as s:
        season = _current(s)
        if season is None:
            return {"combined": [], "division_a": [], "division_b": []}
        try:
            return custom_league.standings(s, season)
        except ValueError:
            return {"combined": [], "division_a": [], "division_b": []}


@current_router.get("/playoffs")
def playoffs() -> dict:
    with Session(ENGINE) as s:
        season = _current(s)
        if season is None:
            return {"ready": False, "reason": "No season yet."}
        try:
            return custom_league.playoffs(s, season)
        except ValueError as e:
            return {"ready": False, "reason": str(e)}
