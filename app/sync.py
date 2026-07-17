"""Pull reference data from the FPL API into the local SQLite DB.

Usage:
    python -m app.sync                # sync teams/players/gameweeks + all finished GW stats
    python -m app.sync --no-stats     # skip the per-gameweek points (faster)
    python -m app.sync --max-gw 5     # only fetch per-gameweek stats up to GW5
"""
from __future__ import annotations

import argparse

import httpx
from sqlmodel import Session, select

from app import fpl_client
from app.db import ENGINE, init_db
from app.models import Gameweek, Player, PlayerGameweekStats, Team


def _sync_reference(session: Session, boot: dict) -> None:
    for t in boot["teams"]:
        session.merge(Team(
            id=t["id"], name=t["name"], short_name=t["short_name"], code=t["code"],
        ))

    for e in boot["elements"]:
        session.merge(Player(
            id=e["id"],
            web_name=e["web_name"],
            first_name=e["first_name"],
            second_name=e["second_name"],
            team_id=e["team"],
            element_type=e["element_type"],
            total_points=e["total_points"],
            minutes=e["minutes"],
            goals_scored=e["goals_scored"],
            assists=e["assists"],
            clean_sheets=e["clean_sheets"],
            now_cost=e["now_cost"],
            status=e["status"],
        ))

    for ev in boot["events"]:
        session.merge(Gameweek(
            id=ev["id"],
            name=ev["name"],
            deadline_time=ev.get("deadline_time"),
            finished=ev["finished"],
            is_current=ev.get("is_current", False),
            is_next=ev.get("is_next", False),
        ))
    session.commit()


def _sync_gameweek_stats(session: Session, client: httpx.Client, max_gw: int | None) -> int:
    finished = session.exec(
        select(Gameweek).where(Gameweek.finished == True).order_by(Gameweek.id)  # noqa: E712
    ).all()
    if max_gw is not None:
        finished = [gw for gw in finished if gw.id <= max_gw]

    rows = 0
    for gw in finished:
        live = fpl_client.fetch_gameweek_live(client, gw.id)
        for el in live["elements"]:
            s = el["stats"]
            session.merge(PlayerGameweekStats(
                player_id=el["id"],
                gameweek_id=gw.id,
                total_points=s["total_points"],
                minutes=s["minutes"],
                goals_scored=s["goals_scored"],
                assists=s["assists"],
                clean_sheets=s["clean_sheets"],
                bonus=s["bonus"],
            ))
            rows += 1
        session.commit()
        print(f"  synced stats for {gw.name} ({len(live['elements'])} players)")
    return rows


def sync_gameweeks_only() -> int:
    """Populate just the Gameweek table (finished flags) via one bootstrap call.

    This is all the custom two-division league needs from the classic API, so it's
    cheap to run on startup / before syncing points to keep 'finished' current.
    """
    init_db()
    with httpx.Client() as client:
        boot = fpl_client.fetch_bootstrap(client)
    with Session(ENGINE) as session:
        for ev in boot["events"]:
            session.merge(Gameweek(
                id=ev["id"], name=ev["name"], deadline_time=ev.get("deadline_time"),
                finished=ev["finished"], is_current=ev.get("is_current", False),
                is_next=ev.get("is_next", False),
            ))
        session.commit()
    return len(boot["events"])


def run_sync(with_stats: bool = True, max_gw: int | None = None) -> None:
    init_db()
    with httpx.Client() as client:
        boot = fpl_client.fetch_bootstrap(client)
        with Session(ENGINE) as session:
            _sync_reference(session, boot)
            print(
                f"Reference data: {len(boot['teams'])} teams, "
                f"{len(boot['elements'])} players, {len(boot['events'])} gameweeks."
            )
            if with_stats:
                rows = _sync_gameweek_stats(session, client, max_gw)
                print(f"Per-gameweek stat rows: {rows}")
    print("Sync complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync FPL reference data into SQLite.")
    parser.add_argument("--no-stats", action="store_true", help="Skip per-gameweek points.")
    parser.add_argument("--max-gw", type=int, default=None, help="Only sync stats up to this GW.")
    args = parser.parse_args()
    run_sync(with_stats=not args.no_stats, max_gw=args.max_gw)


if __name__ == "__main__":
    main()
