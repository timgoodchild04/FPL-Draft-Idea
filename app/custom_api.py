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
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, delete, select

from app import custom_league, fpldraft_client, setup_league
from app.db import ENGINE
from app.league_models import Division, DivisionMembership, DraftPick, RosterSlot, Season
from app.lineup_models import Lineup
from app.mirror_models import MirrorEntry, MirrorLink, MirrorMatch
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


def _resolve_season(s: Session, season_id: int | None) -> Season | None:
    """Explicit season_id (e.g. picking a past season) or else the current one."""
    return _season(s, season_id) if season_id is not None else _current(s)


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
        "points_synced_at": meta.points_synced_at if meta else None,
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
def status(season_id: int | None = None) -> dict:
    with Session(ENGINE) as s:
        season = _resolve_season(s, season_id)
        if season is None:
            return {"has_season": False}
        return _status(s, season)


@current_router.get("/seasons")
def list_seasons() -> list[dict]:
    """Every season ever created, newest first - powers the season picker."""
    with Session(ENGINE) as s:
        cur = _current(s)
        rows = s.exec(select(Season).order_by(Season.id.desc())).all()
        return [{"id": sn.id, "name": sn.name, "archived_at": sn.archived_at,
                 "is_current": cur is not None and sn.id == cur.id} for sn in rows]


class NewSeasonIn(BaseModel):
    name: str = ""


@current_router.post("/seasons/new")
def new_season(body: NewSeasonIn = NewSeasonIn(), _admin: bool = Depends(require_admin)) -> dict:
    """Archive the current season (read-only forever after) and start a fresh one."""
    with Session(ENGINE) as s:
        season = _current(s)
        has_fixtures = season is not None and s.exec(
            select(Fixture).where(Fixture.season_id == season.id).limit(1)).first() is not None
        if not has_fixtures:
            raise HTTPException(400, "Generate this season's fixtures before starting a new one.")
        season.archived_at = datetime.now(timezone.utc).isoformat()
        s.add(season)
        s.commit()
        name = body.name.strip() or "Draft League"
        new = setup_league.create_season(s, name, split_gameweek=35)
        _ensure_divisions(s, new)
        return _status(s, new)


def _purge_season(s: Session, season: Season) -> None:
    """Permanently delete every row belonging to a season, including the season
    itself. Children are cleared before their parents (all via bulk deletes, in
    order) so this is safe under Postgres' enforced foreign keys - SQLite doesn't
    enforce them, so a partial delete only shows up once hosted."""
    div_ids = [d.id for d in
               s.exec(select(Division).where(Division.season_id == season.id)).all()]
    if div_ids:
        for tbl in (MirrorEntry, MirrorLink, MirrorMatch,
                    DivisionMembership, DraftPick, RosterSlot, Lineup):
            s.exec(delete(tbl).where(tbl.division_id.in_(div_ids)))
    s.exec(delete(Division).where(Division.season_id == season.id))
    s.exec(delete(Fixture).where(Fixture.season_id == season.id))
    s.exec(delete(EntryPoints).where(EntryPoints.season_id == season.id))
    s.exec(delete(Rivalry).where(Rivalry.season_id == season.id))
    s.exec(delete(LeagueMeta).where(LeagueMeta.season_id == season.id))
    s.exec(delete(Season).where(Season.id == season.id))


@current_router.delete("/seasons/{season_id}")
def delete_season(season_id: int, _admin: bool = Depends(require_admin)) -> dict:
    """Permanently delete an archived season and all its data. Can't touch the
    current season this way - archive it (Start new season) first."""
    with Session(ENGINE) as s:
        season = s.get(Season, season_id)
        if season is None:
            raise HTTPException(404, f"No season {season_id}")
        if not season.archived_at:
            raise HTTPException(400, "Can't delete the current season - start a new one first "
                                     "to archive it, then delete it.")
        _purge_season(s, season)
        s.commit()
        return {"deleted": season_id}


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
            raise HTTPException(400, "Fixtures already generated and locked - start a new season to change teams.")
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
            raise HTTPException(400, "Fixtures already generated and locked - start a new season to change rivalries.")
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


REFRESH_STALE_SECONDS = 1800       # 30 minutes, normally
REFRESH_STALE_SECONDS_LIVE = 180   # 3 minutes, while a gameweek is actually in play


@current_router.post("/refresh")
def refresh() -> dict:
    """Public, throttled: re-pull results only if the last sync is stale.

    Cheap no-op when fresh, so it's safe to call on every page load without
    hammering FPL. Not admin-gated - viewers keep the data current just by visiting.
    Refreshes much more often while a gameweek is live, since that's when scores
    are actually moving.
    """
    with Session(ENGINE) as s:
        season = _current(s)
        if season is None:
            return {"synced": False, "last_updated": None}
        meta = s.get(LeagueMeta, season.id)
        last = meta.points_synced_at if meta else None
        live = s.exec(select(Gameweek).where(
            Gameweek.is_current == True, Gameweek.finished == False)).first() is not None  # noqa: E712
        stale_after = REFRESH_STALE_SECONDS_LIVE if live else REFRESH_STALE_SECONDS
        if last:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
                if age < stale_after:
                    return {"synced": False, "last_updated": last}
            except Exception:
                pass
        try:
            from app.sync import sync_gameweeks_only
            sync_gameweeks_only()
            custom_league.sync_points(s, season)
        except Exception:
            return {"synced": False, "last_updated": last}
        meta = s.get(LeagueMeta, season.id)
        return {"synced": True, "last_updated": meta.points_synced_at if meta else None}


@current_router.get("/fixtures")
def fixtures(season_id: int | None = None) -> dict:
    with Session(ENGINE) as s:
        season = _resolve_season(s, season_id)
        if season is None:
            return {"gameweeks": []}
        a, b = custom_league.collect_divisions(s, season)
        names = {e.entry_id: e.manager_name for e in a + b}
        gwmeta = {g.id: g for g in s.exec(select(Gameweek)).all()}
        pts = {(p.entry_id, p.gameweek): p.points
               for p in s.exec(select(EntryPoints).where(EntryPoints.season_id == season.id)).all()}
        rows = s.exec(
            select(Fixture).where(Fixture.season_id == season.id).order_by(Fixture.gameweek)).all()
        weeks: dict[int, list] = {}
        for f in rows:
            weeks.setdefault(f.gameweek, []).append({
                "home": names.get(f.home_entry, str(f.home_entry)),
                "away": names.get(f.away_entry, str(f.away_entry)),
                "home_id": f.home_entry, "away_id": f.away_entry,
                "home_points": pts.get((f.home_entry, f.gameweek)),
                "away_points": pts.get((f.away_entry, f.gameweek)),
                "kind": f.kind,
            })

        def gw_status(gw: int) -> str:
            g = gwmeta.get(gw)
            if g is None:
                return "upcoming"
            if g.finished:
                return "finished"
            return "current" if g.is_current else "upcoming"

        meta = s.get(LeagueMeta, season.id)
        return {
            "generated_at": meta.fixtures_generated_at if meta else None,
            "last_updated": meta.points_synced_at if meta else None,
            "gameweeks": [{"gameweek": gw,
                           "deadline": gwmeta[gw].deadline_time if gw in gwmeta else None,
                           "status": gw_status(gw), "matches": m}
                          for gw, m in sorted(weeks.items())],
        }


@current_router.get("/table")
def table(season_id: int | None = None) -> dict:
    with Session(ENGINE) as s:
        season = _resolve_season(s, season_id)
        if season is None:
            return {"combined": [], "division_a": [], "division_b": []}
        meta = s.get(LeagueMeta, season.id)
        last = meta.points_synced_at if meta else None
        try:
            return {**custom_league.standings(s, season), "last_updated": last}
        except ValueError:
            return {"combined": [], "division_a": [], "division_b": [], "last_updated": last}


@current_router.get("/playoffs")
def playoffs(season_id: int | None = None) -> dict:
    with Session(ENGINE) as s:
        season = _resolve_season(s, season_id)
        if season is None:
            return {"ready": False, "reason": "No season yet."}
        try:
            return custom_league.playoffs(s, season)
        except ValueError as e:
            return {"ready": False, "reason": str(e)}


@current_router.get("/records")
def records() -> dict:
    with Session(ENGINE) as s:
        return custom_league.league_records(s)


@current_router.get("/trophies")
def trophies() -> list[dict]:
    with Session(ENGINE) as s:
        return custom_league.trophy_cabinet(s)


@current_router.get("/manager/{entry_id}")
def manager_profile(entry_id: int) -> dict:
    with Session(ENGINE) as s:
        profile = custom_league.manager_profile(s, entry_id)
        if profile is None:
            raise HTTPException(404, f"No manager with entry id {entry_id}.")
        return profile


@current_router.get("/export")
def export_data(_admin: bool = Depends(require_admin)) -> dict:
    """Full dump of every season's league data - teams, fixtures, results,
    rivalries - across the whole history of this app, not just the current
    season. This is the part of the site that can't be regenerated: the FPL
    reference data (players/teams/gameweeks) re-syncs on its own from the
    official API, but nobody else has a copy of your own draft/fixtures/
    results. Save this somewhere safe as a backup against the site breaking
    or needing to be re-hosted."""
    with Session(ENGINE) as s:
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "seasons": [row.model_dump() for row in s.exec(select(Season)).all()],
            "divisions": [row.model_dump() for row in s.exec(select(Division)).all()],
            "mirror_entries": [row.model_dump() for row in s.exec(select(MirrorEntry)).all()],
            "mirror_links": [row.model_dump() for row in s.exec(select(MirrorLink)).all()],
            "fixtures": [row.model_dump() for row in s.exec(select(Fixture)).all()],
            "entry_points": [row.model_dump() for row in s.exec(select(EntryPoints)).all()],
            "rivalries": [row.model_dump() for row in s.exec(select(Rivalry)).all()],
            "league_meta": [row.model_dump() for row in s.exec(select(LeagueMeta)).all()],
        }


class ImportIn(BaseModel):
    seasons: list[dict] = []
    divisions: list[dict] = []
    mirror_entries: list[dict] = []
    mirror_links: list[dict] = []
    fixtures: list[dict] = []
    entry_points: list[dict] = []
    rivalries: list[dict] = []
    league_meta: list[dict] = []


def _resync_pg_sequences(s: Session) -> None:
    """After inserting rows with explicit (imported) primary keys, Postgres'
    auto-increment sequences don't know those ids were used - the next normal
    insert could then collide with one of them. SQLite doesn't need this (it
    derives the next rowid from MAX(id) itself), so only run it on Postgres."""
    if ENGINE.dialect.name != "postgresql":
        return
    for table, id_col in [("season", "id"), ("division", "id"), ("mirrorentry", "id"),
                          ("fixture", "id"), ("entrypoints", "id"), ("rivalry", "id")]:
        s.exec(text(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{id_col}'), "
            f"COALESCE((SELECT MAX({id_col}) FROM {table}), 1))"
        ))


@current_router.post("/import")
def import_data(body: ImportIn, _admin: bool = Depends(require_admin)) -> dict:
    """Restore a full export, REPLACING every season currently stored - this is
    disaster recovery (re-hosting from scratch), not a merge. All rows are
    validated (constructed into their real model types) before anything is
    touched, so a malformed file is rejected without wiping existing data. The
    wipe-and-restore itself runs as one transaction, so a failure partway
    through rolls back completely rather than leaving things half-restored."""
    if not body.seasons:
        raise HTTPException(400, "This doesn't look like a Branksbowl export - no seasons found.")
    try:
        seasons = [Season(**row) for row in body.seasons]
        divisions = [Division(**row) for row in body.divisions]
        mirror_entries = [MirrorEntry(**row) for row in body.mirror_entries]
        mirror_links = [MirrorLink(**row) for row in body.mirror_links]
        fixtures = [Fixture(**row) for row in body.fixtures]
        entry_points = [EntryPoints(**row) for row in body.entry_points]
        rivalries = [Rivalry(**row) for row in body.rivalries]
        league_meta = [LeagueMeta(**row) for row in body.league_meta]
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"Malformed export file: {e}")

    with Session(ENGINE) as s:
        try:
            for model in (MirrorEntry, MirrorLink, Fixture, EntryPoints, Rivalry, LeagueMeta, Division, Season):
                s.exec(delete(model))
            for row in seasons:
                s.add(row)
            s.flush()  # parents hit the DB (within this same transaction) before children reference them
            for row in divisions:
                s.add(row)
            s.flush()
            for row in (*mirror_entries, *mirror_links):
                s.add(row)
            s.flush()
            for row in (*fixtures, *entry_points, *rivalries, *league_meta):
                s.add(row)
            s.flush()
            _resync_pg_sequences(s)
            s.commit()  # single commit - anything failing above rolls back the whole restore
        except Exception as e:
            s.rollback()
            raise HTTPException(400, f"Import failed, nothing was changed: {e}")

    return {"imported": True, "seasons": len(seasons), "divisions": len(divisions),
            "fixtures": len(fixtures), "entry_points": len(entry_points)}
