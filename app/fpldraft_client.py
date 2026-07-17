"""Client for the (unofficial) FPL *Draft* API at draft.premierleague.com.

Separate from fpl_client.py, which targets the classic game. These endpoints
are public (no auth) for reading league and entry data.
"""
from __future__ import annotations

import re

import httpx

BASE = "https://draft.premierleague.com/api"
HEADERS = {"User-Agent": "fpl-draft-league/0.1 (local private league tool)"}


def _get(client: httpx.Client, path: str) -> dict:
    resp = client.get(f"{BASE}{path}", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_league_details(client: httpx.Client, league_id: int) -> dict:
    """League entries, standings, and the full H2H match schedule/results."""
    return _get(client, f"/league/{league_id}/details")


def fetch_entry_history(client: httpx.Client, entry_id: int) -> dict:
    """A single entry's per-gameweek points history."""
    return _get(client, f"/entry/{entry_id}/history")


def fetch_entry_public(client: httpx.Client, entry_id: int) -> dict:
    """Public entry info, including `league_set` (the leagues it belongs to)."""
    return _get(client, f"/entry/{entry_id}/public")


def parse_league_id(url_or_id: str) -> int:
    """Accept a raw id, or a draft URL like .../league/12345/standings."""
    s = str(url_or_id).strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"/league/(\d+)", s)
    if m:
        return int(m.group(1))
    # Fall back to the last number anywhere in the string.
    nums = re.findall(r"\d+", s)
    if nums:
        return int(nums[-1])
    raise ValueError(f"Could not find a league id in {url_or_id!r}")


def parse_entry_id(url_or_id: str) -> int:
    """Accept a raw id, or a team URL like .../entry/98765/event/3."""
    s = str(url_or_id).strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"/entry/(\d+)", s)
    if m:
        return int(m.group(1))
    raise ValueError(f"Could not find an entry id in {url_or_id!r}")
