"""The scoring engine (spec §1-§6).

    EP(horizon) = minutes_prob × Σ fixture_ep(f)
    fixture_ep(f) = (base_pts×W_BASE + attack_pts×W_ATTACK) × fdr_mult(f) × venue_mult(f) + cs_pts(f)

Two intentional design choices from CLAUDE.md / the spec are implemented AS SPECIFIED
and must not be "fixed":
  - base_pts and attack_pts deliberately double-count attacking returns; the 50/50
    W_BASE/W_ATTACK blend IS the correction (§2.3). We do not zero or dedupe either side.
  - cs_pts is ADDED AFTER fdr_mult/venue_mult, never scaled by them (§2.6): the CS
    probability already encodes the fixture, so multiplying again would double-punish it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

from .baseline import baseline_lookup, compute_baseline_table
from .config import DEFAULT_CONFIG, HeuristicConfig

IN_SEASON = "IN_SEASON"
PRESEASON = "PRESEASON"

_POS_NAME = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
_SEASON_MINUTES = 38 * 90


def _f(x) -> float:
    return float(x) if x is not None else 0.0


# ---------------------------------------------------------------------------
# Context — built once per sync, shared across all players (avoids re-filtering
# ~380 fixtures per player over ~800 players).
# ---------------------------------------------------------------------------

@dataclass
class EngineContext:
    config: HeuristicConfig
    mode: str
    anchor_gw: int
    horizon_gws: list[int]
    gws_elapsed: int
    teams_by_id: dict
    fixtures_by_team: dict          # team_id -> [fixture dicts with non-null gameweek]
    pos_xgi_fallback: dict          # position -> median xgi_per90 (in-season thin-minutes fallback)
    baseline: dict = field(default_factory=dict)          # (pos, band) -> rates (pre-season)
    promoted_team_ids: set = field(default_factory=set)
    archive_by_id: dict = field(default_factory=dict)     # id -> archive row
    archive_by_namepos: dict = field(default_factory=dict)  # (full_name, pos) -> [rows]
    archive_season: str | None = None
    euro_by_team_gw: dict = field(default_factory=dict)     # (team_id, gw) -> {competition, importance}


def _xgi_per90(xgi, minutes) -> float:
    m = _f(minutes)
    return (_f(xgi) / m) * 90 if m else 0.0


def build_context(players, teams, fixtures, gameweeks, *, config=DEFAULT_CONFIG,
                  mode, anchor=None, archive=None, euro_weeks=None) -> EngineContext:
    teams_by_id = {t["id"]: t for t in teams}

    fixtures_by_team: dict = {}
    for fx in fixtures:
        if fx["gameweek"] is None:  # postponed -> in no GW's set (§4)
            continue
        fixtures_by_team.setdefault(fx["home_team"], []).append(fx)
        fixtures_by_team.setdefault(fx["away_team"], []).append(fx)

    gw_ids = sorted(g["id"] for g in gameweeks)
    gws_elapsed = sum(1 for g in gameweeks if g.get("finished"))

    if anchor is None:
        if mode == PRESEASON:
            anchor = min(gw_ids) if gw_ids else 1
        else:
            unfinished = sorted(g["id"] for g in gameweeks if not g.get("finished"))
            anchor = unfinished[0] if unfinished else (gw_ids[-1] if gw_ids else 1)
    horizon_gws = [g for g in gw_ids if g >= anchor][:4] or [anchor]

    # In-season thin-minutes fallback: per-position median xgi_per90 among players with
    # a usable sample. (§2.3 "fall back to position-band average".)
    pos_samples: dict = {}
    for p in players:
        if _f(p.get("minutes")) >= config.MIN_MINUTES_XGI:
            pos_samples.setdefault(p["position"], []).append(
                _xgi_per90(p.get("xgi"), p.get("minutes"))
            )
    pos_xgi_fallback = {pos: median(v) for pos, v in pos_samples.items() if v}

    euro_by_team_gw: dict = {}
    for ew in (euro_weeks or []):
        euro_by_team_gw[(ew["team_id"], ew["gameweek"])] = {
            "competition": ew["competition"], "importance": ew["tie_importance"],
        }

    ctx = EngineContext(
        config=config, mode=mode, anchor_gw=anchor, horizon_gws=horizon_gws,
        gws_elapsed=gws_elapsed, teams_by_id=teams_by_id,
        fixtures_by_team=fixtures_by_team, pos_xgi_fallback=pos_xgi_fallback,
        euro_by_team_gw=euro_by_team_gw,
    )

    if archive:
        ctx.archive_season = archive[0].get("season")
        # An FPL element id is NOT stable across a season reset — the API
        # reassigns ids club-by-club each July. Measured on the live 2026-27
        # bootstrap against the archived 2025-26 season (2026-07-30): of 564
        # id-to-id joins, exactly 5 landed on the same person. Indexing the
        # archive by raw id therefore hands `_resolve_preseason` (and the
        # `_resolve_current` early-season blend) a confident WRONG match for
        # ~99% of players — Haaland's season attached to Mason Mount, and a
        # top-20 that looked plausible and was nonsense.
        #
        # So the id path is now VERIFIED, not assumed: an id only indexes when it
        # still points at the same name. A mismatch falls through to the
        # (full_name, position) match and then to the baseline, both of which
        # carry honesty flags. Fixed here, in the one index both resolvers read,
        # rather than in each resolver.
        current_name_by_id = {p["id"]: p.get("full_name") for p in players}
        for r in archive:
            if current_name_by_id.get(r["id"]) == r.get("full_name"):
                ctx.archive_by_id[r["id"]] = r
            ctx.archive_by_namepos.setdefault((r.get("full_name"), r["position"]), []).append(r)
        ctx.baseline = compute_baseline_table(archive, config)
        # Promoted teams: current teams with no archive players last season.
        archived_team_ids = {r["team_id"] for r in archive}
        ctx.promoted_team_ids = {t["id"] for t in teams if t["id"] not in archived_team_ids}

    return ctx


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class EPResult:
    player_id: int
    gameweek: int
    ep: float
    ep_next4: float
    components: dict

    def to_row(self) -> dict:
        """Map to an `ep_scores` insert dict (ep/ep_next4 as Decimal)."""
        return {
            "player_id": self.player_id,
            "gameweek": self.gameweek,
            "ep": Decimal(f"{self.ep:.2f}"),
            "ep_next4": Decimal(f"{self.ep_next4:.2f}"),
            "components": self.components,
        }


# ---------------------------------------------------------------------------
# Data resolution — decides where a player's rates come from (§5)
# ---------------------------------------------------------------------------

@dataclass
class _Resolved:
    data_basis: str
    match_method: str
    form: float
    ppg: float
    xgi_per90: float
    minutes_sample: int
    minutes_share: float
    attack_fallback: bool
    flags: list
    early_blend: bool = False


def _resolve_current(player, ctx) -> _Resolved:
    minutes = _f(player.get("minutes"))
    flags: list = []
    if minutes >= ctx.config.MIN_MINUTES_XGI:
        xgi90 = _xgi_per90(player.get("xgi"), minutes)
        attack_fallback = False
    else:
        xgi90 = ctx.pos_xgi_fallback.get(player["position"], 0.0)
        attack_fallback = True
        flags.append("thin_minutes_sample")

    denom = max(1, ctx.gws_elapsed) * 90
    share = min(1.0, max(0.0, minutes / denom))
    if ctx.gws_elapsed < ctx.config.EARLY_SEASON_GW:
        share = max(share, ctx.config.EARLY_SHARE_FLOOR)

    form = _f(player.get("form"))
    ppg = _f(player.get("ppg"))
    early_blend = False
    archive_row = ctx.archive_by_id.get(player["id"])
    if ctx.gws_elapsed < ctx.config.EARLY_SEASON_GW and archive_row is not None:
        # Pre-GW4: blend archive ppg 50/50 with current form (§2.2).
        ppg = _f(archive_row.get("ppg"))
        early_blend = True
        flags.append("early_season_blend")

    return _Resolved(
        data_basis="current", match_method="element_id",
        form=form, ppg=ppg, xgi_per90=xgi90,
        minutes_sample=int(minutes), minutes_share=share,
        attack_fallback=attack_fallback, flags=flags, early_blend=early_blend,
    )


def _resolve_preseason(player, ctx) -> _Resolved:
    cfg = ctx.config
    flags: list = []
    # 1. Archive match: element id first, then unique name+position.
    archive_row = ctx.archive_by_id.get(player["id"])
    method = "element_id"
    if archive_row is None:
        candidates = ctx.archive_by_namepos.get((player.get("full_name"), player["position"]), [])
        if len(candidates) == 1:
            archive_row, method = candidates[0], "name_position"

    if archive_row is not None:
        a_min = _f(archive_row.get("minutes"))
        ppg = _f(archive_row.get("ppg"))
        # Apply the xGI minutes floor to the archive path too (§9 fix, 2026-07-10):
        # a handful of archive minutes explodes xgi_per90, so mirror _resolve_current
        # and fall back to the (position, price-band) baseline xgi below the floor.
        if a_min >= cfg.MIN_MINUTES_XGI:
            xgi90 = _xgi_per90(archive_row.get("xgi"), a_min)
            attack_fallback = False
        else:
            base = baseline_lookup(ctx.baseline, player["position"], _f(player.get("price")), cfg)
            xgi90 = base["xgi_per90"] if base else 0.0
            attack_fallback = True
            flags.append("thin_minutes_sample")
        return _Resolved(
            data_basis=f"archive_{ctx.archive_season}", match_method=method,
            form=ppg, ppg=ppg,  # form is meaningless pre-season -> archive ppg (§5.1)
            xgi_per90=xgi90,
            minutes_sample=int(a_min),  # real archive minutes, even when xgi fell back
            minutes_share=min(1.0, a_min / _SEASON_MINUTES),
            attack_fallback=attack_fallback, flags=flags,
        )

    # 2/3. No archive match -> baseline table (× discount if promoted).
    base = baseline_lookup(ctx.baseline, player["position"], _f(player.get("price")), cfg)
    promoted = player["team_id"] in ctx.promoted_team_ids
    if base is None:
        base = {"ppg": 0.0, "xgi_per90": 0.0, "minutes_share": cfg.EARLY_SHARE_FLOOR}
    discount = cfg.PROMOTED_DISCOUNT if promoted else 1.0
    flags.append("promoted_team_fallback" if promoted else "no_history_fallback")
    return _Resolved(
        data_basis="baseline_fallback", match_method="none",
        form=base["ppg"] * discount, ppg=base["ppg"] * discount,
        xgi_per90=base["xgi_per90"] * discount,
        minutes_sample=0, minutes_share=base["minutes_share"],
        attack_fallback=True, flags=flags,
    )


# ---------------------------------------------------------------------------
# Component math
# ---------------------------------------------------------------------------

def _minutes_prob(player, share) -> tuple[float, str, list]:
    status = (player.get("status") or "a")[:1]
    chance = player.get("chance_of_playing")
    flags: list = []
    if status == "a":
        status_factor = 1.0
    elif status == "d":
        status_factor = (chance / 100.0) if chance is not None else 0.75
        flags.append(f"injury_doubt:{chance if chance is not None else 75}%")
    else:  # i, s, u, n
        status_factor = (chance / 100.0) if chance is not None else 0.0
        flags.append(f"unavailable:{status}")

    mp = status_factor * (0.5 + 0.5 * share)
    basis = f"status={status}, {round(share * 100)}% minutes share"
    return mp, basis, flags


def _base_pts(res, ctx) -> tuple[float, dict]:
    cfg = ctx.config
    if res.early_blend:
        base = 0.5 * res.ppg + 0.5 * res.form
        weights = [0.5, 0.5]
    else:
        base = cfg.W_FORM * res.form + cfg.W_PPG * res.ppg
        weights = [cfg.W_FORM, cfg.W_PPG]
    return base, {"form": round(res.form, 2), "ppg": round(res.ppg, 2), "weights": weights}


def _attack_pts(res, position, ctx) -> tuple[float, dict]:
    cfg = ctx.config
    goal_v = cfg.GOAL_VALUE[position]
    blended = cfg.RETURN_BLEND[0] * goal_v + cfg.RETURN_BLEND[1] * cfg.ASSIST_VALUE
    attack = res.xgi_per90 * blended
    return attack, {
        "xgi_per90": round(res.xgi_per90, 3),
        "minutes_sample": res.minutes_sample,
        "fallback": res.attack_fallback,
    }


def _fixture_ep(base, attack, position, fx, team_id, ctx) -> dict:
    cfg = ctx.config
    is_home = fx["home_team"] == team_id
    fdr = fx["home_difficulty"] if is_home else fx["away_difficulty"]
    venue_mult = cfg.VENUE_HOME if is_home else cfg.VENUE_AWAY
    fdr_mult = cfg.fdr_mult(fdr)

    cs_prob = cfg.cs_prob(fdr) + (cfg.CS_VENUE_ADJ if is_home else -cfg.CS_VENUE_ADJ)
    lo, hi = cfg.CS_PROB_CLAMP
    cs_prob = min(hi, max(lo, cs_prob))
    # W_CS weights the expected-CS term only (completes the §2.3 double-count
    # correction for CS); the flat GK save-base is a separate lever, unscaled.
    cs_pts = cfg.W_CS * cs_prob * cfg.CS_VALUE[position]
    if position == 1:  # GK save-points baseline, flat per fixture
        cs_pts += cfg.SAVE_PTS_BASE

    core = (base * cfg.W_BASE + attack * cfg.W_ATTACK) * fdr_mult * venue_mult
    fixture_ep = max(0.0, core + cs_pts)  # floor at 0 (§1)

    opponent = fx["away_team"] if is_home else fx["home_team"]
    opp = ctx.teams_by_id.get(opponent, {})
    return {
        "gw": fx["gameweek"],
        "opponent_short": opp.get("short_name", "?"),
        "is_home": is_home,
        "fdr": fdr,
        "fdr_mult": round(fdr_mult, 3),
        "venue_mult": round(venue_mult, 3),
        "cs_prob": round(cs_prob, 3),
        "cs_pts": round(cs_pts, 3),
        "fixture_ep": round(fixture_ep, 3),
    }


# ---------------------------------------------------------------------------
# Per-player scoring
# ---------------------------------------------------------------------------

def score_player(player, ctx: EngineContext) -> EPResult:
    """Score one player over the context's horizon.

    Realizes the spec's `score_player(player, fixtures, teams, config, mode, archive)`:
    fixtures/teams/config/mode/horizon are bundled into `ctx` so they are computed once
    per sync rather than per player. Returns an EPResult with the full §3 components blob.
    """
    cfg = ctx.config
    position = player["position"]

    res = _resolve_preseason(player, ctx) if ctx.mode == PRESEASON else _resolve_current(player, ctx)

    minutes_prob, minutes_basis, mp_flags = _minutes_prob(player, res.minutes_share)
    if res.data_basis != "current":
        minutes_basis += f" ({res.data_basis})"

    # European-week rotation dampener (§1.2): flag always; dampen minutes_prob only
    # for non-nailed players (share < EURO_MIN_SHARE). Keyed on the anchor GW.
    euro = ctx.euro_by_team_gw.get((player["team_id"], ctx.anchor_gw))
    if euro:
        mp_flags.append(f"european_fixture_week:{euro['competition']}:{euro['importance']}")
        if res.minutes_share < cfg.EURO_MIN_SHARE:
            minutes_prob *= cfg.EURO_ROTATION_DAMP.get(euro["importance"], 1.0)

    base, base_inputs = _base_pts(res, ctx)
    attack, attack_inputs = _attack_pts(res, position, ctx)

    flags = list(res.flags) + mp_flags

    # Walk the horizon GW by GW; blanks contribute 0, doubles sum (§4).
    team_fixtures = ctx.fixtures_by_team.get(player["team_id"], [])
    fixture_components: list = []
    ep_anchor_sum = 0.0
    ep_next4_sum = 0.0
    for gw in ctx.horizon_gws:
        gw_fx = [fx for fx in team_fixtures if fx["gameweek"] == gw]
        if len(gw_fx) == 0:
            flags.append(f"blank_gw:{gw}")
            continue
        if len(gw_fx) >= 2:
            flags.append(f"double_gw:{gw}")
        for fx in gw_fx:
            fc = _fixture_ep(base, attack, position, fx, player["team_id"], ctx)
            fixture_components.append(fc)
            ep_next4_sum += fc["fixture_ep"]
            if gw == ctx.anchor_gw:
                ep_anchor_sum += fc["fixture_ep"]

    ep = minutes_prob * ep_anchor_sum
    ep_next4 = minutes_prob * ep_next4_sum

    components = {
        "config_version": cfg.config_version,
        "data_basis": res.data_basis,
        "match_method": res.match_method,
        "minutes_prob": round(minutes_prob, 3),
        "minutes_basis": minutes_basis,
        "base": round(base, 3),
        "base_inputs": base_inputs,
        "attack": round(attack, 3),
        "attack_inputs": attack_inputs,
        "fixtures": fixture_components,
        "flags": flags,
        "ep": round(ep, 2),
        "ep_next4": round(ep_next4, 2),
    }
    return EPResult(player["id"], ctx.anchor_gw, ep, ep_next4, components)


def score_all(players, teams, fixtures, gameweeks, *, config=DEFAULT_CONFIG,
              mode, anchor=None, archive=None, euro_weeks=None) -> list[EPResult]:
    """Score every player. Called by sync_fpl() step 6. Returns EPResult list."""
    ctx = build_context(players, teams, fixtures, gameweeks,
                        config=config, mode=mode, anchor=anchor, archive=archive,
                        euro_weeks=euro_weeks)
    return [score_player(p, ctx) for p in players]
