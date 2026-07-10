"""Pre-season baseline table (spec §5).

Groups last season's players (from `players_archive`) by (position, price_band) and
takes the median ppg / xgi_per90 / minutes-share of each band. Used to score players
with no usable archive history (new signings, promoted-team players).

Computed from archive rows once per rollover; recomputing on demand is cheap (~700
rows) and produces the identical dict, so we build it when PRESEASON scoring runs.
"""
from __future__ import annotations

import math
from statistics import median

from .config import HeuristicConfig

# 38 GWs × 90 minutes = the season's full minutes, for the minutes-share denominator.
_SEASON_MINUTES = 38 * 90


def price_band(price: float, step: float) -> float:
    """Lower edge of the £`step`-wide band containing `price` (e.g. 6.3 -> 6.0 at step 0.5)."""
    return math.floor(float(price) / step) * step


def _xgi_per90(xgi: float, minutes: int) -> float:
    if not minutes:
        return 0.0
    return (float(xgi) / minutes) * 90


def compute_baseline_table(archive_rows: list[dict], config: HeuristicConfig) -> dict:
    """Return {(position, band): {"ppg", "xgi_per90", "minutes_share"}}.

    `archive_rows` are dicts with position, price, ppg, xgi, minutes. Only rows with
    >= BASELINE_MIN_MINUTES are included (avoid fringe-player noise).
    """
    buckets: dict[tuple, dict[str, list[float]]] = {}
    for r in archive_rows:
        if (r.get("minutes") or 0) < config.BASELINE_MIN_MINUTES:
            continue
        band = price_band(r["price"], config.PRICE_BAND_STEP)
        key = (int(r["position"]), band)
        b = buckets.setdefault(key, {"ppg": [], "xgi_per90": [], "minutes_share": []})
        b["ppg"].append(float(r.get("ppg") or 0))
        b["xgi_per90"].append(_xgi_per90(r.get("xgi") or 0, r.get("minutes") or 0))
        b["minutes_share"].append(min(1.0, (r.get("minutes") or 0) / _SEASON_MINUTES))

    return {
        key: {
            "ppg": median(v["ppg"]),
            "xgi_per90": median(v["xgi_per90"]),
            "minutes_share": median(v["minutes_share"]),
        }
        for key, v in buckets.items()
    }


def baseline_lookup(table: dict, position: int, price: float, config: HeuristicConfig) -> dict | None:
    """Look up (position, band); if that band is empty, borrow the nearest populated
    band for the same position (§5: 'clamp to nearest populated band')."""
    if not table:
        return None
    band = price_band(price, config.PRICE_BAND_STEP)
    if (position, band) in table:
        return table[(position, band)]
    same_pos = [b for (pos, b) in table if pos == position]
    if not same_pos:
        return None
    nearest = min(same_pos, key=lambda b: abs(b - band))
    return table[(position, nearest)]
