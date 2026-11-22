"""Head-to-head league table rules."""
from app.custom_league import compute_h2h_from_fixtures

NAMES = {1: "A", 2: "B", 3: "C", 4: "D"}
DIVS = {1: "A", 2: "A", 3: "B", 4: "B"}


def row(table, eid):
    return next(r for r in table if r.entry_id == eid)


def test_win_draw_loss_points():
    fixtures = [{"gameweek": 1, "home_entry": 1, "away_entry": 2},
                {"gameweek": 1, "home_entry": 3, "away_entry": 4}]
    points = {(1, 1): 50, (2, 1): 40, (3, 1): 45, (4, 1): 45}
    t = compute_h2h_from_fixtures(fixtures, points, {1}, NAMES, DIVS)
    assert (row(t, 1).won, row(t, 1).h2h_points) == (1, 3)
    assert (row(t, 2).lost, row(t, 2).h2h_points) == (1, 0)
    assert (row(t, 3).drawn, row(t, 3).h2h_points, row(t, 4).h2h_points) == (1, 1, 1)
    assert (row(t, 1).points_for, row(t, 1).points_against) == (50, 40)


def test_unfinished_or_unscored_games_dont_count():
    fixtures = [{"gameweek": 1, "home_entry": 1, "away_entry": 2},   # gameweek not finished
                {"gameweek": 2, "home_entry": 3, "away_entry": 4}]   # finished, but 4 has no score yet
    points = {(1, 1): 50, (2, 1): 40, (3, 2): 60}
    t = compute_h2h_from_fixtures(fixtures, points, {2}, NAMES, DIVS)
    assert all(r.played == 0 for r in t)


def test_ties_on_h2h_points_broken_by_total_points():
    fixtures = [{"gameweek": 1, "home_entry": 1, "away_entry": 3},
                {"gameweek": 1, "home_entry": 2, "away_entry": 4}]
    points = {(1, 1): 50, (3, 1): 40, (2, 1): 70, (4, 1): 30}
    t = compute_h2h_from_fixtures(fixtures, points, {1}, NAMES, DIVS)
    assert [r.entry_id for r in t][:2] == [2, 1]   # both on 3 pts, 2 scored more
