"""Formation rules shared by the auto-sub logic in app.scoring."""
from __future__ import annotations

# Valid FPL formation bounds for the 11 starters (GK is always exactly 1).
FORMATION_BOUNDS = {"GK": (1, 1), "DEF": (3, 5), "MID": (2, 5), "FWD": (1, 3)}
