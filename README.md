# FPL Draft League (private)

A companion site for a private FPL Draft mini-league. Drafting, transfers,
weekly lineups and scoring all happen as normal on the official FPL Draft
site - this app pulls each entry's real results and builds a custom
two-division head-to-head season on top, since FPL Draft doesn't offer any
of that itself.

## What it does

- Two divisions of real FPL Draft managers, drafted as normal on the official site.
- One ~36-game season generated up front and locked in (your division x3, the
  other division x2, plus a few extra games), followed by a cross-division
  playoff on the final two gameweeks.
- A **League** table (head-to-head: win 3, draw 1, tie-broken on total points)
  and a **Fixtures** grid, both built from each entry's actual FPL Draft results.
- Click into any fixture once its gameweek has started to see both squads
  side by side - who's played, live points, auto-subs (a starter who didn't
  play, replaced by a bench player who did), and transfers made that
  gameweek - all reconstructed from the official site's own data. Once a
  gameweek finishes, that view is frozen for good: later transfers on the
  real site can never rewrite what already happened.
- Scores update automatically while a gameweek's live - checked roughly every
  60 seconds, but only while a real match from that gameweek is actually
  being played (not for the dead time between matches), and paused whenever
  the tab isn't visible. Nothing is invented: while FPL Draft hasn't
  finalised a gameweek's score yet, this app computes its own live-as-close-
  as-possible estimate from real per-player stats; once FPL Draft locks the
  official number in, that's what's shown instead.
- **Hall of Fame**: trophy cabinet and all-time records across every season
  this site has tracked.
- Everything else lives behind an admin-only **Setup** page (⚙️ icon): create
  a season, enter each division's FPL Draft team IDs, generate the schedule
  (one-time, then locked), rename things, back up/restore, and force a sync.

## Stack

- Python + FastAPI (web/API)
- SQLModel - SQLite locally, or Postgres (e.g. Neon) via `DATABASE_URL` in production
- httpx, talking to both the official classic FPL API (player/team reference
  data, live per-gameweek player stats) and the FPL Draft API (league
  entries, results, picks)

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Sync FPL reference data

Pulls teams, players and gameweeks - needed to resolve names/positions in the
fixture view - into the database. The app also does this itself on startup
if the Player table is ever found empty, so this is mainly for local dev:

```bash
./.venv/bin/python -m app.sync            # everything, incl. all finished gameweeks' stats
./.venv/bin/python -m app.sync --no-stats # reference data only (fast) - all this app actually needs
```

## Run the app

```bash
./.venv/bin/uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/** for the web UI:
- **League** - the head-to-head table, playoff bracket, and a live card while a gameweek's in progress
- **Fixtures** - the full season's schedule; click any started-or-finished fixture for the head-to-head detail
- **Hall of Fame** - trophy cabinet and all-time records
- **Rules** - how the format works
- **Setup** (⚙️, admin-only) - create a season, enter each division's FPL Draft team IDs, generate the schedule, sync, rename the league/managers, start a new season, back up/restore

Also available:
- http://127.0.0.1:8000/health - row counts (proof data landed)
- http://127.0.0.1:8000/docs - interactive docs for the `/api/custom/*` endpoints that actually power the site

## Deployment

Configured for Render (see `render.yaml`) as a free web service, with
`DATABASE_URL` pointed at a Postgres database (e.g. Neon's free tier) so data
survives restarts and deploys. Without `DATABASE_URL` set, it falls back to a
local SQLite file - fine for development, but that file won't persist across
a Render deploy.
