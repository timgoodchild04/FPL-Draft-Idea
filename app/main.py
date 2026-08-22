"""FastAPI app entry point - mounts the custom-league API and serves the web UI.

Run: uvicorn app.main:app --reload
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, func, select

from app.db import ENGINE, init_db
from app.custom_api import current_router as custom_current_router
from app.custom_api import router as custom_router
from app.models import Gameweek, Player, PlayerGameweekStats, Team

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
    # Separately from the above: teams/players are needed to resolve names and
    # positions in the fixture-lineup view. Checked independently of Gameweek -
    # that table can already be populated (e.g. via /refresh) while this one
    # is still empty, which left production silently showing "Player 123" /
    # "?" for everyone instead of real names.
    try:
        with Session(ENGINE) as s:
            if not s.exec(select(Player).limit(1)).first():
                from app.sync import run_sync
                run_sync(with_stats=False)
    except Exception as e:
        print("startup reference sync skipped:", e)


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
