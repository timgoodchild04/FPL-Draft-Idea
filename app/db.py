"""Database engine and session helpers.

Uses DATABASE_URL (e.g. a hosted Postgres) when set - required in production so
data survives restarts - and falls back to a local SQLite file for development.
"""
import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine


def _make_engine():
    url = os.environ.get("DATABASE_URL")
    if url:
        # Normalise to the psycopg v3 driver SQLAlchemy expects.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return create_engine(url, echo=False, pool_pre_ping=True)
    db_path = Path(__file__).resolve().parent.parent / "fpl_draft.db"
    return create_engine(f"sqlite:///{db_path}", echo=False,
                         connect_args={"check_same_thread": False})


ENGINE = _make_engine()


def init_db() -> None:
    """Create all tables. Safe to call repeatedly."""
    # Importing models registers them on SQLModel.metadata before create_all.
    from app import (  # noqa: F401
        league_models,
        lineup_models,
        mirror_models,
        models,
        schedule_models,
    )

    SQLModel.metadata.create_all(ENGINE)


def get_session() -> Session:
    return Session(ENGINE)
