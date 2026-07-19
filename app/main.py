"""FastAPI app exposing the synced FPL data.

Milestone 1 is read-only: just enough endpoints to confirm real data landed.
Run: uvicorn app.main:app --reload
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, func, select

from app.db import ENGINE, init_db
from app.draft_api import router as draft_router
from app.custom_api import current_router as custom_current_router
from app.custom_api import router as custom_router
from app.mirror_api import router as mirror_router
from app.models import Gameweek, Player, PlayerGameweekStats, Team
from app.scoring_api import router as scoring_router
from app.season_api import router as season_router

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Branksbowl", version="0.1")
_STARTED_AT = datetime.now(timezone.utc)


@app.middleware("http")
async def no_cache_for_frontend(request, call_next):
    """Force the browser to always revalidate index.html/app.js/styles.css.

    Without this, a browser that visited before a deploy can keep serving its
    cached copy indefinitely (no Cache-Control was set), so UI changes silently
    don't show up for returning visitors. `no-cache` still lets ETag/Last-Modified
    conditional requests short-circuit to a cheap 304 when nothing changed.
    """
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.include_router(draft_router)
app.include_router(scoring_router)
app.include_router(season_router)
app.include_router(mirror_router)
app.include_router(custom_router)
app.include_router(custom_current_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # On a fresh (e.g. cloud) database, populate the gameweek finished-flags the
    # league tables need. One API call; ignored if it can't reach FPL at boot.
    try:
        with Session(ENGINE) as s:
            if not s.exec(select(Gameweek).limit(1)).first():
                from app.sync import sync_gameweeks_only
                sync_gameweeks_only()
    except Exception as e:  # never block startup on a network hiccup
        print("startup gameweek sync skipped:", e)


@app.get("/api/gameweeks")
def list_gameweeks() -> list[dict]:
    with Session(ENGINE) as s:
        rows = s.exec(select(Gameweek).order_by(Gameweek.id)).all()
        return [{"id": g.id, "name": g.name, "finished": g.finished} for g in rows]


@app.get("/health")
def health() -> dict:
    with Session(ENGINE) as s:
        return {
            "status": "ok",
            # How long this process has been up - lets the frontend tell a genuine
            # error apart from "Render's free tier just cold-started this request"
            # (a low number here means the container booted moments ago).
            "uptime_seconds": (datetime.now(timezone.utc) - _STARTED_AT).total_seconds(),
            "teams": s.exec(select(func.count()).select_from(Team)).one(),
            "players": s.exec(select(func.count()).select_from(Player)).one(),
            "gameweeks": s.exec(select(func.count()).select_from(Gameweek)).one(),
            "gw_stat_rows": s.exec(select(func.count()).select_from(PlayerGameweekStats)).one(),
        }


@app.get("/teams")
def list_teams() -> list[dict]:
    with Session(ENGINE) as s:
        teams = s.exec(select(Team).order_by(Team.name)).all()
        return [{"id": t.id, "name": t.name, "short_name": t.short_name} for t in teams]


@app.get("/players")
def list_players(
    position: str | None = Query(None, description="GK/DEF/MID/FWD"),
    team_id: int | None = None,
    limit: int = 50,
    order_by_points: bool = True,
) -> list[dict]:
    type_by_pos = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}
    with Session(ENGINE) as s:
        stmt = select(Player, Team.short_name).join(Team, Player.team_id == Team.id)
        if position and position.upper() in type_by_pos:
            stmt = stmt.where(Player.element_type == type_by_pos[position.upper()])
        if team_id is not None:
            stmt = stmt.where(Player.team_id == team_id)
        if order_by_points:
            stmt = stmt.order_by(Player.total_points.desc())
        stmt = stmt.limit(limit)
        rows = s.exec(stmt).all()
        return [
            {
                "id": p.id,
                "name": p.web_name,
                "position": p.position,
                "team": short_name,
                "total_points": p.total_points,
                "goals": p.goals_scored,
                "assists": p.assists,
            }
            for p, short_name in rows
        ]


@app.get("/players/{player_id}/gameweeks")
def player_gameweeks(player_id: int) -> list[dict]:
    with Session(ENGINE) as s:
        rows = s.exec(
            select(PlayerGameweekStats)
            .where(PlayerGameweekStats.player_id == player_id)
            .order_by(PlayerGameweekStats.gameweek_id)
        ).all()
        return [
            {"gameweek": r.gameweek_id, "points": r.total_points, "minutes": r.minutes}
            for r in rows
        ]


# --- web UI (served last so /api and other routes win) -------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    # Browsers request this bare path directly, regardless of the <link> tags.
    return FileResponse(STATIC_DIR / "favicon.ico")


# --- PWA (installable app): manifest + service worker served from root -------
@app.get("/manifest.json")
def manifest() -> JSONResponse:
    # Built dynamically so the installed-app name follows the configured league name.
    from app.settings_models import Setting
    with Session(ENGINE) as s:
        row = s.get(Setting, "league_name")
        name = row.value if row and row.value else "Branksbowl"
    return JSONResponse({
        "name": name, "short_name": name,
        "description": f"{name} - a custom two-division FPL Draft head-to-head league.",
        "start_url": "/", "scope": "/", "display": "standalone", "orientation": "portrait",
        "background_color": "#0f1729", "theme_color": "#0f1729",
        "icons": [
            {"src": "/static/favicon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/favicon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/favicon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    # Served from root so its scope covers the whole site; never cached by HTTP
    # so a new deploy's worker is picked up promptly.
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
