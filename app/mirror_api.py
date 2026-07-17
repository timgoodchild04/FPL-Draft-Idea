"""HTTP API for mirror mode: link a division to a real FPL Draft league and
view its head-to-head table.

Accepts either a league URL/id or a *team* URL - for a team URL we look up the
entry's `league_set` and, if it belongs to exactly one league, link that; if
several, we return the choices for the user to pick.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app import fpldraft_client, mirror
from app.db import ENGINE
from app.league_models import Division
from app.mirror_models import MirrorLink

router = APIRouter(prefix="/api", tags=["mirror"])


class LinkIn(BaseModel):
    url: str                      # a draft league URL/id or a team URL
    league_id: int | None = None  # set to force a specific league (disambiguation)


def _division(s: Session, division_id: int) -> Division:
    d = s.get(Division, division_id)
    if d is None:
        raise HTTPException(404, f"No division {division_id}")
    return d


def _resolve_leagues(client: httpx.Client, url: str) -> list[dict]:
    """Return candidate leagues [{id, name}] from a league or team URL."""
    if "/entry/" in url and "/league/" not in url:
        entry_id = fpldraft_client.parse_entry_id(url)
        pub = fpldraft_client.fetch_entry_public(client, entry_id)
        # `league_set` lives inside the nested "entry" object.
        ids = (pub.get("entry") or {}).get("league_set") or pub.get("league_set") or []
        out = []
        for lid in ids:
            try:
                details = fpldraft_client.fetch_league_details(client, lid)
                out.append({"id": lid, "name": (details.get("league") or {}).get("name", f"League {lid}")})
            except Exception:
                out.append({"id": lid, "name": f"League {lid}"})
        return out
    return [{"id": fpldraft_client.parse_league_id(url), "name": None}]


@router.post("/divisions/{division_id}/link")
def link_league(division_id: int, body: LinkIn) -> dict:
    with Session(ENGINE) as s:
        _division(s, division_id)
        try:
            with httpx.Client() as client:
                if body.league_id is not None:
                    league_id = body.league_id
                else:
                    candidates = _resolve_leagues(client, body.url)
                    if len(candidates) == 0:
                        raise HTTPException(400, "No league found for that URL.")
                    if len(candidates) > 1:
                        return {"status": "choose", "leagues": candidates}
                    league_id = candidates[0]["id"]
                summary = mirror.ingest_league(s, division_id, league_id, client)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"Failed to fetch from FPL Draft: {e}")
        return {"status": "linked", **summary}


@router.get("/divisions/{division_id}/mirror")
def mirror_status(division_id: int) -> dict:
    with Session(ENGINE) as s:
        _division(s, division_id)
        link = s.get(MirrorLink, division_id)
        if not link:
            return {"linked": False}
        return {"linked": True, "league_id": link.external_league_id,
                "league_name": link.league_name}


@router.get("/divisions/{division_id}/h2h")
def h2h(division_id: int, gw_lo: int = 1, gw_hi: int = 38) -> dict:
    with Session(ENGINE) as s:
        div = _division(s, division_id)
        return mirror.h2h_standings(s, div, gw_lo, gw_hi)
