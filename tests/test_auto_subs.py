"""Auto-sub rules (app.scoring) - the logic behind every live score."""
from app.scoring import SquadPlayer, apply_auto_subs, valid_formation


def P(pid, pos):
    return SquadPlayer(pid, f"P{pid}", pos, 0)


# A 4-4-2: GK 1, DEF 2-5, MID 6-9, FWD 10-11. Bench: GK 12, then 13 DEF, 14 MID, 15 FWD.
XI = [P(1, "GK"), P(2, "DEF"), P(3, "DEF"), P(4, "DEF"), P(5, "DEF"),
      P(6, "MID"), P(7, "MID"), P(8, "MID"), P(9, "MID"), P(10, "FWD"), P(11, "FWD")]
BENCH = [P(12, "GK"), P(13, "DEF"), P(14, "MID"), P(15, "FWD")]
EVERYONE_PLAYED = {pid: (90, 2) for pid in range(1, 16)}


def ids(xi):
    return [p.player_id for p in xi]


def stats_without(*no_shows):
    return {pid: v for pid, v in EVERYONE_PLAYED.items() if pid not in no_shows}


def test_valid_formation():
    assert valid_formation([p.position for p in XI])
    assert not valid_formation(["GK"] * 2 + ["DEF"] * 4 + ["MID"] * 4 + ["FWD"])  # two keepers
    assert not valid_formation(["GK"] + ["DEF"] * 2 + ["MID"] * 5 + ["FWD"] * 3)  # only 2 defenders
    assert not valid_formation([p.position for p in XI[:10]])                     # only 10 players


def test_no_subs_when_everyone_played():
    xi, subs = apply_auto_subs(XI, BENCH, EVERYONE_PLAYED, confirmed_no_show=set(range(1, 16)))
    assert ids(xi) == ids(XI) and subs == []


def test_starter_whose_match_hasnt_started_is_not_subbed():
    # Regression (c2cf0af): 0 minutes because the game hasn't kicked off yet
    # must never count as a no-show.
    xi, subs = apply_auto_subs(XI, BENCH, stats_without(6), confirmed_no_show=set())
    assert ids(xi) == ids(XI) and subs == []


def test_confirmed_no_show_is_replaced_by_first_bench_outfielder():
    xi, subs = apply_auto_subs(XI, BENCH, stats_without(6), confirmed_no_show={6})
    assert ids(xi)[5] == 13          # 1st outfield sub (a DEF) comes in for the MID: 5-3-2 is valid
    assert subs == ["P13 in for P6"]


def test_bench_order_is_respected_and_unused_subs_skipped():
    # 1st sub didn't play either, so the 2nd sub comes on.
    xi, _ = apply_auto_subs(XI, BENCH, stats_without(6, 13), confirmed_no_show={6, 13})
    assert ids(xi)[5] == 14


def test_formation_limits_block_a_sub():
    # 3-5-2 with a defender out: the 1st sub is a MID, but 2 defenders is
    # illegal, so it's skipped and the DEF on the bench comes on instead.
    xi_352 = [P(1, "GK"), P(2, "DEF"), P(3, "DEF"), P(4, "DEF"),
              P(5, "MID"), P(6, "MID"), P(7, "MID"), P(8, "MID"), P(9, "MID"), P(10, "FWD"), P(11, "FWD")]
    bench = [P(12, "GK"), P(14, "MID"), P(13, "DEF"), P(15, "FWD")]
    xi, subs = apply_auto_subs(xi_352, bench, stats_without(2), confirmed_no_show={2})
    assert ids(xi)[1] == 13 and subs == ["P13 in for P2"]


def test_goalkeeper_only_replaced_by_goalkeeper():
    xi, subs = apply_auto_subs(XI, BENCH, stats_without(1), confirmed_no_show={1})
    assert ids(xi)[0] == 12 and subs == ["P12 in for P1"]
    # ...and if the reserve keeper didn't play, no outfielder goes in goal.
    xi, subs = apply_auto_subs(XI, BENCH, stats_without(1, 12), confirmed_no_show={1, 12})
    assert ids(xi)[0] == 1 and subs == []


def test_each_bench_player_used_once():
    xi, subs = apply_auto_subs(XI, BENCH, stats_without(6, 7, 8), confirmed_no_show={6, 7, 8})
    assert len(set(ids(xi))) == 11 and len(subs) == 3
