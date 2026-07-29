"""Driver / smoke harness for fpl-analyst.

fpl-analyst is a CLI + library, not a GUI: there is nothing to screenshot.
This driver reaches into the two surfaces PRs actually touch:

  engine  (default) — Layer 2. Load the FROZEN 2025-26 season snapshot
                      (tests/assets/*_2025-26.json), parse it, run
                      engine.score_all(), and print summary stats + one
                      player's full labeled `components` blob (the real
                      product of Layer 2). Deterministic, offline.

  sync              — Layer 1+2 end to end. Run fpl.sync.sync_fpl() with a
                      FakeClient serving the frozen snapshot into a throwaway
                      SQLite DB, then read `ep_scores` straight back out to
                      prove the whole pipeline wrote rows. Deterministic, offline.

  live              — The real CLI path against the LIVE FPL API: init-db +
                      sync into a temp DB, then `mode`. Needs network and is
                      nondeterministic (depends on the real season state).

Run from the repo root with the project's interpreter, e.g.:
    .venv/Scripts/python .claude/skills/run-fpl-analyst/driver.py engine
    .venv/Scripts/python .claude/skills/run-fpl-analyst/driver.py sync
    .venv/Scripts/python .claude/skills/run-fpl-analyst/driver.py live
"""
from __future__ import annotations

import copy
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

# Run from repo root; make sure fpl/ and engine/ import regardless of CWD.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

ASSETS = ROOT / "tests" / "assets"
BOOTSTRAP = ASSETS / "bootstrap_2025-26.json"
FIXTURES = ASSETS / "fixtures_2025-26.json"


def _load_snapshot():
    bootstrap = json.loads(BOOTSTRAP.read_text(encoding="utf-8"))
    fixtures_raw = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return bootstrap, fixtures_raw


def _parse(bootstrap, fixtures_raw):
    from fpl.parsers import parse_fixture, parse_gameweek, parse_player, parse_team

    teams = [parse_team(t) for t in bootstrap["teams"]]
    gameweeks = [parse_gameweek(e) for e in bootstrap["events"]]
    players = [parse_player(el) for el in bootstrap["elements"]]
    fixtures = [parse_fixture(f) for f in fixtures_raw]
    # parse_player leaves Decimals; the engine calls float() on them, but be
    # explicit here so a stray non-numeric would surface loudly.
    return players, teams, fixtures, gameweeks


def cmd_engine() -> int:
    """Direct Layer-2 invocation on the frozen snapshot."""
    from engine.scoring import score_all

    bootstrap, fixtures_raw = _load_snapshot()
    players, teams, fixtures, gameweeks = _parse(bootstrap, fixtures_raw)

    # Full-season snapshot => IN_SEASON, anchor is the last unfinished GW (=> 38).
    results = score_all(players, teams, fixtures, gameweeks, mode="IN_SEASON")
    assert len(results) == len(players), "score_all must return one result per player"

    eps = [r.ep for r in results]
    print(f"scored {len(results)} players  mode=IN_SEASON")
    print(f"anchor_gw={results[0].gameweek}  "
          f"ep: min={min(eps):.2f} max={max(eps):.2f} "
          f"mean={statistics.mean(eps):.2f} median={statistics.median(eps):.2f}")

    top = max(results, key=lambda r: r.ep_next4)
    name = next(p["web_name"] for p in players if p["id"] == top.player_id)
    print(f"\ntop ep_next4: {name} (id={top.player_id}) "
          f"ep={top.ep:.2f} ep_next4={top.ep_next4:.2f}")
    print("components:")
    print(json.dumps(top.components, indent=2, default=str))
    return 0


def cmd_sync() -> int:
    """Full sync_fpl() pipeline into a throwaway SQLite DB, then read ep_scores back."""
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import sessionmaker

    from fpl.models import Base, EpScore, Player
    from fpl.sync import sync_fpl

    bootstrap, fixtures_raw = _load_snapshot()

    class FakeClient:
        def get_bootstrap(self):
            return copy.deepcopy(bootstrap)

        def get_fixtures(self):
            return copy.deepcopy(fixtures_raw)

        def close(self):
            pass

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    result = sync_fpl(client=FakeClient(), session_factory=Session)
    print(f"sync_fpl -> mode={result.mode} players={result.players_updated} "
          f"ep_rows={result.ep_rows} fixtures={result.fixtures} "
          f"rollover={result.rollover} {result.duration_s}s")

    with Session() as s:
        rows = s.scalar(select(func.count()).select_from(EpScore))
        assert rows == result.ep_rows, f"ep_scores rows {rows} != {result.ep_rows}"
        top = s.scalars(select(EpScore).order_by(EpScore.ep_next4.desc())).first()
        name = s.get(Player, top.player_id).web_name
        print(f"ep_scores has {rows} rows; top ep_next4: {name} "
              f"ep={top.ep} ep_next4={top.ep_next4}")
    engine.dispose()
    return 0


def cmd_live() -> int:
    """The real CLI path against the LIVE FPL API. Needs network."""
    import subprocess

    tmp = Path(tempfile.mkdtemp(prefix="fpl-live-")) / "live.db"
    env = dict(os.environ, FPL_DATABASE_URL=f"sqlite:///{tmp.as_posix()}")
    py = sys.executable
    for args in (["init-db"], ["sync"], ["mode"]):
        print(f"$ python -m fpl {' '.join(args)}")
        rc = subprocess.run([py, "-m", "fpl", *args], cwd=ROOT, env=env).returncode
        if rc != 0:
            print(f"  -> exited {rc}", file=sys.stderr)
            return rc
    return 0


CMDS = {"engine": cmd_engine, "sync": cmd_sync, "live": cmd_live}


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "engine"
    if cmd not in CMDS:
        print(f"usage: driver.py [{'|'.join(CMDS)}]", file=sys.stderr)
        return 2
    return CMDS[cmd]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
