# FPL Draft League (private)

A self-hosted Fantasy Premier League draft clone for a private mates' league,
with a custom two-division structure and a mid-season split (points carry over,
squads re-drafted).

Scoring is **not** computed by us - we pull each player's actual per-gameweek
points from the public FPL API, so a real match feed is never needed.

## Stack

- Python + FastAPI (web/API)
- SQLite via SQLModel (storage, single file `fpl_draft.db`)
- httpx (FPL API client)

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install fastapi "uvicorn[standard]" sqlmodel httpx
```

## Sync FPL data

Pulls teams, players, gameweeks, and per-gameweek points into `fpl_draft.db`:

```bash
./.venv/bin/python -m app.sync            # everything, incl. all finished gameweeks
./.venv/bin/python -m app.sync --no-stats # reference data only (fast)
./.venv/bin/python -m app.sync --max-gw 5 # per-gameweek stats up to GW5 only
```

## Run the app

```bash
./.venv/bin/uvicorn app.main:app --reload
```

Then open **http://127.0.0.1:8000/** for the web UI (each page has a help panel):
- **Setup** - create a season and its divisions (one division = one real FPL Draft
  mini-league); delete divisions here too
- **Live Tables** - link each division to its real league via a league or team URL
  and view the live head-to-head table (finished gameweeks only)
- **Season Split** - explains promotion/relegation + carry-over (the combined
  cross-division engine is the next feature)

> Drafting, transfers and scoring happen on the official FPL Draft site - this app
> mirrors that data and builds the custom two-division tables on top. In-app
> drafting was removed in favour of this approach.

Also available:
- http://127.0.0.1:8000/health - row counts (proof data landed)
- http://127.0.0.1:8000/docs - interactive API docs

## League + draft API

Under `/api` (see `/docs`): create a season, managers, and divisions; assign
members (list order = draft seed); then run a snake draft.

```
POST /api/seasons                         {name, split_gameweek}
POST /api/managers                        {name}
POST /api/divisions                       {season_id, stage, tier, name}
POST /api/divisions/{id}/members          {manager_ids: [...]}   # order = seed
POST /api/divisions/{id}/draft/start
GET  /api/divisions/{id}/draft            # live state: who's on the clock
GET  /api/divisions/{id}/available        # undrafted players, best first
POST /api/divisions/{id}/draft/pick       {player_id, manager_id?}
POST /api/divisions/{id}/draft/autopick
GET  /api/divisions/{id}/board            # picks in order
GET  /api/divisions/{id}/rosters          # each manager's squad
```

Rules enforced by the engine: snake order, one owner per player per division,
and legal 2/5/5/3 squads (15 players).

## Scoring + standings

```
PUT  /api/divisions/{id}/managers/{mid}/lineup            {gameweek_id, starters[11], bench[]}
GET  /api/divisions/{id}/managers/{mid}/lineup?gameweek_id=N
GET  /api/divisions/{id}/managers/{mid}/gameweek/{gw}     # score + which auto-subs fired
GET  /api/divisions/{id}/standings                        # cumulative table for the stage
```

Draft-mode scoring (no captain): a manager scores their starting XI's FPL points
for the gameweek, with automatic substitutions - a starter who played 0 minutes
is replaced, in bench order, by a bench player who played, keeping a valid
formation (GK-for-GK, outfield-for-outfield). If no lineup is set for a gameweek,
a sensible default best-XI is used. A division's standings sum over the gameweeks
in its stage (stage 1 = up to `split_gameweek`, stage 2 = after).

## Status

**Milestone 1 (done):** project skeleton + FPL data sync.
**Milestone 2 (done):** seasons, managers, two-division structure, snake draft engine + API.
**Milestone 3 (done):** lineups, auto-subs, per-gameweek scoring, division standings.
**Milestone 4 (done):** mid-season split (promotion/relegation + re-draft) and the combined carry-over season table.

The full season loop now works: draft two divisions -> score stage 1 ->
promote/relegate at the split -> re-draft stage 2 -> combined table.
**Milestone 5 (done):** web UI (Setup / Draft / Standings / Season Split) served at `/`.

Next candidates: waivers / free-agent transfers between gameweeks, a real-time
(WebSocket) live draft room, and per-gameweek lineup editing in the UI.

## Mid-season split

```
GET  /api/seasons/{id}/split/preview?n_swap=2   # who'd go up/down (no changes)
POST /api/seasons/{id}/split/apply?n_swap=2     # create stage-2 divisions, advance season
GET  /api/seasons/{id}/table                    # combined table, points carry over
```

Bottom `n_swap` of tier 1 swap with top `n_swap` of tier 2. Stage-2 divisions are
seeded worst-first (by stage-1 points) and then drafted via the normal draft
endpoints. The combined table sums each manager's stage-1 and stage-2 points.
