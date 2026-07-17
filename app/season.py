"""The season layer: the only place stage 1 and stage 2 connect.

Responsibilities (and nothing else):
  1. Promotion/relegation - from stage-1 standings, work out who moves.
  2. Creating the stage-2 divisions + memberships (which then draft normally).
  3. The combined season table - carry-over points = stage 1 + stage 2.

Everything below reuses the draft and scoring engines unchanged; the "re-draft"
is just a normal draft on the freshly created stage-2 divisions.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app import scoring, setup_league
from app.league_models import Division, DivisionMembership, Manager, Season


def stage_divisions(session: Session, season_id: int, stage: int) -> list[Division]:
    """Divisions for a season+stage, ordered by tier (1 = top)."""
    return session.exec(
        select(Division)
        .where(Division.season_id == season_id, Division.stage == stage)
        .order_by(Division.tier)
    ).all()


def _standings_lookup(session: Session, division: Division) -> list[dict]:
    return scoring.division_standings(session, division)


def compute_split(session: Session, season: Season, n_swap: int = 2) -> dict:
    """Preview promotion/relegation without changing anything.

    Bottom `n_swap` of tier 1 are relegated; top `n_swap` of tier 2 are promoted.
    """
    divs = stage_divisions(session, season.id, 1)
    if len(divs) != 2:
        raise ValueError("Split expects exactly two stage-1 divisions (tier 1 and tier 2).")
    tier1, tier2 = divs
    s1 = _standings_lookup(session, tier1)
    s2 = _standings_lookup(session, tier2)
    if len(s1) < n_swap or len(s2) < n_swap:
        raise ValueError("Each division needs at least n_swap managers.")

    relegated = s1[-n_swap:]          # worst in tier 1
    promoted = s2[:n_swap]            # best in tier 2
    new_tier1 = s1[:-n_swap] + promoted
    new_tier2 = s2[n_swap:] + relegated
    return {
        "n_swap": n_swap,
        "relegated": [{"manager_id": r["manager_id"], "manager": r["manager"],
                       "stage1_points": r["points"], "stage1_rank": r["rank"]} for r in relegated],
        "promoted": [{"manager_id": r["manager_id"], "manager": r["manager"],
                      "stage1_points": r["points"], "stage1_rank": r["rank"]} for r in promoted],
        "new_tier1": new_tier1,
        "new_tier2": new_tier2,
    }


def apply_split(session: Session, season: Season, n_swap: int = 2) -> dict:
    """Create the stage-2 divisions and memberships, then advance the season.

    Stage-2 draft seed order is worst-first by stage-1 cumulative points, so the
    managers who did worse in the first half pick earlier in the re-draft.
    """
    if season.current_stage >= 2:
        raise ValueError("Season is already in stage 2.")
    plan = compute_split(session, season, n_swap)

    new_t1 = setup_league.create_division(
        session, season.id, stage=2, tier=1, name="Division A - Stage 2")
    new_t2 = setup_league.create_division(
        session, season.id, stage=2, tier=2, name="Division B - Stage 2")

    for div, members in ((new_t1, plan["new_tier1"]), (new_t2, plan["new_tier2"])):
        worst_first = sorted(members, key=lambda m: m["points"])
        setup_league.set_division_members(session, div.id, [m["manager_id"] for m in worst_first])

    season.current_stage = 2
    session.add(season)
    session.commit()
    return {
        "stage2_tier1_division_id": new_t1.id,
        "stage2_tier2_division_id": new_t2.id,
        "promoted": plan["promoted"],
        "relegated": plan["relegated"],
    }


def season_table(session: Session, season: Season) -> list[dict]:
    """Combined ranked table across both stages (points carry over)."""
    divisions = session.exec(
        select(Division).where(Division.season_id == season.id)
    ).all()

    # Compute each division's standings once; index points by (division, manager).
    per_manager: dict[int, dict] = {}
    for div in divisions:
        for row in _standings_lookup(session, div):
            mid = row["manager_id"]
            entry = per_manager.setdefault(
                mid, {"manager_id": mid, "manager": row["manager"],
                      "stage1": None, "stage2": None, "total": 0})
            key = f"stage{div.stage}"
            entry[key] = {"division": div.name, "tier": div.tier, "points": row["points"]}
            entry["total"] += row["points"]

    table = sorted(per_manager.values(), key=lambda e: e["total"], reverse=True)
    for rank, row in enumerate(table, start=1):
        row["overall_rank"] = rank
    return table
