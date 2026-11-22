"""Playoffs: top two per division, cross-division semis on GW37, final GW38."""
from app import custom_league
from app.schedule_models import EntryPoints, Fixture
from tests.conftest import add_gameweeks


def setup_regular_season(session, season):
    # One cross-division gameweek: every A team beats its B opponent, and
    # higher ids score less, so 101 > 102 > 103 in A and 201 > 202 > 203 in B.
    pairs = [(101, 201), (102, 202), (103, 203)]
    for h, a in pairs:
        session.add(Fixture(season_id=season.id, gameweek=1, home_entry=h, away_entry=a))
    scores = {101: 90, 102: 80, 103: 70, 201: 60, 202: 50, 203: 40}
    for eid, pts in scores.items():
        session.add(EntryPoints(season_id=season.id, entry_id=eid, gameweek=1, points=pts))
    session.commit()


def add_scores(session, season, gw, scores):
    for eid, pts in scores.items():
        session.add(EntryPoints(season_id=season.id, entry_id=eid, gameweek=gw, points=pts))
    session.commit()


def entry(x):
    return x["entry_id"] if x else None


def test_semis_are_cross_division_and_pending_until_played(session, league):
    add_gameweeks(session, {1})
    setup_regular_season(session, league)
    p = custom_league.playoffs(session, league)
    sf1, sf2 = p["semis"]
    assert {entry(sf1["high_seed"]), entry(sf1["low_seed"])} == {101, 202}   # A1 v B2
    assert {entry(sf2["high_seed"]), entry(sf2["low_seed"])} == {201, 102}   # B1 v A2
    assert sf1["status"] == "pending" and sf1["high_points"] is None
    assert p["champion"] is None


def test_stale_playoff_scores_hidden_until_gameweek_finished(session, league):
    add_gameweeks(session, {1})
    setup_regular_season(session, league)
    add_scores(session, league, 37, {101: 10, 202: 99, 201: 10, 102: 99})  # e.g. left over from last season
    sf1 = custom_league.playoffs(session, league)["semis"][0]
    assert sf1["high_points"] is None and sf1["winner"] is None


def test_semi_tie_goes_to_better_seed_and_final_decides_champion(session, league):
    add_gameweeks(session, {1, 37, 38})
    setup_regular_season(session, league)
    add_scores(session, league, 37, {101: 50, 202: 50, 201: 40, 102: 60})
    add_scores(session, league, 38, {101: 70, 102: 70})
    p = custom_league.playoffs(session, league)
    assert entry(p["semis"][0]["winner"]) == 101     # 50-50, 101 had the better season
    assert entry(p["semis"][1]["winner"]) == 102     # 60 beats 40
    assert entry(p["champion"]) == 101               # final 70-70, overall rank decides
