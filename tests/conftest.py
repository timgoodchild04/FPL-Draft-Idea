"""Shared test helpers: an in-memory database and a tiny league factory.

Nothing here touches the network or the real database - FPL API calls are
monkeypatched in each test.
"""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import league_models, mirror_models, models, schedule_models, settings_models  # noqa: F401  (register tables)
from app.league_models import Division, Season
from app.mirror_models import MirrorEntry
from app.models import Gameweek, Player, Team


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def league(session):
    """Season with two divisions of three: A = 101-103, B = 201-203."""
    season = Season(name="Test")
    session.add(season)
    session.commit()
    for tier, ids in ((1, [101, 102, 103]), (2, [201, 202, 203])):
        d = Division(season_id=season.id, stage=1, tier=tier, name=f"Division {'AB'[tier - 1]}")
        session.add(d)
        session.commit()
        for i, eid in enumerate(ids):
            session.add(MirrorEntry(division_id=d.id, league_entry_id=eid, entry_id=eid,
                                    manager_name=f"Manager {eid}", team_name=f"Team {eid}"))
    session.commit()
    return season


def add_gameweeks(session, finished_ids, upto=38):
    for gw in range(1, upto + 1):
        session.add(Gameweek(id=gw, name=f"Gameweek {gw}", finished=gw in finished_ids))
    session.commit()


def add_players(session, players):
    """players: {player_id: (element_type, team_id)} - teams are created as needed."""
    for tid in {t for _, t in players.values()}:
        if session.get(Team, tid) is None:
            session.add(Team(id=tid, name=f"Club {tid}", short_name=f"C{tid}", code=tid))
    for pid, (etype, tid) in players.items():
        session.add(Player(id=pid, web_name=f"P{pid}", first_name="", second_name="",
                           team_id=tid, element_type=etype))
    session.commit()
