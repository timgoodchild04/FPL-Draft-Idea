"""Scoring: turn a manager's roster + FPL per-gameweek points into results.

Draft-mode scoring (no captain): a manager scores the sum of their starting XI's
points for the gameweek, with automatic substitutions - any starter who played 0
minutes is replaced, in bench order, by a bench player who played, provided the
formation stays valid (1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.league_models import Division, DivisionMembership, Manager, RosterSlot, Season
from app.lineup_models import FORMATION_BOUNDS, Lineup
from app.models import POSITION_BY_TYPE, Player, PlayerGameweekStats


@dataclass
class SquadPlayer:
    player_id: int
    name: str
    position: str
    season_points: int


def _roster(session: Session, division_id: int, manager_id: int) -> list[SquadPlayer]:
    rows = session.exec(
        select(Player)
        .join(RosterSlot, RosterSlot.player_id == Player.id)
        .where(RosterSlot.division_id == division_id, RosterSlot.manager_id == manager_id)
    ).all()
    return [
        SquadPlayer(p.id, p.web_name, POSITION_BY_TYPE[p.element_type], p.total_points)
        for p in rows
    ]


def valid_formation(positions: list[str]) -> bool:
    if len(positions) != 11:
        return False
    c = Counter(positions)
    return all(lo <= c.get(pos, 0) <= hi for pos, (lo, hi) in FORMATION_BOUNDS.items())


def default_lineup(squad: list[SquadPlayer]) -> tuple[list[SquadPlayer], list[SquadPlayer]]:
    """Pick a valid best-XI by season points; return (starters, bench_in_order).

    Bench order: outfield by descending season points, then the reserve GK last.
    """
    by_pos: dict[str, list[SquadPlayer]] = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in squad:
        by_pos[p.position].append(p)
    for lst in by_pos.values():
        lst.sort(key=lambda p: p.season_points, reverse=True)

    starters: list[SquadPlayer] = [by_pos["GK"][0]]  # best GK
    # Take formation minimums first.
    for pos in ("DEF", "MID", "FWD"):
        starters.extend(by_pos[pos][: FORMATION_BOUNDS[pos][0]])

    # Fill the remaining outfield slots by points, respecting per-position maxima.
    chosen = {p.player_id for p in starters}
    counts = Counter(p.position for p in starters)
    pool = sorted(
        (p for p in squad if p.position != "GK" and p.player_id not in chosen),
        key=lambda p: p.season_points,
        reverse=True,
    )
    for p in pool:
        if len(starters) == 11:
            break
        if counts[p.position] < FORMATION_BOUNDS[p.position][1]:
            starters.append(p)
            counts[p.position] += 1
            chosen.add(p.player_id)

    bench_outfield = sorted(
        (p for p in squad if p.position != "GK" and p.player_id not in chosen),
        key=lambda p: p.season_points,
        reverse=True,
    )
    bench_gk = [p for p in by_pos["GK"][1:]]
    return starters, bench_outfield + bench_gk


def _resolve_lineup(
    session: Session, division_id: int, manager_id: int, gameweek_id: int
) -> tuple[list[SquadPlayer], list[SquadPlayer]]:
    """Use an explicit lineup if one was set for this gameweek, else the default."""
    rows = session.exec(
        select(Lineup).where(
            Lineup.division_id == division_id,
            Lineup.manager_id == manager_id,
            Lineup.gameweek_id == gameweek_id,
        )
    ).all()
    squad = {p.player_id: p for p in _roster(session, division_id, manager_id)}
    if not rows:
        return default_lineup(list(squad.values()))
    starters = [squad[r.player_id] for r in rows if r.is_starter and r.player_id in squad]
    bench = [
        squad[r.player_id]
        for r in sorted(
            (r for r in rows if not r.is_starter and r.player_id in squad),
            key=lambda r: r.bench_order if r.bench_order is not None else 99,
        )
    ]
    return starters, bench


@dataclass
class GameweekResult:
    manager_id: int
    gameweek_id: int
    points: int
    starters_used: list[str] = field(default_factory=list)
    subs_made: list[str] = field(default_factory=list)


def _gw_stats(session: Session, gameweek_id: int, player_ids: list[int]) -> dict[int, tuple[int, int]]:
    """player_id -> (minutes, points) for a gameweek. Missing => (0, 0)."""
    if not player_ids:
        return {}
    rows = session.exec(
        select(PlayerGameweekStats).where(
            PlayerGameweekStats.gameweek_id == gameweek_id,
            PlayerGameweekStats.player_id.in_(player_ids),
        )
    ).all()
    return {r.player_id: (r.minutes, r.total_points) for r in rows}


def apply_auto_subs(
    starters: list[SquadPlayer],
    bench: list[SquadPlayer],
    stats: dict[int, tuple[int, int]],
) -> tuple[list[SquadPlayer], list[str]]:
    """Pure auto-sub logic. Returns (final XI, human-readable sub descriptions).

    stats maps player_id -> (minutes, points); missing => (0, 0). A bench player
    who played replaces the first non-playing starter whose swap keeps a valid
    formation (this naturally enforces GK-for-GK and outfield-for-outfield).
    """
    def played(p: SquadPlayer) -> bool:
        return stats.get(p.player_id, (0, 0))[0] > 0

    xi = list(starters)
    subs: list[str] = []
    for bp in bench:
        if not played(bp):
            continue
        for i, sp in enumerate(xi):
            if played(sp):
                continue
            candidate = [q.position for q in xi[:i]] + [bp.position] + [q.position for q in xi[i + 1:]]
            if valid_formation(candidate):
                subs.append(f"{bp.name} in for {sp.name}")
                xi[i] = bp
                break
    return xi, subs


def score_gameweek(
    session: Session, division_id: int, manager_id: int, gameweek_id: int
) -> GameweekResult:
    starters, bench = _resolve_lineup(session, division_id, manager_id, gameweek_id)
    all_ids = [p.player_id for p in starters + bench]
    stats = _gw_stats(session, gameweek_id, all_ids)

    xi, subs = apply_auto_subs(starters, bench, stats)
    total = sum(stats.get(p.player_id, (0, 0))[1] for p in xi)
    return GameweekResult(
        manager_id=manager_id,
        gameweek_id=gameweek_id,
        points=total,
        starters_used=[p.name for p in xi],
        subs_made=subs,
    )


def stage_gameweeks(session: Session, season: Season, stage: int) -> list[int]:
    """Finished gameweek ids that belong to a stage (1 = up to split, 2 = after)."""
    from app.models import Gameweek

    gws = session.exec(select(Gameweek).where(Gameweek.finished == True)).all()  # noqa: E712
    if stage == 1:
        ids = [g.id for g in gws if g.id <= season.split_gameweek]
    else:
        ids = [g.id for g in gws if g.id > season.split_gameweek]
    return sorted(ids)


def division_standings(session: Session, division: Division) -> list[dict]:
    season = session.get(Season, division.season_id)
    gw_ids = stage_gameweeks(session, season, division.stage)
    members = session.exec(
        select(DivisionMembership, Manager.name)
        .join(Manager, Manager.id == DivisionMembership.manager_id)
        .where(DivisionMembership.division_id == division.id)
    ).all()

    table = []
    for membership, name in members:
        by_gw = {gw: score_gameweek(session, division.id, membership.manager_id, gw).points
                 for gw in gw_ids}
        table.append({
            "manager_id": membership.manager_id,
            "manager": name,
            "played": len(gw_ids),
            "points": sum(by_gw.values()),
            "gameweek_points": by_gw,
        })
    table.sort(key=lambda r: r["points"], reverse=True)
    for rank, row in enumerate(table, start=1):
        row["rank"] = rank
    return table
