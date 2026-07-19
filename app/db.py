"""Database engine and session helpers.

Uses DATABASE_URL (e.g. a hosted Postgres) when set - required in production so
data survives restarts - and falls back to a local SQLite file for development.
"""
import os
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

# Columns added to already-existing tables after their first release. create_all
# never alters existing tables, so we add these by hand on startup if missing.
# (table_name, column_name, SQL type)
_ADDED_COLUMNS = [
    ("leaguemeta", "points_synced_at", "VARCHAR"),
    ("season", "archived_at", "VARCHAR"),
]


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
    _migrate()


def _migrate() -> None:
    """Add any columns introduced after a table's first release (idempotent)."""
    insp = inspect(ENGINE)
    tables = set(insp.get_table_names())
    for table, column, sqltype in _ADDED_COLUMNS:
        if table not in tables:
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        if column not in existing:
            with ENGINE.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"))


def get_session() -> Session:
    return Session(ENGINE)
