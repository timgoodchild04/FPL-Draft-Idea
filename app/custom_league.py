"""The custom two-division league engine.

Ties a generated schedule to the real managers in the two linked mini-leagues,
pulls each team's weekly FPL points, and computes head-to-head tables from *our*
fixtures (win 3 / draw 1 / loss 0, tie-break = total season points).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlmodel import Session, delete, select

from app import fpldraft_client, schedule
from app.league_models import Division, Season
from app.mirror_models import MirrorEntry
from app.models import Gameweek
from app.schedule_models import EntryPoints, Fixture, Rivalry


@dataclass
class TeamRow:
    entry_id: int
    name: str
    division: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    points_for: int = 0
    points_against: int = 0
    h2h_points: int = 0


# --- gathering the 14 teams ----------------------------------------------
def collect_divisions(session: Session, season: Season) -> tuple[list[MirrorEntry], list[MirrorEntry]]:
    """The two stage-1 divisions' entries as (tier-1 list, tier-2 list)."""
    divs = session.exec(
        select(Division).where(Division.season_id == season.id, Division.stage == 1)
        .order_by(Division.tier)
    ).all()
    if len(divs) != 2:
        raise ValueError("Need exactly two stage-1 divisions (tier 1 and tier 2).")
    out = []
    for d in divs:
        entries = session.exec(
            select(MirrorEntry).where(MirrorEntry.division_id == d.id)
        ).all()
        entries = [e for e in entries if e.entry_id is not None]
        out.append(entries)
    return out[0], out[1]


# --- schedule ------------------------------------------------------------
def generate_and_store_schedule(session: Session, season: Season, seed: int | None = None,
                                allow_regenerate: bool = False) -> dict:
    existing = session.exec(
        select(Fixture).where(Fixture.season_id == season.id).limit(1)).first()
    if existing is not None and not allow_regenerate:
        raise ValueError("Fixtures are already generated and locked for this season.")

    a_entries, b_entries = collect_divisions(session, season)
    if len(a_entries) != len(b_entries):
        raise ValueError(f"Divisions must be equal size (got {len(a_entries)} and {len(b_entries)}).")
    k = len(a_entries)
    if k < 2:
        raise ValueError("Each division needs at least 2 teams.")
    rounds = (k - 1) * 3 + k * 2 + 3

    a_labels = [str(e.entry_id) for e in a_entries]
    b_labels = [str(e.entry_id) for e in b_entries]
    div_of = {e.entry_id: "A" for e in a_entries} | {e.entry_id: "B" for e in b_entries}

    # Load derby pairs (if set) and pass them as guaranteed extra games.
    valid_ids = {e.entry_id for e in a_entries + b_entries}
    derby_pairs = None
    rivalries = session.exec(select(Rivalry).where(Rivalry.season_id == season.id)).all()
    if rivalries and all(r.entry_a in valid_ids and r.entry_b in valid_ids for r in rivalries):
        derby_pairs = [(str(r.entry_a), str(r.entry_b)) for r in rivalries]

    weeks = schedule.generate_schedule(a_labels, b_labels, rounds=rounds, seed=seed,
                                       derby_pairs=derby_pairs)

    session.exec(delete(Fixture).where(Fixture.season_id == season.id))
    for gw, week in enumerate(weeks, start=1):
        for home, away in week:
            h, a = int(home), int(away)
            kind = "division" if div_of[h] == div_of[a] else "cross"
            session.add(Fixture(season_id=season.id, gameweek=gw,
                                home_entry=h, away_entry=a, kind=kind))
    session.commit()
    return {"gameweeks": rounds, "teams": 2 * k, "fixtures": rounds * k}


# --- weekly points -------------------------------------------------------
def sync_points(session: Session, season: Season, client: httpx.Client | None = None) -> dict:
    a_entries, b_entries = collect_divisions(session, season)
    entries = a_entries + b_entries
    own = client is None
    client = client or httpx.Client()
    session.exec(delete(EntryPoints).where(EntryPoints.season_id == season.id))
    rows = 0
    failed: list[int] = []
    try:
        for e in entries:
            try:
                hist = fpldraft_client.fetch_entry_history(client, e.entry_id)
            except Exception:
                failed.append(e.entry_id)  # one bad id shouldn't zero the whole league
                continue
            for ev in hist.get("history", []):
                session.add(EntryPoints(season_id=season.id, entry_id=e.entry_id,
                                        gameweek=ev["event"], points=ev["points"]))
                rows += 1
    finally:
        if own:
            client.close()
    session.commit()
    return {"teams": len(entries), "point_rows": rows, "failed": failed}


# --- H2H computation -----------------------------------------------------
def compute_h2h_from_fixtures(fixtures: list[dict], points: dict[tuple[int, int], int],
                              finished: set[int], names: dict[int, str],
                              divs: dict[int, str]) -> list[TeamRow]:
    """Pure: build the table from fixtures + per-(entry,gw) points.

    Only fixtures whose gameweek is finished and where both teams have a score
    count. Win 3 / draw 1 / loss 0; ranked by H2H points then points-for (which
    equals a team's total season points, since it plays exactly once per week).
    """
    rows: dict[int, TeamRow] = {
        eid: TeamRow(eid, names.get(eid, f"Entry {eid}"), divs.get(eid, "?")) for eid in names}

    for fx in fixtures:
        gw = fx["gameweek"]
        if gw not in finished:
            continue
        h, a = fx["home_entry"], fx["away_entry"]
        if (h, gw) not in points or (a, gw) not in points:
            continue
        ph, pa = points[(h, gw)], points[(a, gw)]
        rh, ra = rows[h], rows[a]
        rh.played += 1; ra.played += 1
        rh.points_for += ph; rh.points_against += pa
        ra.points_for += pa; ra.points_against += ph
        if ph > pa:
            rh.won += 1; ra.lost += 1; rh.h2h_points += 3
        elif pa > ph:
            ra.won += 1; rh.lost += 1; ra.h2h_points += 3
        else:
            rh.drawn += 1; ra.drawn += 1; rh.h2h_points += 1; ra.h2h_points += 1

    return sorted(rows.values(), key=lambda r: (r.h2h_points, r.points_for), reverse=True)


def _row_dict(rank: int, r: TeamRow) -> dict:
    return {"rank": rank, "manager": r.name, "entry_id": r.entry_id, "division": r.division,
            "played": r.played, "won": r.won, "drawn": r.drawn, "lost": r.lost,
            "points_for": r.points_for, "points_against": r.points_against,
            "h2h_points": r.h2h_points}


def standings(session: Session, season: Season) -> dict:
    a_entries, b_entries = collect_divisions(session, season)
    names = {e.entry_id: e.manager_name for e in a_entries + b_entries}
    divs = {e.entry_id: "A" for e in a_entries} | {e.entry_id: "B" for e in b_entries}

    fixtures = [{"gameweek": f.gameweek, "home_entry": f.home_entry, "away_entry": f.away_entry}
                for f in session.exec(select(Fixture).where(Fixture.season_id == season.id)).all()]
    points = {(p.entry_id, p.gameweek): p.points
              for p in session.exec(select(EntryPoints).where(EntryPoints.season_id == season.id)).all()}
    finished = {g.id for g in session.exec(select(Gameweek).where(Gameweek.finished == True)).all()}  # noqa: E712

    table = compute_h2h_from_fixtures(fixtures, points, finished, names, divs)
    combined = [_row_dict(i, r) for i, r in enumerate(table, start=1)]
    by_div = {}
    for d in ("A", "B"):
        rows = [r for r in table if r.division == d]
        by_div[d] = [_row_dict(i, r) for i, r in enumerate(rows, start=1)]
    return {"combined": combined, "division_a": by_div["A"], "division_b": by_div["B"],
            "fixtures_total": len(fixtures)}


# --- playoffs ------------------------------------------------------------
SEMI_GWS = [36, 37]   # semi-finals aggregated over these gameweeks
FINAL_GW = 38         # final decided on this gameweek


def playoffs(session: Session, season: Season) -> dict:
    """Top-4 knockout: semis (1v4, 2v3) aggregated over GW36-37, final on GW38.

    Seeded from the combined regular-season table. Ties in a tie broken by the
    higher seed (which reflects total season points, the agreed tie-break).
    """
    combined = standings(session, season)["combined"]
    if len(combined) < 4:
        return {"ready": False, "reason": "Need at least 4 teams with a generated schedule."}
    seeds = combined[:4]  # ranked rows: rank, manager, entry_id, division

    points = {(p.entry_id, p.gameweek): p.points
              for p in session.exec(select(EntryPoints).where(EntryPoints.season_id == season.id)).all()}
    finished = {g.id for g in session.exec(select(Gameweek).where(Gameweek.finished == True)).all()}  # noqa: E712

    def score(entry_id: int, gws: list[int]) -> tuple[int, bool]:
        done = all(gw in finished for gw in gws)
        total = sum(points.get((entry_id, gw), 0) for gw in gws)
        return total, done

    def tie(high: dict, low: dict, gws: list[int]) -> dict:
        hp, hd = score(high["entry_id"], gws)
        lp, ld = score(low["entry_id"], gws)
        done = hd and ld
        winner = None
        if done:
            winner = high if hp >= lp else low  # equal -> higher seed (high)
        return {"high_seed": high, "low_seed": low, "gameweeks": gws,
                "high_points": hp, "low_points": lp,
                "status": "complete" if done else "pending", "winner": winner}

    sf1 = tie(seeds[0], seeds[3], SEMI_GWS)   # #1 v #4
    sf2 = tie(seeds[1], seeds[2], SEMI_GWS)   # #2 v #3

    final = {"gameweeks": [FINAL_GW], "status": "pending",
             "team_a": None, "team_b": None, "winner": None}
    champion = None
    if sf1["winner"] and sf2["winner"]:
        a, b = sf1["winner"], sf2["winner"]
        ap, ad = score(a["entry_id"], [FINAL_GW])
        bp, bd = score(b["entry_id"], [FINAL_GW])
        done = ad and bd
        winner = None
        if done:
            winner = a if ap > bp else b if bp > ap else (a if a["rank"] < b["rank"] else b)
        final = {"gameweeks": [FINAL_GW], "team_a": a, "team_b": b,
                 "a_points": ap, "b_points": bp,
                 "status": "complete" if done else "pending", "winner": winner}
        champion = winner

    return {"ready": True, "seeds": seeds,
            "semis": [dict(name="Semi-final 1", **sf1), dict(name="Semi-final 2", **sf2)],
            "final": final, "champion": champion}
