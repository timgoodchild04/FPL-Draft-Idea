"""Thin client over the (unofficial) public FPL API.

Only the read endpoints we need. No auth required for these.
"""
from __future__ import annotations

import httpx

BASE = "https://fantasy.premierleague.com/api"
# A browser-ish UA avoids the occasional bot block on these endpoints.
HEADERS = {"User-Agent": "fpl-draft-league/0.1 (local private league tool)"}


def _get(client: httpx.Client, path: str) -> dict:
    resp = client.get(f"{BASE}{path}", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_bootstrap(client: httpx.Client) -> dict:
    """Teams, players (elements), gameweeks (events), position types."""
    return _get(client, "/bootstrap-static/")


def fetch_gameweek_live(client: httpx.Client, gw: int) -> dict:
    """Per-player stats and points for a single finished/in-play gameweek."""
    return _get(client, f"/event/{gw}/live/")
