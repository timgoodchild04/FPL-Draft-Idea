"""Ingest a linked FPL Draft league and build its head-to-head table.

H2H scoring: win = 3, draw = 1, loss = 0. Only *finished* matches count, which
is also why an in-progress gameweek never affects the table - it isn't finished
until the site settles bonus points and auto-subs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlmodel import Session, delete, select

from app import fpldraft_client
from app.league_models import Division
from app.mirror_models import MirrorEntry, MirrorLink, MirrorMatch


def _full_name(le: dict) -> str:
    parts = [le.get("player_first_name") or "", le.get("player_last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or (le.get("entry_name") or f"Entry {le.get('id')}")


def ingest_league(session: Session, division_id: int, league_id: int,
                  client: httpx.Client | None = None) -> dict:
    """Fetch a league and (re)load its entries and matches for this division."""
    own = client is None
    client = client or httpx.Client()
    try:
        data = fpldraft_client.fetch_league_details(client, league_id)
    finally:
        if own:
            client.close()

    league_name = (data.get("league") or {}).get("name", "")
    entries = data.get("league_entries", [])
    matches = data.get("matches", [])

    # Replace any prior mirror data for this division (idempotent re-sync).
    session.exec(delete(MirrorMatch).where(MirrorMatch.division_id == division_id))
    session.exec(delete(MirrorEntry).where(MirrorEntry.division_id == division_id))
    session.merge(MirrorLink(division_id=division_id, external_league_id=league_id,
                             league_name=league_name))

    for le in entries:
        session.add(MirrorEntry(
            division_id=division_id,
            league_entry_id=le["id"],
            entry_id=le.get("entry_id"),
            team_name=le.get("entry_name") or "",
            manager_name=_full_name(le),
        ))

    finished_count = 0
    for m in matches:
        fin = bool(m.get("finished"))
        finished_count += 1 if fin else 0
        session.add(MirrorMatch(
            division_id=division_id,
            event=m["event"],
            e1=m["league_entry_1"], e1_points=m.get("league_entry_1_points", 0),
            e2=m["league_entry_2"], e2_points=m.get("league_entry_2_points", 0),
            finished=fin,
        ))
    session.commit()
    return {
        "league_id": league_id, "league_name": league_name,
        "entries": len(entries), "matches": len(matches), "finished_matches": finished_count,
    }


@dataclass
class H2HRow:
    league_entry_id: int
    manager: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    points_for: int = 0
    points_against: int = 0
    h2h_points: int = 0


def compute_h2h(names: dict[int, str], matches: list[dict]) -> list[dict]:
    """Pure H2H table builder. `matches` items: {e1,e1_points,e2,e2_points,finished}.

    Ranked by H2H points, then points-for (the standard FPL Draft tiebreak).
    """
    rows: dict[int, H2HRow] = {eid: H2HRow(eid, name) for eid, name in names.items()}

    def ensure(eid: int) -> H2HRow:
        return rows.setdefault(eid, H2HRow(eid, f"Entry {eid}"))

    for m in matches:
        if not m.get("finished"):
            continue
        a, b = ensure(m["e1"]), ensure(m["e2"])
        pa, pb = m["e1_points"], m["e2_points"]
        a.played += 1; b.played += 1
        a.points_for += pa; a.points_against += pb
        b.points_for += pb; b.points_against += pa
        if pa > pb:
            a.won += 1; b.lost += 1; a.h2h_points += 3
        elif pb > pa:
            b.won += 1; a.lost += 1; b.h2h_points += 3
        else:
            a.drawn += 1; b.drawn += 1; a.h2h_points += 1; b.h2h_points += 1

    table = sorted(rows.values(), key=lambda r: (r.h2h_points, r.points_for), reverse=True)
    out = []
    for rank, r in enumerate(table, start=1):
        out.append({
            "rank": rank, "manager": r.manager, "league_entry_id": r.league_entry_id,
            "played": r.played, "won": r.won, "drawn": r.drawn, "lost": r.lost,
            "points_for": r.points_for, "points_against": r.points_against,
            "h2h_points": r.h2h_points,
        })
    return out


def h2h_standings(session: Session, division: Division,
                  gw_lo: int = 1, gw_hi: int = 38) -> dict:
    link = session.get(MirrorLink, division.id)
    names = {e.league_entry_id: e.manager_name
             for e in session.exec(select(MirrorEntry)
                                   .where(MirrorEntry.division_id == division.id)).all()}
    matches = session.exec(
        select(MirrorMatch).where(
            MirrorMatch.division_id == division.id,
            MirrorMatch.event >= gw_lo, MirrorMatch.event <= gw_hi,
        )
    ).all()
    match_dicts = [{"e1": m.e1, "e1_points": m.e1_points, "e2": m.e2,
                    "e2_points": m.e2_points, "finished": m.finished} for m in matches]
    return {
        "division": division.name,
        "linked_league": link.league_name if link else None,
        "gameweek_range": [gw_lo, gw_hi],
        "table": compute_h2h(names, match_dicts),
    }
