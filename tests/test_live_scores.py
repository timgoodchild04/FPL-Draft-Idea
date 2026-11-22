"""Live gameweek state and live scores, with the FPL APIs faked."""
import pytest

from app import custom_league, fpl_client, fpldraft_client
from tests.conftest import add_players


def fx(h, a, started=True, finished=False):
    return {"team_h": h, "team_a": a, "started": started, "finished": finished, "finished_provisional": finished}


@pytest.fixture
def fixtures(monkeypatch):
    holder = {"value": []}

    def fake(client, gw):
        v = holder["value"]
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(fpl_client, "fetch_fixtures", fake)
    return holder


def test_closed_teams(session, fixtures):
    add_players(session, {1: (1, 1), 2: (1, 2), 3: (1, 3), 4: (1, 4), 5: (1, 5)})
    # Clubs 1 v 2 mid-match; club 3 has a double gameweek with one game still to play;
    # club 4 finished; club 5 has no game at all (blank) -> only 4 and 5 are done.
    fixtures["value"] = [fx(1, 2), fx(3, 4, finished=True), fx(3, 1, started=False)]
    assert custom_league.fixture_closed_teams(session, None, 1) == {4, 5}


def test_no_fixture_data_means_nobody_is_closed(session, fixtures):
    add_players(session, {1: (1, 1)})
    fixtures["value"] = []
    assert custom_league.fixture_closed_teams(session, None, 1) == set()
    fixtures["value"] = RuntimeError("FPL down")
    assert custom_league.fixture_closed_teams(session, None, 1) == set()


def test_gameweek_over_and_in_play_flags(fixtures):
    fixtures["value"] = [fx(1, 2, finished=True), fx(3, 4, started=True)]
    assert not custom_league.real_matches_finished(None, 1)
    assert custom_league.any_match_in_play(None, 1)
    fixtures["value"] = [fx(1, 2, finished=True), fx(3, 4, finished=True)]
    assert custom_league.real_matches_finished(None, 1)
    assert not custom_league.any_match_in_play(None, 1)
    # "No information" must never read as "all done".
    fixtures["value"] = []
    assert not custom_league.real_matches_finished(None, 1)
    fixtures["value"] = RuntimeError("FPL down")
    assert not custom_league.real_matches_finished(None, 1)


def test_live_points_with_auto_sub(session, monkeypatch):
    # 4-4-2, players 1-11 starting, 12-15 bench. Club 1 = everyone except
    # player 6 (club 2, game finished, didn't play) and player 7 (club 3, not kicked off).
    players = {1: (1, 1), 12: (1, 1)}
    players |= {pid: (2, 1) for pid in (2, 3, 4, 5, 13)}
    players |= {pid: (3, 1) for pid in (8, 9, 14)} | {6: (3, 2), 7: (3, 3)}
    players |= {pid: (4, 1) for pid in (10, 11, 15)}
    add_players(session, players)
    picks = [{"element": pid, "position": pid} for pid in range(1, 16)]
    monkeypatch.setattr(fpldraft_client, "fetch_entry_picks", lambda c, e, gw: {"picks": picks})

    stats = {pid: (90, 2) for pid in range(1, 16) if pid not in (6, 7)}
    out = custom_league.live_points_for_gameweek(session, None, [101], 1, stats, closed_teams={1, 2})
    # 9 starters played (18) + 1st sub (13) in for 6 (2) = 20; 7 still to play counts 0 but isn't subbed.
    assert out[101] == (20, 10, 11)


def test_live_points_skips_entries_without_picks(session, monkeypatch):
    monkeypatch.setattr(fpldraft_client, "fetch_entry_picks", lambda c, e, gw: {"picks": []})
    assert custom_league.live_points_for_gameweek(session, None, [101], 1, {}, set()) == {}
