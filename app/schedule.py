"""Random fixture-schedule generator for the two-division format.

Produces a 35-gameweek schedule for 14 teams (two divisions of 7) where, per team:
  * each of the 6 division rivals is played 3 times   (18)
  * each of the 7 cross-division teams is played twice (14)
  * 3 further "extra" games are drawn at random         (3)
  * exactly one match per gameweek (every GW is a perfect pairing of all 14)

The schedule is generated once (randomly, no team advantaged) and then frozen.
We build the required multiset of meetings, then decompose it into 35 perfect
matchings via randomised backtracking with restarts. Because every team plays
once per gameweek, the "remaining" graph stays regular, so a valid decomposition
is reliably found; the result is always validated before being returned.
"""
from __future__ import annotations

import random
from collections import Counter
from itertools import combinations

Pair = tuple[str, str]


def _key(a: str, b: str) -> Pair:
    return (a, b) if a < b else (b, a)


def _extra_layer(teams: list[str], degree: int, rng: random.Random,
                 fixed: list[Pair] | None = None) -> Counter:
    """`degree` extra perfect matchings (edge-disjoint where possible).

    Adds one game to each drawn pair, so every team gains `degree` extra games.
    If `fixed` (a perfect matching, e.g. the derby pairs) is given, it is used as
    the first extra matching and the rest are drawn at random.
    """
    edges: Counter = Counter()
    used: set[Pair] = set()
    done = 0
    if fixed:
        for a, b in fixed:
            k = _key(a, b)
            used.add(k)
            edges[k] += 1
        done = 1
    for _ in range(degree - done):
        pairs: list[Pair] | None = None
        for _try in range(2000):
            shuffled = teams[:]
            rng.shuffle(shuffled)
            candidate = [_key(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled), 2)]
            if all(p not in used for p in candidate):
                pairs = candidate
                break
        if pairs is None:  # fall back to allowing a repeated extra pairing
            shuffled = teams[:]
            rng.shuffle(shuffled)
            pairs = [_key(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled), 2)]
        for p in pairs:
            used.add(p)
            edges[p] += 1
    return edges


def required_meetings(division_a: list[str], division_b: list[str],
                      extra_per_team: int, rng: random.Random,
                      derby_pairs: list[Pair] | None = None) -> Counter:
    req: Counter = Counter()
    for members in (division_a, division_b):
        for a, b in combinations(members, 2):
            req[_key(a, b)] += 3
    for a in division_a:
        for b in division_b:
            req[_key(a, b)] += 2
    req.update(_extra_layer(division_a + division_b, extra_per_team, rng, derby_pairs))
    return req


def _find_matching(teams: list[str], remaining: Counter, rng: random.Random) -> list[Pair] | None:
    """One perfect matching using pairs that still have games left.

    Most-constrained-first (fewest available partners) to avoid dead ends.
    """
    matched: set[str] = set()
    result: list[Pair] = []

    def partners(t: str) -> list[str]:
        return [u for u in teams
                if u != t and u not in matched and remaining.get(_key(t, u), 0) > 0]

    def backtrack() -> bool:
        free = [t for t in teams if t not in matched]
        if not free:
            return True
        # pick the free team with the fewest available partners
        t = min(free, key=lambda x: len(partners(x)))
        opts = partners(t)
        rng.shuffle(opts)
        for u in opts:
            matched.add(t); matched.add(u); result.append(_key(t, u))
            if backtrack():
                return True
            result.pop(); matched.discard(t); matched.discard(u)
        return False

    return result[:] if backtrack() else None


def generate_schedule(division_a: list[str], division_b: list[str], *,
                      rounds: int = 35, extra_per_team: int = 3,
                      seed: int | None = None, restarts: int = 300,
                      derby_pairs: list[Pair] | None = None) -> list[list[Pair]]:
    """Return `rounds` gameweeks, each a list of 7 (team, team) pairs.

    `derby_pairs`, if given, must be a perfect matching of all teams (each team
    in exactly one pair); those meetings are guaranteed as one of the extra games.
    """
    rng = random.Random(seed)
    teams = division_a + division_b
    if len(teams) % 2 != 0:
        raise ValueError("Need an even number of teams.")

    if derby_pairs:
        flat = [t for pair in derby_pairs for t in pair]
        if len(derby_pairs) != len(teams) // 2 or sorted(flat) != sorted(teams):
            raise ValueError("Derby pairs must pair every team exactly once.")

    for _outer in range(20):  # re-roll the extra layer if a draw proves unschedulable
        req = required_meetings(division_a, division_b, extra_per_team, rng, derby_pairs)
        for _attempt in range(restarts):
            remaining = Counter({k: v for k, v in req.items()})
            weeks: list[list[Pair]] = []
            ok = True
            for _gw in range(rounds):
                m = _find_matching(teams, remaining, rng)
                if m is None:
                    ok = False
                    break
                weeks.append(m)
                for p in m:
                    remaining[p] -= 1
            if ok and all(v == 0 for v in remaining.values()):
                validate_schedule(weeks, division_a, division_b, extra_per_team)
                return weeks
    raise RuntimeError("Could not generate a valid schedule; try a different seed.")


def validate_schedule(weeks: list[list[Pair]], division_a: list[str],
                      division_b: list[str], extra_per_team: int = 3) -> dict:
    """Raise AssertionError if any rule is violated; else return a summary."""
    teams = division_a + division_b
    div_of = {t: "A" for t in division_a} | {t: "B" for t in division_b}

    # 1. Each gameweek is a perfect matching of all 14 teams.
    for i, wk in enumerate(weeks, 1):
        played = [t for pair in wk for t in pair]
        assert len(wk) == len(teams) // 2, f"GW{i}: expected {len(teams)//2} matches"
        assert sorted(played) == sorted(teams), f"GW{i} is not a perfect pairing of all teams"

    # 2. Per-pair meeting counts.
    counts: Counter = Counter()
    for wk in weeks:
        for p in wk:
            counts[p] += 1

    # 3. Per-team requirements.
    per_team = {}
    for t in teams:
        div_games = cross_games = extras = 0
        for u in teams:
            if u == t:
                continue
            c = counts.get(_key(t, u), 0)
            same_div = div_of[t] == div_of[u]
            base = 3 if same_div else 2
            assert c >= base, f"{t} v {u}: {c} games, expected at least {base}"
            if same_div:
                div_games += c
            else:
                cross_games += c
            extras += c - base
        total = div_games + cross_games
        assert total == len(weeks), f"{t}: {total} games over {len(weeks)} gameweeks"
        assert extras == extra_per_team, f"{t}: {extras} extra games, expected {extra_per_team}"
        per_team[t] = {"division": div_of[t], "total": total,
                       "div_games": div_games, "cross_games": cross_games, "extras": extras}
    return {"gameweeks": len(weeks), "teams": len(teams), "per_team": per_team,
            "meeting_counts": {f"{a} v {b}": c for (a, b), c in counts.items()}}
