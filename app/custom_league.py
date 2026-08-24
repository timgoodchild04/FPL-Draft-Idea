"""The custom two-division league engine.

Ties a generated schedule to the real managers in the two linked mini-leagues,
pulls each team's weekly FPL points, and computes head-to-head tables from *our*
fixtures (win 3 / draw 1 / loss 0, tie-break = total season points).
"""
from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, delete, select

from app import fpl_client, fpldraft_client, schedule
from app.league_models import Division, Season
from app.mirror_models import MirrorEntry
from app.models import Gameweek, Player, Team
from app.schedule_models import EntryPoints, Fixture, FixtureLineupSnapshot, LeagueMeta, Rivalry
from app.scoring import SquadPlayer, apply_auto_subs


# Teams per division. Rosters can be saved part-filled (ids often arrive a few at
# a time), but fixtures can't be generated until both divisions hit this number.
DIVISION_SIZE = 7


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
    if len(a_entries) != DIVISION_SIZE or len(b_entries) != DIVISION_SIZE:
        raise ValueError(f"Both divisions must be full ({DIVISION_SIZE} teams each) before "
                         f"fixtures can be generated - Division A has {len(a_entries)}, "
                         f"Division B has {len(b_entries)}.")
    k = len(a_entries)
    rounds = (k - 1) * 3 + k * 2 + 3

    a_labels = [str(e.entry_id) for e in a_entries]
    b_labels = [str(e.entry_id) for e in b_entries]
    div_of = {e.entry_id: "A" for e in a_entries} | {e.entry_id: "B" for e in b_entries}

    weeks = schedule.generate_schedule(a_labels, b_labels, rounds=rounds, seed=seed)

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

    # The random draw above fills rounds 1..N; the last regular-season week is
    # added separately so that a season already locked to N weeks can be brought
    # up to the same length without redrawing a single existing fixture.
    append_balanced_week(session, season, seed)
    # The draw decides who plays whom; this decides who's listed first, which the
    # draw would otherwise hand to whoever's entry id sorts lowest, every week.
    rebalance_home_away(session, season, seed)
    total = rounds + 1
    return {"gameweeks": total, "teams": 2 * k, "fixtures": total * k}


def _meeting_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def rebalance_home_away(session: Session, season: Season, seed: int | None = None) -> dict:
    """Even out who gets listed first in a fixture, without moving any fixture.

    The scheduler emits each pair in a canonical order - lexicographic on the
    entry-id *label* - which is stable but not fair: whoever's id happens to
    sort first is listed first in nearly every game they play, and it's a text
    sort, so "14000" loses to "130618". Nothing in the scoring notices (home and
    away are compared symmetrically), but on the fixtures page it reads as a
    pecking order.

    Walks the season in order and hands the top slot to whichever of the two has
    had it less often, so everyone lands within a game of half. Only ever swaps
    the two sides of a fixture - who plays whom, and in which gameweek, is
    untouched.
    """
    rows = session.exec(
        select(Fixture).where(Fixture.season_id == season.id)
        .order_by(Fixture.gameweek, Fixture.id)
    ).all()
    rng = random.Random(season.id if seed is None else seed)
    counts: Counter = Counter()
    swapped = 0
    for f in rows:
        # Tie-break off the canonical pair, never off what's currently stored,
        # so the outcome depends only on the fixture order and the seed - run it
        # twice and you get the same answer instead of reshuffling every side.
        a, b = _meeting_key(f.home_entry, f.away_entry)
        if counts[a] < counts[b]:
            home, away = a, b
        elif counts[b] < counts[a]:
            home, away = b, a
        else:
            home, away = (a, b) if rng.random() < 0.5 else (b, a)
        if home != f.home_entry:
            f.home_entry, f.away_entry = home, away
            session.add(f)
            swapped += 1
        counts[home] += 1
    session.commit()
    tally = sorted(counts.values())
    return {"fixtures": len(rows), "swapped": swapped,
            "listed_first_min": tally[0] if tally else 0,
            "listed_first_max": tally[-1] if tally else 0}


def _perfect_matching(a_ids: list[int], b_ids: list[int], allowed: set[tuple[int, int]],
                      rng: random.Random) -> list[tuple[int, int]] | None:
    """Pair every Division A team with a Division B team using only `allowed`
    pairs (Kuhn's augmenting-path algorithm). None if no perfect matching exists.
    """
    adj = {a: [b for b in b_ids if _meeting_key(a, b) in allowed] for a in a_ids}
    for a in adj:
        rng.shuffle(adj[a])
    taken: dict[int, int] = {}   # b -> a

    def assign(a: int, seen: set[int]) -> bool:
        for b in adj[a]:
            if b in seen:
                continue
            seen.add(b)
            if b not in taken or assign(taken[b], seen):
                taken[b] = a
                return True
        return False

    order = a_ids[:]
    rng.shuffle(order)
    for a in order:
        if not assign(a, set()):
            return None
    return [(a, b) for b, a in taken.items()]


def append_balanced_week(session: Session, season: Season, seed: int | None = None) -> dict:
    """Add one more regular-season gameweek on top of whatever is already there.

    Every existing fixture is left exactly as drawn - this only appends - so a
    schedule that's already locked in (and possibly half-played) can be extended
    without anyone's fixture list changing underneath them.

    The added week is a single cross-division round: each manager faces someone
    from the other division that they've met the fewest times. That makes it the
    most balanced week in the season - unlike the random extras drawn at
    generation time, nobody can land a softer or harder opponent out of it.
    """
    a_entries, b_entries = collect_divisions(session, season)
    if len(a_entries) != len(b_entries) or not a_entries:
        raise ValueError("Both divisions must be the same, non-zero size to extend the schedule.")

    existing = session.exec(select(Fixture).where(Fixture.season_id == season.id)).all()
    if not existing:
        raise ValueError("No fixtures to extend - generate the schedule first.")
    next_gw = max(f.gameweek for f in existing) + 1
    if next_gw >= min(SEMI_GWS):
        raise ValueError(f"GW{next_gw} is a playoff week - the regular season can't run into it.")

    counts: Counter = Counter()
    for f in existing:
        counts[_meeting_key(f.home_entry, f.away_entry)] += 1

    a_ids = [e.entry_id for e in a_entries]
    b_ids = [e.entry_id for e in b_entries]
    cross = [_meeting_key(x, y) for x in a_ids for y in b_ids]
    fewest = min(counts.get(p, 0) for p in cross)

    rng = random.Random(seed)
    pairs = None
    # Ideally everyone plays someone they've met the minimum number of times; if
    # the extras happen to make that impossible, widen the net a meeting at a time.
    for limit in range(fewest, fewest + len(a_ids) + 1):
        pairs = _perfect_matching(a_ids, b_ids, {p for p in cross if counts.get(p, 0) <= limit}, rng)
        if pairs:
            break
    if pairs is None:  # unreachable for equal divisions - every cross pair is allowed by then
        raise RuntimeError("Could not build a balanced extra gameweek.")

    for home, away in pairs:
        session.add(Fixture(season_id=season.id, gameweek=next_gw,
                            home_entry=home, away_entry=away, kind="cross"))
    session.commit()
    return {"gameweek": next_gw, "matches": len(pairs), "met_before": fewest}


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
            # Draft team names (unlike real names) can be changed anytime, so
            # keep the current season's on record fresh here rather than only
            # capturing it once at Setup - this runs every points sync, which
            # is already the site's regular "check for changes" heartbeat.
            try:
                pub = fpldraft_client.fetch_entry_public(client, e.entry_id)
                team = (pub.get("entry") or pub).get("name")
                if team and team != e.team_name:
                    e.team_name = team
                    session.add(e)
            except Exception:
                pass
    finally:
        if own:
            client.close()
    meta = session.get(LeagueMeta, season.id) or LeagueMeta(season_id=season.id)
    meta.points_synced_at = datetime.now(timezone.utc).isoformat()
    session.add(meta)
    session.commit()
    return {"teams": len(entries), "point_rows": rows, "failed": failed}


def live_player_stats(client: httpx.Client, gameweek: int) -> dict[int, tuple[int, int]]:
    """Live per-player (minutes, total_points) for a gameweek, straight from FPL.

    Unlike our own PlayerGameweekStats table (only backfilled once a gameweek
    is *finished* - see app.sync), this works mid-gameweek too, which the
    fixture-lineup view needs to show a running score as a match happens.
    """
    try:
        live = fpl_client.fetch_gameweek_live(client, gameweek)
    except Exception:
        return {}
    return {el["id"]: (el["stats"]["minutes"], el["stats"]["total_points"]) for el in live.get("elements", [])}


def fixture_closed_teams(session: Session, client: httpx.Client, gameweek: int) -> set[int]:
    """Team ids with no playing time left in this gameweek: every fixture
    they have this gameweek has finished, or (a blank gameweek for them)
    they have none at all.

    Used to gate auto-subs - a starter with 0 minutes should only be treated
    as confirmed not to be playing once we know their game is actually over,
    never just because it hasn't kicked off yet. Returns an empty set (no
    team confirmed) if the fixture list can't be fetched or comes back
    empty, since that's "no information" rather than "everyone's done".
    """
    try:
        fixtures = fpl_client.fetch_fixtures(client, gameweek)
    except Exception:
        return set()
    if not fixtures:
        return set()
    all_team_ids = {t.id for t in session.exec(select(Team)).all()}
    per_team: dict[int, list[bool]] = {tid: [] for tid in all_team_ids}
    for f in fixtures:
        done = bool(f.get("finished_provisional")) or bool(f.get("finished"))
        for tid in (f.get("team_h"), f.get("team_a")):
            if tid in per_team:
                per_team[tid].append(done)
    return {tid for tid, flags in per_team.items() if all(flags)}


def any_match_in_play(client: httpx.Client, gameweek: int) -> bool:
    """Whether a real Premier League match from this gameweek is being played
    right now (kicked off, not yet finished).

    A gameweek stays "current" for the whole multi-day round, so this is a
    much tighter signal than gw_status - it's what decides whether the
    frontend's auto-refresh ticker should actually be ticking, as opposed to
    sitting idle between Friday night's match and Saturday afternoon's.
    """
    try:
        fixtures = fpl_client.fetch_fixtures(client, gameweek)
    except Exception:
        return False
    return any(f.get("started") and not f.get("finished_provisional") for f in fixtures)


def live_points_for_gameweek(
    session: Session, client: httpx.Client, entry_ids: list[int], gameweek: int,
    live_stats: dict[int, tuple[int, int]], closed_teams: set[int],
) -> dict[int, int]:
    """Live provisional total per entry for one gameweek still in progress.

    Same auto-sub rule as entry_lineup's live_total, but skips building the
    full squad detail (names, transfers, ...) - the Fixtures list just needs
    a number per entry, for whichever entries have published picks.
    """
    picks_by_entry: dict[int, list[dict]] = {}
    needed_ids: set[int] = set()
    for entry_id in entry_ids:
        try:
            raw = fpldraft_client.fetch_entry_picks(client, entry_id, gameweek)
        except Exception:
            continue
        picks = raw.get("picks") or []
        if not picks:
            continue
        picks_by_entry[entry_id] = picks
        needed_ids.update(p["element"] for p in picks)

    if not needed_ids:
        return {}
    players_by_id = {p.id: p for p in session.exec(select(Player).where(Player.id.in_(needed_ids))).all()}
    confirmed_no_show = {pid for pid, pl in players_by_id.items() if pl.team_id in closed_teams}

    out: dict[int, int] = {}
    for entry_id, picks in picks_by_entry.items():
        ordered = sorted(picks, key=lambda p: p.get("position", 99))
        pos = lambda pid: players_by_id[pid].position if pid in players_by_id else "?"  # noqa: E731
        sp = lambda p: SquadPlayer(p["element"], "", pos(p["element"]), 0)  # noqa: E731
        starters = [sp(p) for p in ordered if p.get("position", 99) <= 11]
        bench = [sp(p) for p in ordered if p.get("position", 99) > 11]
        xi, _ = apply_auto_subs(starters, bench, live_stats, confirmed_no_show)
        out[entry_id] = sum(live_stats.get(p.player_id, (0, 0))[1] for p in xi)
    return out


def _entry_transfers(client: httpx.Client, entry_id: int, gameweek: int, this_gw_ids: set[int]) -> list[dict]:
    """Squad changes vs. the previous gameweek.

    Draft mode has no "free transfers" - the only way a squad changes between
    gameweeks is a processed waiver or trade, so a plain diff of picks *is*
    the transfer list. Nothing to compare gameweek 1 against.
    """
    if gameweek <= 1:
        return []
    try:
        prev_raw = fpldraft_client.fetch_entry_picks(client, entry_id, gameweek - 1)
    except Exception:
        return []
    prev_ids = {p["element"] for p in (prev_raw.get("picks") or [])}
    if not prev_ids:
        return []
    ins = [{"player_id": pid, "direction": "in"} for pid in sorted(this_gw_ids - prev_ids)]
    outs = [{"player_id": pid, "direction": "out"} for pid in sorted(prev_ids - this_gw_ids)]
    return ins + outs


def entry_lineup(
    session: Session, client: httpx.Client, entry_id: int, gameweek: int,
    live_stats: dict[int, tuple[int, int]], season_id: int | None, finished: bool,
    closed_teams: set[int] = frozenset(),
) -> dict | None:
    """One entry's squad for a gameweek, with live played-status and points.

    Pulls the entry's actual picks from the official FPL Draft site (this app
    doesn't store rosters for mirror-mode divisions). Draft mode has no
    captain, so every player counts at face value. Returns None if that
    gameweek's picks aren't published yet (before the first deadline, or a
    fetch error - a missing lineup shouldn't break the other side of the
    fixture).

    Once `finished` is True the squad/transfers detail is frozen: the first
    computation is saved to FixtureLineupSnapshot and every later call just
    replays it. The overall score is deliberately NOT part of that freeze -
    while the gameweek is still in progress the caller uses this result's own
    live_total (FPL Draft doesn't publish anything for an entry until the
    gameweek is fully processed); once finished, the caller switches to
    EntryPoints, so it stays live for as long as this app keeps re-syncing
    that table, exactly like the League
    table does.
    """
    if finished and season_id is not None:
        snap = session.exec(
            select(FixtureLineupSnapshot).where(
                FixtureLineupSnapshot.season_id == season_id,
                FixtureLineupSnapshot.entry_id == entry_id,
                FixtureLineupSnapshot.gameweek == gameweek,
            )
        ).first()
        if snap:
            return json.loads(snap.data)

    try:
        raw = fpldraft_client.fetch_entry_picks(client, entry_id, gameweek)
    except Exception:
        return None
    picks = raw.get("picks") or []
    if not picks:
        return None

    player_ids = {p["element"] for p in picks}
    transfers = _entry_transfers(client, entry_id, gameweek, player_ids)
    player_ids |= {t["player_id"] for t in transfers}
    players = {p.id: p for p in session.exec(select(Player).where(Player.id.in_(player_ids))).all()}
    teams = {t.id: t.short_name for t in session.exec(select(Team)).all()}

    def build(pick: dict) -> dict:
        pl = players.get(pick["element"])
        minutes, points = live_stats.get(pick["element"], (0, 0))
        return {
            "player_id": pick["element"],
            "name": pl.web_name if pl else f"Player {pick['element']}",
            "position": pl.position if pl else "?",
            "team": teams.get(pl.team_id, "") if pl else "",
            "played": minutes > 0,
            "points": points,
        }

    for t in transfers:
        pl = players.get(t["player_id"])
        t["name"] = pl.web_name if pl else f"Player {t['player_id']}"
        t["position"] = pl.position if pl else "?"

    ordered = sorted(picks, key=lambda p: p.get("position", 99))
    starters = [build(p) for p in ordered if p.get("position", 99) <= 11]
    bench = [build(p) for p in ordered if p.get("position", 99) > 11]

    # Flag who's *effectively* playing (same auto-sub rule as our own scoring):
    # a starter who didn't play, replaced in bench order by a bench player who
    # did, keeping a valid formation. Also doubles as a live provisional score
    # while the gameweek's still in progress - see live_total below. A starter
    # only counts as "didn't play" once their own fixture has actually
    # finished (or they have none this gameweek) - not just because it hasn't
    # kicked off yet.
    confirmed_no_show = {pid for pid, pl in players.items() if pl.team_id in closed_teams}
    starters_sp = [SquadPlayer(p["player_id"], p["name"], p["position"], 0) for p in starters]
    bench_sp = [SquadPlayer(p["player_id"], p["name"], p["position"], 0) for p in bench]
    xi, _ = apply_auto_subs(starters_sp, bench_sp, live_stats, confirmed_no_show)
    final_ids = {p.player_id for p in xi}
    for p in starters:
        p["subbed_out"] = p["player_id"] not in final_ids
    for p in bench:
        p["subbed_in"] = p["player_id"] in final_ids

    result = {
        "starters": starters,
        "bench": bench,
        "transfers": transfers,
        # Our own live estimate (sum of the effective XI's current points) -
        # FPL Draft itself doesn't publish anything for an entry until the
        # whole gameweek is fully processed, so this is what "as close to
        # live as possible" means before that happens. The caller uses this
        # only while the gameweek isn't finished; once it is, EntryPoints (the
        # same row the League table reads) is the authoritative score instead.
        "live_total": sum(live_stats.get(p.player_id, (0, 0))[1] for p in xi),
    }

    if finished and season_id is not None:
        try:
            session.add(FixtureLineupSnapshot(
                season_id=season_id, entry_id=entry_id, gameweek=gameweek, data=json.dumps(result),
            ))
            session.commit()
        except Exception:
            session.rollback()  # another request already snapshotted this - fine, we still return it

    return result


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
SEMI_GWS = [37]       # semi-finals decided on this gameweek
FINAL_GW = 38         # final decided on this gameweek


def playoffs(session: Session, season: Season) -> dict:
    """Top-2-per-division knockout: cross-division semis on GW37, final on GW38.

    Qualifiers are each division's top 2 (not the top 4 of the combined
    table), so one strong division can no longer shut the other out of the
    playoffs entirely. Semis are cross-division - a division's winner faces
    the OTHER division's runner-up - so the all-four-qualifiers-from-one-
    division scenario the old combined-top-4 format allowed can't happen, and
    the two division winners can only meet in the final. Ties are broken by
    true combined-table rank (not division rank), so the tie-break still
    reflects who actually had the better regular season, regardless of which
    division-slot they qualified through.
    """
    table = standings(session, season)
    div_a, div_b = table["division_a"], table["division_b"]
    if len(div_a) < 2 or len(div_b) < 2:
        return {"ready": False, "reason": "Each division needs at least 2 teams with a generated schedule."}
    overall_rank = {r["entry_id"]: r["rank"] for r in table["combined"]}

    def qualifier(row: dict) -> dict:
        return {**row, "seed_label": f"{row['division']}{row['rank']}",
                "overall_rank": overall_rank[row["entry_id"]]}

    a1, a2 = qualifier(div_a[0]), qualifier(div_a[1])
    b1, b2 = qualifier(div_b[0]), qualifier(div_b[1])

    points = {(p.entry_id, p.gameweek): p.points
              for p in session.exec(select(EntryPoints).where(EntryPoints.season_id == season.id)).all()}
    finished = finished_gameweeks(session, season)

    def score(entry_id: int, gws: list[int]) -> tuple[int | None, bool]:
        done = all(gw in finished for gw in gws)
        if not done:
            # Don't surface a total at all until every constituent gameweek is
            # confirmed finished - EntryPoints can still hold a stale row for one
            # of these gameweek ids from an older real-world calendar mapping
            # (e.g. last season's GW37-38), which must never masquerade as this
            # season's not-yet-played playoff score.
            return None, False
        total = sum(points.get((entry_id, gw), 0) for gw in gws)
        return total, done

    def order(x: dict, y: dict) -> tuple[dict, dict]:
        """Whichever of the pair actually had the better regular season goes first."""
        return (x, y) if x["overall_rank"] <= y["overall_rank"] else (y, x)

    def tie(high: dict, low: dict, gws: list[int]) -> dict:
        hp, hd = score(high["entry_id"], gws)
        lp, ld = score(low["entry_id"], gws)
        done = hd and ld
        winner = None
        if done:
            winner = high if hp >= lp else low  # equal -> higher (better) seed
        return {"high_seed": high, "low_seed": low, "gameweeks": gws,
                "high_points": hp, "low_points": lp,
                "status": "complete" if done else "pending", "winner": winner}

    sf1 = tie(*order(a1, b2), SEMI_GWS)   # Division A's #1 v Division B's #2
    sf2 = tie(*order(b1, a2), SEMI_GWS)   # Division B's #1 v Division A's #2

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
            winner = a if ap > bp else b if bp > ap else (a if a["overall_rank"] < b["overall_rank"] else b)
        final = {"gameweeks": [FINAL_GW], "team_a": a, "team_b": b,
                 "a_points": ap, "b_points": bp,
                 "status": "complete" if done else "pending", "winner": winner}
        champion = winner

    return {"ready": True, "seeds": [a1, b1, a2, b2],
            "semis": [dict(name="Semi-final 1", **sf1), dict(name="Semi-final 2", **sf2)],
            "final": final, "champion": champion}


# --- shared manager lookup --------------------------------------------------
def all_manager_names(session: Session) -> dict[int, str]:
    """entry_id -> manager_name, from every MirrorEntry ever recorded.

    Note this is keyed per *entry*, not per person - the same manager appears
    under a different key each season (see manager_identities). It's the right
    lookup for labelling one specific team in one specific season, and the wrong
    one for anything that needs to follow someone across seasons.
    """
    rows = session.exec(
        select(MirrorEntry).where(MirrorEntry.entry_id != None).order_by(MirrorEntry.id)  # noqa: E711
    ).all()
    return {e.entry_id: e.manager_name for e in rows}  # later rows win on repeats


def _identity_key(name: str) -> str:
    """Fold a manager's name into a key that survives a season rollover."""
    return " ".join((name or "").split()).casefold()


@dataclass
class ManagerIdentity:
    """One real person, across however many FPL Draft entries they've had."""

    key: str
    name: str                 # most recent spelling of their name
    entry_ids: list[int]      # every entry id they've used, first appearance first
    current_entry_id: int     # the newest - the only one FPL Draft still serves
    team: str = ""            # most recent draft team name (may be blank for older entries)


def manager_identities(session: Session) -> dict[str, ManagerIdentity]:
    """Every manager we've ever recorded, folded together across seasons.

    FPL Draft issues a fresh entry id each season and retires the old one - last
    season's /entry/{id}/ 404s outright - so an entry_id identifies a team in a
    season, not a person. The API exposes no account id either (league_entries
    carries only the entry id, the team name and the player's name), so the name
    on the account is the only thing that carries across a rollover.

    Rows are walked oldest season first, so the newest spelling of a name and
    the live entry id both win.
    """
    rows = session.exec(
        select(MirrorEntry, Division.season_id)
        .join(Division, Division.id == MirrorEntry.division_id)
        .where(MirrorEntry.entry_id != None)  # noqa: E711
        .order_by(Division.season_id, MirrorEntry.id)
    ).all()

    out: dict[str, ManagerIdentity] = {}
    for entry, _season_id in rows:
        # A nameless entry can't be matched to anyone, so keep it on its own key
        # rather than collapsing every nameless entry into a single "person".
        key = _identity_key(entry.manager_name) or f"entry:{entry.entry_id}"
        ident = out.get(key)
        if ident is None:
            out[key] = ManagerIdentity(key, entry.manager_name, [entry.entry_id], entry.entry_id,
                                        entry.team_name)
            continue
        if entry.entry_id not in ident.entry_ids:
            ident.entry_ids.append(entry.entry_id)
        ident.name = entry.manager_name or ident.name
        ident.current_entry_id = entry.entry_id
        ident.team = entry.team_name or ident.team
    return out


def identities_by_entry(session: Session) -> dict[int, ManagerIdentity]:
    """entry_id -> the person it belongs to; every id they've ever had maps here."""
    return {eid: ident for ident in manager_identities(session).values()
            for eid in ident.entry_ids}


# --- league records / hall of fame -----------------------------------------
def league_records(session: Session) -> dict:
    """Fun stats computed across every season this app has ever tracked.

    Season point totals (most/fewest in a season) only consider archived
    seasons, so an in-progress season with a hot start can't claim a record
    against seasons that played a full schedule.
    """
    names = all_manager_names(session)
    idents = identities_by_entry(session)

    def who(eid: int) -> tuple[str, str, int]:
        """(identity key, display name, id worth linking to) for an entry id."""
        ident = idents.get(eid)
        if ident is None:
            return f"entry:{eid}", names.get(eid, f"Entry {eid}"), eid
        return ident.key, ident.name, ident.current_entry_id

    highest_gw = None
    biggest_margin = None
    season_totals: list[dict] = []
    # Keyed by person, not by entry, so a run that carries over a season break
    # is still one streak - the manager's entry id changes, the manager doesn't.
    timeline: dict[str, list[tuple[int, int, str]]] = {}
    streak_who: dict[str, tuple[str, int]] = {}

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
                _, name, link = who(eid)
                highest_gw = {"entry_id": link, "name": name,
                             "season_name": season.name, "gameweek": gw, "points": pts}

        if season.archived_at:
            for eid, total in totals.items():
                _, name, link = who(eid)
                season_totals.append({"entry_id": link, "name": name,
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
                _, wname, wlink = who(winner)
                _, lname, llink = who(loser)
                biggest_margin = {
                    "winner_id": wlink, "winner": wname,
                    "loser_id": llink, "loser": lname,
                    "season_name": season.name, "gameweek": f.gameweek, "margin": margin,
                    "winner_points": max(hp, ap), "loser_points": min(hp, ap),
                }
            result_h = "W" if hp > ap else "L" if hp < ap else "D"
            result_a = "W" if ap > hp else "L" if ap < hp else "D"
            for eid, result in ((f.home_entry, result_h), (f.away_entry, result_a)):
                key, name, link = who(eid)
                timeline.setdefault(key, []).append((season.id, f.gameweek, result))
                streak_who[key] = (name, link)

    best_win_streak = {"entry_id": None, "name": None, "length": 0}
    best_unbeaten_streak = {"entry_id": None, "name": None, "length": 0}
    for key, games in timeline.items():
        games.sort(key=lambda g: (g[0], g[1]))
        name, link = streak_who[key]
        win_run = unbeaten_run = 0
        for _, _, result in games:
            win_run = win_run + 1 if result == "W" else 0
            unbeaten_run = unbeaten_run + 1 if result in ("W", "D") else 0
            if win_run > best_win_streak["length"]:
                best_win_streak = {"entry_id": link, "name": name, "length": win_run}
            if unbeaten_run > best_unbeaten_streak["length"]:
                best_unbeaten_streak = {"entry_id": link, "name": name, "length": unbeaten_run}

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


# --- manager profile ---------------------------------------------------
def manager_profile(session: Session, entry_id: int) -> dict | None:
    """Career W-D-L/PF across every season, plus a gameweek log for the most
    recent season they've appeared in (current if they're in it, else their
    last one) - the season log isn't limited to any one selected season since
    a manager's profile is inherently a cross-season view.

    Accepts *any* entry id the manager has ever held, not just their live one,
    because FPL Draft reissues these every season - so a link off a past
    season's trophy or an old bookmark still lands on the full profile rather
    than an empty one.
    """
    ident = identities_by_entry(session).get(entry_id)
    if ident is None:
        return None
    mine = set(ident.entry_ids)
    names = all_manager_names(session)

    career = {"seasons_played": 0, "wins": 0, "draws": 0, "losses": 0, "points_for": 0}
    latest_season = None
    latest_log: list[dict] = []

    for season in session.exec(select(Season).order_by(Season.id)).all():
        fixtures = [f for f in session.exec(
            select(Fixture).where(Fixture.season_id == season.id)).all()
            if f.home_entry in mine or f.away_entry in mine]
        if not fixtures:
            continue
        finished = finished_gameweeks(session, season)
        points = {
            (p.entry_id, p.gameweek): p.points
            for p in session.exec(select(EntryPoints).where(EntryPoints.season_id == season.id)).all()
        }
        log: list[dict] = []
        for f in sorted(fixtures, key=lambda fx: fx.gameweek):
            if f.gameweek not in finished:
                continue
            me = f.home_entry if f.home_entry in mine else f.away_entry
            opp = f.away_entry if f.home_entry in mine else f.home_entry
            if (me, f.gameweek) not in points or (opp, f.gameweek) not in points:
                continue
            own, other = points[(me, f.gameweek)], points[(opp, f.gameweek)]
            result = "W" if own > other else "L" if own < other else "D"
            career["wins" if result == "W" else "losses" if result == "L" else "draws"] += 1
            career["points_for"] += own
            log.append({"gameweek": f.gameweek, "opponent": names.get(opp, f"Entry {opp}"),
                       "opponent_id": opp,
                       "own_points": own, "opp_points": other, "result": result})
        if log:
            career["seasons_played"] += 1
        if latest_season is None or season.id >= latest_season.id:
            latest_season = season
            latest_log = log

    division = None
    if latest_season is not None:
        try:
            a_entries, b_entries = collect_divisions(session, latest_season)
            if mine & {e.entry_id for e in a_entries}:
                division = "A"
            elif mine & {e.entry_id for e in b_entries}:
                division = "B"
        except ValueError:
            division = None

    return {
        "entry_id": ident.current_entry_id, "name": ident.name, "career": career,
        "season": None if latest_season is None else {
            "season_id": latest_season.id, "season_name": latest_season.name,
            "division": division, "log": latest_log,
        },
    }
