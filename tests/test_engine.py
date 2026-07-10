"""Layer 2 engine tests (spec §8) + guards on the two intentional design choices."""
import time
from dataclasses import replace

import pytest

from engine.config import DEFAULT_CONFIG
from engine.scoring import build_context, score_all, score_player
from fpl.parsers import parse_fixture, parse_player, parse_team

# --- tiny builders for controlled, deterministic inputs --------------------

def team(tid, short):
    return {"id": tid, "short_name": short, "name": short}


def gameweeks(n_finished=10, total=14):
    return [
        {"id": i, "name": f"GW{i}", "finished": i <= n_finished, "is_current": False,
         "is_next": i == n_finished + 1}
        for i in range(1, total + 1)
    ]


def player(pid=1, team_id=1, position=3, minutes=900, status="a", chance=None,
           form=6.0, ppg=6.0, xgi=8.0, price=9.0, full_name="Prem Attacker"):
    return {
        "id": pid, "web_name": full_name, "full_name": full_name, "team_id": team_id,
        "position": position, "price": price, "total_points": 120, "form": form,
        "ppg": ppg, "minutes": minutes, "status": status, "chance_of_playing": chance,
        "news": "", "ownership_pct": 20.0, "xg": xgi * 0.6, "xa": xgi * 0.4, "xgi": xgi,
        "xgc": 10.0, "transfers_in_event": 0, "transfers_out_event": 0,
    }


def fixture(fid, gw, home, away, hdiff=3, adiff=3):
    return {"id": fid, "gameweek": gw, "home_team": home, "away_team": away,
            "home_difficulty": hdiff, "away_difficulty": adiff, "kickoff": None,
            "finished": False, "home_score": None, "away_score": None}


TEAMS = [team(1, "ARS"), team(2, "BUR"), team(3, "CHE")]


def ctx_inseason(fixtures, anchor=11, archive=None):
    # single premium attacker on team 1 by default; caller supplies fixtures/players
    return build_context([player()], TEAMS, fixtures, gameweeks(),
                         mode="IN_SEASON", anchor=anchor, archive=archive)


# --- §8.1 nailed premium attacker, easy home fixture -> ep in 5–9 ----------

def test_premium_attacker_easy_home_sane_band():
    fx = [fixture(1, 11, 1, 2, hdiff=2)]  # ARS home vs BUR, FDR 2
    ctx = ctx_inseason(fx)
    r = score_player(player(), ctx)
    assert 5.0 <= r.ep <= 9.0, r.ep
    # base and attack BOTH contribute (double-count is intentional, §2.3)
    assert r.components["base"] > 0 and r.components["attack"] > 0


# --- §8.2 blank GW -> ep = 0 with flag -------------------------------------

def test_blank_gw_zero_with_flag():
    # team 1 has NO fixture in the anchor GW (11); only a later one.
    fx = [fixture(1, 12, 1, 2)]
    ctx = ctx_inseason(fx)
    r = score_player(player(), ctx)
    assert r.ep == 0.0
    assert "blank_gw:11" in r.components["flags"]
    assert r.ep_next4 > 0  # the GW12 fixture still lifts the 4-GW horizon


# --- §8.3 double GW ≈ sum of two singles, one flag -------------------------

def test_double_gw_sums_two_fixtures():
    single = [fixture(1, 11, 1, 2, hdiff=3)]
    double = [fixture(1, 11, 1, 2, hdiff=3), fixture(2, 11, 1, 3, hdiff=3)]
    r_single = score_player(player(), ctx_inseason(single))
    r_double = score_player(player(), ctx_inseason(double))
    assert r_double.components["flags"].count("double_gw:11") == 1
    assert len([f for f in r_double.components["fixtures"] if f["gw"] == 11]) == 2
    # identical fixtures -> double ep is ~2x the single
    assert r_double.ep == pytest.approx(2 * r_single.ep, rel=1e-6)


# --- §8.4 flagged doubt (d, 75%) -> minutes_prob ≈ 0.75 × share term -------

def test_injury_doubt_scales_minutes_prob():
    fx = [fixture(1, 11, 1, 2, hdiff=2)]
    ctx = ctx_inseason(fx)
    r = score_player(player(status="d", chance=75, minutes=900), ctx)  # share term = 1.0
    assert r.components["minutes_prob"] == pytest.approx(0.75, abs=1e-6)
    assert "injury_doubt:75%" in r.components["flags"]


def test_doubt_null_chance_defaults_075():
    fx = [fixture(1, 11, 1, 2)]
    r = score_player(player(status="d", chance=None, minutes=900), ctx_inseason(fx))
    assert r.components["minutes_prob"] == pytest.approx(0.75, abs=1e-6)


# --- §8.5 NULL chance with status 'a' -> unflagged, full availability ------

def test_null_chance_status_a_is_unflagged():
    fx = [fixture(1, 11, 1, 2)]
    r = score_player(player(status="a", chance=None, minutes=900), ctx_inseason(fx))
    assert r.components["minutes_prob"] == pytest.approx(1.0, abs=1e-6)
    assert not any(f.startswith(("injury_doubt", "unavailable")) for f in r.components["flags"])


# --- §8.6 pre-season promoted player -> baseline path, discount, flag ------

def _archive_established():
    # Two established clubs (teams 1 & 2) with enough >=900-min players to form bands.
    rows = []
    pid = 100
    for tid in (1, 2):
        for _ in range(3):
            pid += 1
            rows.append({"id": pid, "full_name": f"Old{pid}", "team_id": tid, "position": 3,
                         "price": 9.0, "ppg": 5.0, "xgi": 6.0, "minutes": 3000,
                         "season": "2025-26"})
    return rows


def test_preseason_promoted_baseline_discount():
    archive = _archive_established()
    fx = [fixture(1, 1, 3, 1, hdiff=3)]  # promoted CHE(3) home vs ARS(1) in GW1
    gws = [{"id": i, "name": f"GW{i}", "finished": False, "is_current": False,
            "is_next": i == 1} for i in range(1, 6)]
    # promoted player (team 3 has no archive rows -> promoted); no archive id match
    promoted = player(pid=9001, team_id=3, position=3, price=9.0, full_name="New Promo")
    # established new-signing (team 1) with an id not in archive -> no_history_fallback
    new_signing = player(pid=9002, team_id=1, position=3, price=9.0, full_name="New Signing")

    ctx = build_context([promoted, new_signing], TEAMS, fx, gws,
                        mode="PRESEASON", anchor=1, archive=archive)
    rp = score_player(promoted, ctx)
    rn = score_player(new_signing, ctx)

    assert rp.components["data_basis"] == "baseline_fallback"
    assert rp.components["match_method"] == "none"
    assert "promoted_team_fallback" in rp.components["flags"]
    assert "no_history_fallback" in rn.components["flags"]
    # discount applied: promoted base == established-baseline base × 0.85
    assert rp.components["base"] == pytest.approx(rn.components["base"] * 0.85, rel=1e-6)


# --- xGI minutes floor on the archive path (§9 fix, config v1.2) -----------

def _archive_mid_band(price=4.7, minutes=3000, xgi=6.0, ppg=3.0):
    """Enough well-sampled MID rows to populate the (MID, band) baseline.
    xgi_per90 = xgi/minutes*90 = 6.0/3000*90 = 0.18 by default."""
    rows = []
    pid = 200
    for _ in range(3):
        pid += 1
        rows.append({"id": pid, "full_name": f"Mid{pid}", "team_id": 1, "position": 3,
                     "price": price, "ppg": ppg, "xgi": xgi, "minutes": minutes,
                     "season": "2025-26"})
    return rows


def _preseason_ctx(archive, players):
    gws = [{"id": i, "name": f"GW{i}", "finished": False, "is_current": False,
            "is_next": i == 1} for i in range(1, 6)]
    fx = [fixture(1, 1, 1, 2, hdiff=3)]  # team 1 home vs team 2 in GW1
    return build_context(players, TEAMS, fx, gws, mode="PRESEASON", anchor=1, archive=archive)


def test_preseason_thin_archive_minutes_xgi_falls_back_to_baseline():
    """A 2-minute archive player with absurd raw xGI must fall back to the band
    baseline xgi_per90, be flagged, and NOT ride an inflated attack term."""
    archive = _archive_mid_band()  # (MID, band 4.5) baseline xgi_per90 == 0.18
    # thin cameo: 2 minutes, xgi 0.08 -> raw xgi_per90 would be 0.08/2*90 = 3.6
    archive.append({"id": 9500, "full_name": "Thin Cameo", "team_id": 1, "position": 3,
                    "price": 4.9, "ppg": 1.0, "xgi": 0.08, "minutes": 2, "season": "2025-26"})
    thin = player(pid=9500, team_id=1, position=3, price=4.9, full_name="Thin Cameo")

    ctx = _preseason_ctx(archive, [thin])
    r = score_player(thin, ctx)
    ai = r.components["attack_inputs"]
    assert ai["fallback"] is True
    assert ai["xgi_per90"] == pytest.approx(0.18, abs=1e-9)  # baseline, not 3.6
    assert ai["minutes_sample"] == 2                          # real archive minutes kept
    assert "thin_minutes_sample" in r.components["flags"]
    # attack rides the baseline xgi, not the exploded raw value.
    assert r.components["attack"] < 1.0


def test_preseason_well_sampled_archive_unperturbed_by_xgi_floor():
    """A 2000+ minute archive player must be identical to pre-fix behaviour:
    raw xgi_per90, no fallback, no thin flag (the fix must not touch the
    well-sampled population)."""
    archive = _archive_mid_band()
    # 2500-min player, id-matched; raw xgi_per90 = 8.0/2500*90 = 0.288
    archive.append({"id": 9600, "full_name": "Regular Starter", "team_id": 1, "position": 3,
                    "price": 6.0, "ppg": 5.0, "xgi": 8.0, "minutes": 2500, "season": "2025-26"})
    regular = player(pid=9600, team_id=1, position=3, price=6.0, full_name="Regular Starter")

    ctx = _preseason_ctx(archive, [regular])
    r = score_player(regular, ctx)
    # Pre-fix the archive branch always produced exactly: raw xgi_per90, fallback False,
    # no thin flag. Asserting those three proves bit-for-bit non-perturbation.
    assert r.components["attack_inputs"] == {
        "xgi_per90": round(8.0 / 2500 * 90, 3),  # 0.288, raw
        "minutes_sample": 2500,
        "fallback": False,
    }
    assert "thin_minutes_sample" not in r.components["flags"]


# --- §8.7 components schema validates for EVERY scored player --------------

_REQUIRED_TOP = {
    "config_version": str, "data_basis": str, "match_method": str,
    "minutes_prob": (int, float), "minutes_basis": str, "base": (int, float),
    "base_inputs": dict, "attack": (int, float), "attack_inputs": dict,
    "fixtures": list, "flags": list, "ep": (int, float), "ep_next4": (int, float),
}


def validate_components(c: dict) -> None:
    for key, typ in _REQUIRED_TOP.items():
        assert key in c, f"missing {key}"
        assert isinstance(c[key], typ), f"{key} wrong type: {type(c[key])}"
    assert "stub" not in c, "real engine must never emit the stub flag"  # smoke test
    assert c["data_basis"] in ("current",) or c["data_basis"].startswith(("archive_", "baseline"))
    assert c["match_method"] in ("element_id", "name_position", "none")
    assert set(c["base_inputs"]) >= {"form", "ppg", "weights"}
    assert set(c["attack_inputs"]) >= {"xgi_per90", "minutes_sample", "fallback"}
    for f in c["fixtures"]:
        assert set(f) >= {"gw", "opponent_short", "is_home", "fdr", "fdr_mult",
                          "venue_mult", "cs_prob", "cs_pts", "fixture_ep"}
        assert f["fixture_ep"] >= 0  # floored at 0


@pytest.fixture
def real_assets():
    import json
    from pathlib import Path
    root = Path(__file__).parent / "assets"
    bs = json.loads((root / "bootstrap_2025-26.json").read_text(encoding="utf-8"))
    fx = json.loads((root / "fixtures_2025-26.json").read_text(encoding="utf-8"))
    players = [parse_player(e) for e in bs["elements"]]
    teams = [parse_team(t) for t in bs["teams"]]
    fixtures = [parse_fixture(f) for f in fx]
    gws = [{"id": e["id"], "name": e["name"], "finished": e["finished"],
            "is_current": e["is_current"], "is_next": e["is_next"]} for e in bs["events"]]
    return players, teams, fixtures, gws


def test_all_players_validate_and_perf(real_assets):
    players, teams, fixtures, gws = real_assets
    # Score the full population against the opening fixture run (GW1-4).
    t0 = time.monotonic()
    results = score_all(players, teams, fixtures, gws, mode="IN_SEASON", anchor=1)
    elapsed = time.monotonic() - t0
    assert len(results) == len(players)
    for r in results:
        validate_components(r.components)
    assert elapsed < 5.0, f"scoring took {elapsed:.2f}s (budget 5s)"


# --- guards on the two intentional design choices --------------------------

def test_clean_sheet_added_after_multipliers_not_scaled():
    """cs_pts must be additive AFTER fdr/venue, never scaled by them (§2.6)."""
    fx = [fixture(1, 11, 1, 2, hdiff=2)]
    ctx = ctx_inseason(fx)
    r = score_player(player(position=2, minutes=900), ctx)  # DEF (cs_value 4)
    f = r.components["fixtures"][0]
    cfg = DEFAULT_CONFIG
    base, attack = r.components["base"], r.components["attack"]
    core = (base * cfg.W_BASE + attack * cfg.W_ATTACK) * f["fdr_mult"] * f["venue_mult"]
    # fixture_ep = core + cs_pts, so subtracting cs_pts must recover core (not core×mult).
    assert f["fixture_ep"] == pytest.approx(core + f["cs_pts"], abs=1e-2)


def test_base_and_attack_blend_is_5050_and_both_present():
    """The double-count correction is the 50/50 blend — neither side zeroed (§2.3)."""
    assert DEFAULT_CONFIG.W_BASE == 0.5 and DEFAULT_CONFIG.W_ATTACK == 0.5


# --- W_CS: expected-CS weight completes the §2.3 correction for CS (v1.3) ---

def _fx0_at_wcs(pos, w_cs, hdiff=2):
    """Score a `pos` player over one fixture at the given W_CS; return fixtures[0]."""
    fx = [fixture(1, 11, 1, 2, hdiff=hdiff)]
    cfg = replace(DEFAULT_CONFIG, W_CS=w_cs)
    ctx = build_context([player()], TEAMS, fx, gameweeks(),
                        mode="IN_SEASON", anchor=11, config=cfg)
    return score_player(player(position=pos, minutes=900), ctx).components["fixtures"][0]


def _core(f):
    return f["fixture_ep"] - f["cs_pts"]  # fixture_ep = core + cs_pts_blob


def test_wcs_halves_def_cs_term_core_unchanged():
    """(a) A DEF's cs contribution halves at W_CS=0.5 vs 1.0; core is untouched."""
    half, full = _fx0_at_wcs(2, 0.5), _fx0_at_wcs(2, 1.0)  # DEF has no save-base
    assert half["cs_pts"] == pytest.approx(0.5 * full["cs_pts"], abs=1e-2)
    assert _core(half) == pytest.approx(_core(full), abs=1e-2)
    assert half["cs_pts"] > 0  # guard: there IS a cs term to halve


def test_wcs_does_not_scale_gk_save_base():
    """(b) The GK save-base is NOT scaled by W_CS: at W_CS=0 only save-base remains."""
    zero = _fx0_at_wcs(1, 0.0)
    assert zero["cs_pts"] == pytest.approx(DEFAULT_CONFIG.SAVE_PTS_BASE, abs=1e-2)
    # at W_CS=0.5 the fixture cs_pts = 0.5·cs_prob·CS_VALUE[GK] + full save-base
    half = _fx0_at_wcs(1, 0.5)
    expected = 0.5 * half["cs_prob"] * DEFAULT_CONFIG.CS_VALUE[1] + DEFAULT_CONFIG.SAVE_PTS_BASE
    assert half["cs_pts"] == pytest.approx(expected, abs=1e-2)


def test_wcs_leaves_fwd_untouched_and_mid_core_intact():
    """(c) FWD (CS_VALUE=0) is identical across W_CS; MID changes only via its cs term."""
    fwd_half, fwd_full = _fx0_at_wcs(4, 0.5), _fx0_at_wcs(4, 1.0)
    assert fwd_half["cs_pts"] == 0.0 and fwd_full["cs_pts"] == 0.0
    assert fwd_half["fixture_ep"] == pytest.approx(fwd_full["fixture_ep"], abs=1e-9)
    mid_half, mid_full = _fx0_at_wcs(3, 0.5), _fx0_at_wcs(3, 1.0)
    assert mid_half["cs_pts"] == pytest.approx(0.5 * mid_full["cs_pts"], abs=1e-2)
    assert _core(mid_half) == pytest.approx(_core(mid_full), abs=1e-2)
