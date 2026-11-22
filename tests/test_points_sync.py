"""Official points sync: only FPL-Draft-finalised gameweeks are stored."""
import pytest
from sqlmodel import select

from app import custom_league, fpldraft_client
from app.schedule_models import EntryPoints


@pytest.fixture
def fake_draft(monkeypatch):
    state = {"game": {"current_event": 8, "current_event_finished": False}, "broken": set()}

    def history(client, entry_id):
        if entry_id in state["broken"]:
            raise RuntimeError("FPL hiccup")
        return {"history": [{"event": gw, "points": entry_id % 100 * 10 + gw} for gw in range(1, 9)]}
    monkeypatch.setattr(fpldraft_client, "fetch_game", lambda c: state["game"])
    monkeypatch.setattr(fpldraft_client, "fetch_entry_history", history)
    monkeypatch.setattr(fpldraft_client, "fetch_entry_public", lambda c, e: {"entry": {"name": f"Team {e}"}})
    return state


def stored(session):
    return {(p.entry_id, p.gameweek): p.points for p in session.exec(select(EntryPoints)).all()}


def test_unfinalised_gameweek_is_not_stored(session, league, fake_draft):
    # Regression (ffff2fc): GW8's interim total (22 instead of 40) was shown as final.
    result = custom_league.sync_points(session, league, client=object())
    rows = stored(session)
    assert max(gw for _, gw in rows) == 7
    assert result["final_up_to"] == 7


def test_finalised_gameweek_is_stored(session, league, fake_draft):
    fake_draft["game"]["current_event_finished"] = True
    custom_league.sync_points(session, league, client=object())
    assert stored(session)[(101, 8)] == 18


def test_resync_replaces_old_numbers(session, league, fake_draft):
    custom_league.sync_points(session, league, client=object())
    session.add(EntryPoints(season_id=league.id, entry_id=101, gameweek=99, points=1))
    session.commit()
    custom_league.sync_points(session, league, client=object())
    assert (101, 99) not in stored(session)   # FPL's numbers always win


def test_one_failing_entry_doesnt_wipe_the_others(session, league, fake_draft):
    fake_draft["broken"] = {102}
    result = custom_league.sync_points(session, league, client=object())
    rows = stored(session)
    assert result["failed"] == [102]
    assert not any(e == 102 for e, _ in rows) and (101, 1) in rows
