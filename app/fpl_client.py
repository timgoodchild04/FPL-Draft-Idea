"""Thin client over the (unofficial) public FPL API.

Only the read endpoints we need. No auth required for these.
"""
from __future__ import annotations

import time

import httpx

BASE = "https://fantasy.premierleague.com/api"
# A browser-ish UA avoids the occasional bot block on these endpoints.
HEADERS = {"User-Agent": "fpl-draft-league/0.1 (local private league tool)"}

# Short-lived cache for the two endpoints the live fixture-lineup/score view
# hits on every page load while a gameweek's in progress. Several viewers
# loading the site within the same few seconds shouldn't each trigger their
# own round trip to FPL for the same gameweek's data.
CACHE_TTL_SECONDS = 30
_live_cache: dict[int, tuple[float, dict]] = {}
_fixtures_cache: dict[int, tuple[float, list]] = {}


def _get(client: httpx.Client, path: str) -> dict:
    resp = client.get(f"{BASE}{path}", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_bootstrap(client: httpx.Client) -> dict:
    """Teams, players (elements), gameweeks (events), position types."""
    return _get(client, "/bootstrap-static/")


def fetch_gameweek_live(client: httpx.Client, gw: int) -> dict:
    """Per-player stats and points for a single finished/in-play gameweek."""
    cached = _live_cache.get(gw)
    now = time.monotonic()
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    data = _get(client, f"/event/{gw}/live/")
    _live_cache[gw] = (now, data)
    return data


def fetch_fixtures(client: httpx.Client, gw: int) -> list[dict]:
    """Real Premier League fixtures for a gameweek - kickoff time, started/finished."""
    cached = _fixtures_cache.get(gw)
    now = time.monotonic()
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    data = _get(client, f"/fixtures/?event={gw}")
    _fixtures_cache[gw] = (now, data)
    return data
