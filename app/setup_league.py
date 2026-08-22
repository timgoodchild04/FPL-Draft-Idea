"""Helpers to create seasons and divisions."""
from __future__ import annotations

from sqlmodel import Session

from app.league_models import Division, Season


def create_season(session: Session, name: str, split_gameweek: int = 19) -> Season:
    season = Season(name=name, split_gameweek=split_gameweek)
    session.add(season)
    session.commit()
    session.refresh(season)
    return season


def create_division(
    session: Session, season_id: int, stage: int, tier: int, name: str
) -> Division:
    div = Division(season_id=season_id, stage=stage, tier=tier, name=name)
    session.add(div)
    session.commit()
    session.refresh(div)
    return div
