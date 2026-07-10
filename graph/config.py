"""Layer 3 tunables and version constants (kept out of the node bodies).

The cache key (§4) mixes several *content version* tags so a change in any input
that affects brief content forces a rerun. Two of those tags — curated team
notes and European-week schedules — are Layer 4/5 concerns whose curated tables
DO NOT EXIST YET. We stamp explicit placeholder versions here so the hash is
already shaped correctly; when those tables land, replace the placeholders with
the real version source and the cache will invalidate exactly once.
"""
from __future__ import annotations

# Placeholder content-version tags (see module docstring). Bump only when the
# real curated sources arrive.
# NOTE for future-us: when the real team_notes / european_weeks tables land in
# Layer 4 and these constants change, EVERY cached brief invalidates exactly once
# (the hash shifts for all rows). That mass cache miss is by design, not a bug.
CURATED_NOTES_VERSION = "notes-v0-placeholder"
EURO_WEEKS_VERSION = "euro-v0-placeholder"

# Detection thresholds for SquadAnalyst.Diagnosis (§2.1). No magic numbers inline.
FIXTURE_CLIFF_SWING = -0.8      # team swing below this => fixture_cliff problem
COLD_FORM_RATIO = 0.6           # form < ratio * ppg (with minutes_prob high) => cold_form
COLD_FORM_MIN_PROB = 0.8        # ...only when the player is nailed
DEAD_BENCH_EP_NEXT4 = 4.0       # bench player below this ep_next4 => dead_bench_spot
BLANK_EXPOSURE_STARTERS = 4     # >= this many starters blanking in next4 => blank_exposure
INJURY_DOUBT_CHANCE = 75        # chance <= this on a doubt flag => injury_doubt problem

# MarketScout (§2.2)
REPLACEMENT_TOP_N = 3
DIFFERENTIAL_TOP_N = 5
DIFFERENTIAL_MAX_OWNERSHIP = 10.0
TEMPLATE_MIN_OWNERSHIP = 30.0
TEMPLATE_TOP_N = 5
MAX_PER_CLUB = 3
SALE_PRICE_EDGE = 0.3           # warn when a replacement is within £this of affordability

# FixturePlanner (§2.3)
FIXTURE_HORIZON = 6             # next-6-GW window
SWING_ENTER = 0.8               # swing >= => entering_good_run
SWING_LEAVE = -0.8              # swing <= => leaving_good_run

# Strategist (§2.4)
TRANSFER_MIN_DELTA = 2.0        # only recommend a transfer at ep_next4_delta >= this
CAPTAIN_DIFF_EP_GAP = 0.5       # vice within this ep of captain ...
CAPTAIN_DIFF_MAX_OWNERSHIP = 15.0  # ...and below this ownership => differential_note
DOUBLE_GW_CHIP_STARTERS = 2     # >= this many squad players with a double => chip note
WATCHLIST_N = 3

# Live FPL fetch policy (§3)
FETCH_TIMEOUT_S = 5
FETCH_RETRIES = 2
