"""The custom two-division league engine.

Ties a generated schedule to the real managers in the two linked mini-leagues,
pulls each team's weekly FPL points, and computes head-to-head tables from *our*
fixtures (win 3 / draw 1 / loss 0, tie-break = total season points).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, delete, select

from app import fpldraft_client, schedule
from app.league_models import Division, Season
from app.mirror_models import MirrorEntry
from app.models import Gameweek
from app.schedule_models import EntryPoints, Fixture, LeagueMeta, Rivalry


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
    meta = session.get(LeagueMeta, season.id) or LeagueMeta(season_id=season.id)
    meta.fixtures_generated_at = datetime.now(timezone.utc).isoformat()
    session.add(meta)
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
    meta = session.get(LeagueMeta, season.id) or LeagueMeta(season_id=season.id)
    meta.points_synced_at = datetime.now(timezone.utc).isoformat()
    session.add(meta)
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


def finished_gameweeks(session: Session, season: Season) -> set[int]:
    """Which gameweek ids count as 'finished' for this season's tables.

    For the active season this mirrors the live Gameweek.finished flags. An
    archived season instead snapshots every gameweek it ever synced points for -
    Gameweek ids are reused every real-world FPL season (upserted in app.sync),
    so trusting the live table forever would let a future season's in-progress
    gameweeks silently un-finish an old, closed-out season's fixtures.
    """
    if season.archived_at:
        return {p.gameweek for p in session.exec(
            select(EntryPoints).where(EntryPoints.season_id == season.id)).all()}
    return {g.id for g in session.exec(select(Gameweek).where(Gameweek.finished == True)).all()}  # noqa: E712


def standings(session: Session, season: Season) -> dict:
    a_entries, b_entries = collect_divisions(session, season)
    names = {e.entry_id: e.manager_name for e in a_entries + b_entries}
    divs = {e.entry_id: "A" for e in a_entries} | {e.entry_id: "B" for e in b_entries}

    fixtures = [{"gameweek": f.gameweek, "home_entry": f.home_entry, "away_entry": f.away_entry}
                for f in session.exec(select(Fixture).where(Fixture.season_id == season.id)).all()]
    points = {(p.entry_id, p.gameweek): p.points
              for p in session.exec(select(EntryPoints).where(EntryPoints.season_id == season.id)).all()}
    finished = finished_gameweeks(session, season)

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
    finished = finished_gameweeks(session, season)

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


# --- head-to-head history --------------------------------------------------
def all_manager_names(session: Session) -> dict[int, str]:
    """entry_id -> manager_name, from every MirrorEntry ever recorded.

    MirrorEntry.entry_id is the durable, global FPL Draft team id (see
    mirror_models.py) - the same identity a manager keeps across seasons - so
    this is the right key for anything spanning season boundaries.
    """
    rows = session.exec(
        select(MirrorEntry).where(MirrorEntry.entry_id != None).order_by(MirrorEntry.id)  # noqa: E711
    ).all()
    return {e.entry_id: e.manager_name for e in rows}  # later rows win on repeats


def head_to_head(session: Session, entry_a: int, entry_b: int) -> dict:
    """All-time record between two managers, across every season they've met in."""
    names = all_manager_names(session)
    a_wins = b_wins = draws = 0
    meetings: list[dict] = []

    for season in session.exec(select(Season).order_by(Season.id)).all():
        fixtures = session.exec(
            select(Fixture).where(
                Fixture.season_id == season.id,
                Fixture.home_entry.in_([entry_a, entry_b]),
                Fixture.away_entry.in_([entry_a, entry_b]),
            )
        ).all()
        if not fixtures:
            continue
        finished = finished_gameweeks(session, season)
        points = {
            (p.entry_id, p.gameweek): p.points
            for p in session.exec(
                select(EntryPoints).where(
                    EntryPoints.season_id == season.id,
                    EntryPoints.entry_id.in_([entry_a, entry_b]),
                )
            ).all()
        }
        for f in fixtures:
            if f.gameweek not in finished:
                continue
            if (f.home_entry, f.gameweek) not in points or (f.away_entry, f.gameweek) not in points:
                continue
            ph, pa = points[(f.home_entry, f.gameweek)], points[(f.away_entry, f.gameweek)]
            a_pts, b_pts = (ph, pa) if f.home_entry == entry_a else (pa, ph)
            if a_pts > b_pts:
                a_wins += 1
            elif b_pts > a_pts:
                b_wins += 1
            else:
                draws += 1
            meetings.append({"season_id": season.id, "season_name": season.name,
                             "gameweek": f.gameweek, "a_points": a_pts, "b_points": b_pts})

    meetings.sort(key=lambda m: (m["season_id"], m["gameweek"]), reverse=True)
    return {
        "a": {"entry_id": entry_a, "name": names.get(entry_a, f"Entry {entry_a}")},
        "b": {"entry_id": entry_b, "name": names.get(entry_b, f"Entry {entry_b}")},
        "record": {"a_wins": a_wins, "b_wins": b_wins, "draws": draws},
        "meetings": meetings,
    }


# --- league records / hall of fame -----------------------------------------
def league_records(session: Session) -> dict:
    """Fun stats computed across every season this app has ever tracked.

    Season point totals (most/fewest in a season) only consider archived
    seasons, so an in-progress season with a hot start can't claim a record
    against seasons that played a full schedule.
    """
    names = all_manager_names(session)
    highest_gw = None
    biggest_margin = None
    season_totals: list[dict] = []
    timeline: dict[int, list[tuple[int, int, str]]] = {}

    for season in session.exec(select(Season)).all():
        finished = finished_gameweeks(session, season)
        points = {
            (p.entry_id, p.gameweek): p.points
            for p in session.exec(select(EntryPoints).where(EntryPoints.season_id == season.id)).all()
        }
        totals: dict[int, int] = {}
        for (eid, gw), pts in points.items():
            if gw not in finished:
                continue
            totals[eid] = totals.get(eid, 0) + pts
            if highest_gw is None or pts > highest_gw["points"]:
                highest_gw = {"entry_id": eid, "name": names.get(eid, f"Entry {eid}"),
                             "season_name": season.name, "gameweek": gw, "points": pts}

        if season.archived_at:
            for eid, total in totals.items():
                season_totals.append({"entry_id": eid, "name": names.get(eid, f"Entry {eid}"),
                                      "season_name": season.name, "total": total})

        for f in session.exec(select(Fixture).where(Fixture.season_id == season.id)).all():
            if f.gameweek not in finished:
                continue
            if (f.home_entry, f.gameweek) not in points or (f.away_entry, f.gameweek) not in points:
                continue
            hp, ap = points[(f.home_entry, f.gameweek)], points[(f.away_entry, f.gameweek)]
            margin = abs(hp - ap)
            if margin > 0 and (biggest_margin is None or margin > biggest_margin["margin"]):
                winner, loser = (f.home_entry, f.away_entry) if hp > ap else (f.away_entry, f.home_entry)
                biggest_margin = {
                    "winner_id": winner, "winner": names.get(winner, f"Entry {winner}"),
                    "loser_id": loser, "loser": names.get(loser, f"Entry {loser}"),
                    "season_name": season.name, "gameweek": f.gameweek, "margin": margin,
                    "winner_points": max(hp, ap), "loser_points": min(hp, ap),
                }
            result_h = "W" if hp > ap else "L" if hp < ap else "D"
            result_a = "W" if ap > hp else "L" if ap < hp else "D"
            timeline.setdefault(f.home_entry, []).append((season.id, f.gameweek, result_h))
            timeline.setdefault(f.away_entry, []).append((season.id, f.gameweek, result_a))

    best_win_streak = {"entry_id": None, "name": None, "length": 0}
    best_unbeaten_streak = {"entry_id": None, "name": None, "length": 0}
    for eid, games in timeline.items():
        games.sort(key=lambda g: (g[0], g[1]))
        win_run = unbeaten_run = 0
        for _, _, result in games:
            win_run = win_run + 1 if result == "W" else 0
            unbeaten_run = unbeaten_run + 1 if result in ("W", "D") else 0
            if win_run > best_win_streak["length"]:
                best_win_streak = {"entry_id": eid, "name": names.get(eid, f"Entry {eid}"), "length": win_run}
            if unbeaten_run > best_unbeaten_streak["length"]:
                best_unbeaten_streak = {"entry_id": eid, "name": names.get(eid, f"Entry {eid}"), "length": unbeaten_run}

    season_totals.sort(key=lambda r: r["total"], reverse=True)
    return {
        "highest_gameweek": highest_gw,
        "biggest_margin": biggest_margin,
        "best_win_streak": best_win_streak if best_win_streak["entry_id"] is not None else None,
        "best_unbeaten_streak": best_unbeaten_streak if best_unbeaten_streak["entry_id"] is not None else None,
        "most_points_season": season_totals[0] if season_totals else None,
        "fewest_points_season": min(season_totals, key=lambda r: r["total"]) if season_totals else None,
    }


def trophy_cabinet(session: Session) -> list[dict]:
    """Every archived season's champion (and runner-up), newest first."""
    seasons = session.exec(
        select(Season).where(Season.archived_at != None).order_by(Season.id.desc())  # noqa: E711
    ).all()
    out = []
    for season in seasons:
        try:
            po = playoffs(session, season)
        except ValueError:
            continue
        champ = po.get("champion")
        if not po.get("ready") or not champ:
            continue
        final = po["final"]
        runner_up = final["team_a"] if final["team_a"] and final["team_a"]["entry_id"] != champ["entry_id"] \
            else final["team_b"]
        out.append({
            "season_id": season.id, "season_name": season.name, "archived_at": season.archived_at,
            "champion": {"entry_id": champ["entry_id"], "name": champ["manager"]},
            "runner_up": {"entry_id": runner_up["entry_id"], "name": runner_up["manager"]} if runner_up else None,
        })
    return out
