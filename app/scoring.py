"""Auto-substitution logic, shared by app.custom_league's live fixture scoring.

Draft-mode scoring has no captain: a manager's score is the sum of their
starting XI's points for the gameweek, with automatic substitutions - any
starter who played 0 minutes is replaced, in bench order, by a bench player
who played, provided the formation stays valid (1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.lineup_models import FORMATION_BOUNDS


@dataclass
class SquadPlayer:
    player_id: int
    name: str
    position: str
    season_points: int


def valid_formation(positions: list[str]) -> bool:
    if len(positions) != 11:
        return False
    c = Counter(positions)
    return all(lo <= c.get(pos, 0) <= hi for pos, (lo, hi) in FORMATION_BOUNDS.items())


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
