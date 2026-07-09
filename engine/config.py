"""The single versioned config object (spec §7).

Every tunable weight/table lives here; NO magic numbers appear inline in the engine.
`config_version` is stamped into every components blob so any historical EP is
reproducible and attributable.

The §7 "contains exactly" list is the core tuning surface. A few structural tables
named in §2/§5 (CS_VALUE, the cs-prob clamp, the price-band step, the archive minutes
threshold) are included here too, because the overriding rule is "no magic number
inline, anywhere" — they are tunables and must not be hard-coded in the formulas.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HeuristicConfig:
    config_version: str = "v1.0"

    # base/attack blend and their internal weights
    W_BASE: float = 0.5
    W_ATTACK: float = 0.5
    W_FORM: float = 0.6
    W_PPG: float = 0.4

    # fixture difficulty -> attacking multiplier
    FDR_TABLE: dict = field(default_factory=lambda: {1: 1.30, 2: 1.20, 3: 1.00, 4: 0.82, 5: 0.68})

    # venue multipliers
    VENUE_HOME: float = 1.08
    VENUE_AWAY: float = 0.94

    # clean-sheet probability by this team's FDR, venue adjustment, and clamp
    CS_PROB_TABLE: dict = field(default_factory=lambda: {1: 0.50, 2: 0.42, 3: 0.30, 4: 0.20, 5: 0.12})
    CS_VENUE_ADJ: float = 0.04
    CS_PROB_CLAMP: tuple = (0.05, 0.60)
    CS_VALUE: dict = field(default_factory=lambda: {1: 4, 2: 4, 3: 1, 4: 0})  # GK/DEF/MID/FWD
    SAVE_PTS_BASE: float = 0.6  # flat GK save-points baseline per fixture

    # attacking return valuation
    GOAL_VALUE: dict = field(default_factory=lambda: {1: 6, 2: 6, 3: 5, 4: 4})  # GK/DEF/MID/FWD
    ASSIST_VALUE: int = 3
    RETURN_BLEND: tuple = (0.65, 0.35)  # goal / assist weighting

    # pre-season / fallback
    PROMOTED_DISCOUNT: float = 0.85
    MIN_MINUTES_XGI: int = 270      # below this, xgi_per90 is too noisy -> position fallback
    BASELINE_MIN_MINUTES: int = 900  # archive players below this excluded from baseline
    PRICE_BAND_STEP: float = 0.5     # £m band width for the baseline table

    # early-season blending
    EARLY_SEASON_GW: int = 4
    EARLY_SHARE_FLOOR: float = 0.7

    def fdr_mult(self, fdr: int) -> float:
        return self.FDR_TABLE.get(fdr, 1.0)

    def cs_prob(self, fdr: int) -> float:
        return self.CS_PROB_TABLE.get(fdr, 0.30)


DEFAULT_CONFIG = HeuristicConfig()
