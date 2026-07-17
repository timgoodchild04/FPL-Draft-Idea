"""Helpers to create seasons, managers, divisions, and memberships."""
from __future__ import annotations

from sqlmodel import Session

from app.league_models import Division, DivisionMembership, Manager, Season


def create_season(session: Session, name: str, split_gameweek: int = 19) -> Season:
    season = Season(name=name, split_gameweek=split_gameweek)
    session.add(season)
    session.commit()
    session.refresh(season)
    return season


def add_manager(session: Session, name: str) -> Manager:
    manager = Manager(name=name)
    session.add(manager)
    session.commit()
    session.refresh(manager)
    return manager


def create_division(
    session: Session, season_id: int, stage: int, tier: int, name: str
) -> Division:
    div = Division(season_id=season_id, stage=stage, tier=tier, name=name)
    session.add(div)
    session.commit()
    session.refresh(div)
    return div


def set_division_members(session: Session, division_id: int, manager_ids: list[int]) -> None:
    """Assign managers to a division; list order becomes the draft seed (1..N)."""
    for seed, manager_id in enumerate(manager_ids, start=1):
        session.add(
            DivisionMembership(division_id=division_id, manager_id=manager_id, seed=seed)
        )
    session.commit()
