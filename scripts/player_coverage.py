"""Where do players disappear between FPL's API and the draft picker?

    .venv/Scripts/python scripts/player_coverage.py
    .venv/Scripts/python scripts/player_coverage.py --offline
    .venv/Scripts/python scripts/player_coverage.py --look-for "Ait-Nouri,Salah,Gyokeres"

Answers one question: a player you expect to pick is not in the picker — which
of the four boundaries lost him, and why?

    A  bootstrap-static `elements`  ->  `players` table       (sync)
    B  `players` table             ->  /api/players payload   (service.players_list)
    C  /api/players payload        ->  what the picker RENDERS (DraftPicker.tsx)
    D  what you typed              ->  what the search MATCHES (DraftPicker.tsx)

A and B are ours and are checked against the live code. C and D are a
REIMPLEMENTATION of the TypeScript in `web/components/DraftPicker.tsx` — this
script cannot import that file, so if you change the picker's cap or its search
predicate, change `_picker_visible` / `_picker_search` here in the same commit
or this diagnostic starts lying. Pinned by
`tests/test_api.py::test_coverage_diagnostic_still_mirrors_the_picker`.

A name FPL has not published yet (a transfer-window signing they have not added
to bootstrap-static) is absent at boundary A with nothing upstream of it. That
is not our bug and the script says so in as many words — it is the one finding
here that no code change can fix.

Exit code is 0 whatever it finds: this is a diagnostic, not a gate. Only a crash
(no database, unreachable API) is non-zero.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# This script's whole subject is accented names, and a Windows console defaults
# to cp1252, which renders every one of them as `?` — turning the report into
# gibberish exactly where it matters. Force UTF-8 on our own stream.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ASSETS = ROOT / "tests" / "assets"

# --- mirrors of web/components/DraftPicker.tsx ------------------------------
# Keep in step with the component. See the module docstring.
ROWS_PER_POS = 0          # DraftPicker.tsx row cap; 0 = no cap (removed 2026-08-06)
POS_ORDER = ["GK", "DEF", "MID", "FWD"]

# fpl.models.Player.status, as FPL publishes it.
STATUS_WORD = {
    "a": "available",
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unavailable",
    "n": "on loan / ineligible",
}


# Latin letters that are NOT a base plus a combining mark, so NFD leaves them
# intact and stripping marks does nothing for them. Mirror of LETTERS in
# web/components/DraftPicker.tsx.
LETTERS = {
    "Ø": "o", "ø": "o", "ß": "ss", "Ł": "l", "ł": "l", "Đ": "d", "đ": "d",
    "Ð": "d", "ð": "d", "ı": "i", "Æ": "ae", "æ": "ae", "Œ": "oe", "œ": "oe",
    "Þ": "th", "þ": "th",
}


def fold(s: str) -> str:
    """Lowercase and strip diacritics: the picker's search key.

    MUST match `fold()` in web/components/DraftPicker.tsx. NFD splits a
    precomposed letter into base + combining mark, and `unicodedata.combining`
    is true for exactly that mark — so Aït-Nouri folds to ait-nouri and Gyökeres
    to gyokeres. LETTERS handles the letters NFD cannot touch (Ødegaard, Groß).
    """
    s = "".join(LETTERS.get(c, c) for c in s)
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if not unicodedata.combining(c)).lower()


def _picker_search(q: str, row: dict) -> bool:
    """The picker's match predicate, mirrored."""
    if not q:
        return True
    needle = fold(q.strip())
    return any(needle in fold(str(row.get(k) or "")) for k in
               ("web_name", "full_name", "team_short"))


def _picker_visible(payload: list[dict]) -> list[dict]:
    """Rows the picker renders with an EMPTY search box, mirrored."""
    if not ROWS_PER_POS:
        return list(payload)
    out = []
    for pos in POS_ORDER:
        in_pos = sorted((r for r in payload if r["position"] == pos),
                        key=lambda r: (-r["price"], r["web_name"]))
        out.extend(in_pos[:ROWS_PER_POS])
    return out


# --- the three data layers --------------------------------------------------

def load_bootstrap(offline: bool) -> list[dict]:
    """FPL's `elements`, live or from the frozen snapshot."""
    if offline:
        path = ASSETS / "bootstrap_2025-26.json"
        if not path.exists():
            raise SystemExit(f"no frozen snapshot at {path}")
        return json.loads(path.read_text(encoding="utf-8"))["elements"]
    from fpl.client import FPLClient
    client = FPLClient()
    try:
        return client.get_bootstrap()["elements"]
    finally:
        client.close()


def load_db_players(db_url: str) -> list[dict]:
    """The `players` table, as sync left it."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from fpl.models import Player

    engine = create_engine(db_url, future=True)
    try:
        with sessionmaker(bind=engine, future=True)() as session:
            return [
                {"id": p.id, "web_name": p.web_name, "full_name": p.full_name,
                 "status": p.status, "position": p.position, "team_id": p.team_id}
                for p in session.scalars(select(Player)).all()
            ]
    finally:
        engine.dispose()


def load_api_payload(db_url: str) -> list[dict]:
    """What GET /api/players returns, through the real handler.

    In-process rather than over HTTP: the route (api/routes.py) is three lines
    that set a cache header and call this, so a running uvicorn would add a port
    to the setup and prove nothing extra.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from graph.deps import Deps
    from api import service

    engine = create_engine(db_url, future=True)
    try:
        sf = sessionmaker(bind=engine, future=True)
        return service.players_list(Deps(session_factory=sf, client=None))
    finally:
        engine.dispose()


# --- reporting --------------------------------------------------------------

def banner(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 68 - len(title)))


def show(label: str, rows: list, limit: int = 25) -> None:
    print(f"\n  {label}: {len(rows)}")
    for r in rows[:limit]:
        print(f"    - {r}")
    if len(rows) > limit:
        print(f"    ... and {len(rows) - limit} more")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true",
                    help="use tests/assets/bootstrap_2025-26.json, not the live API")
    ap.add_argument("--db", default=None, help="database URL (default: FPL_DATABASE_URL)")
    ap.add_argument("--look-for", default="",
                    help="comma-separated names to trace through every boundary")
    args = ap.parse_args(argv)

    from fpl.config import settings as fpl_settings
    db_url = args.db or fpl_settings.database_url
    t0 = time.time()

    print(__doc__.splitlines()[0])
    print(f"database: {db_url}")
    print(f"source:   {'frozen snapshot' if args.offline else 'LIVE bootstrap-static'}")

    elements = load_bootstrap(args.offline)
    db_rows = load_db_players(db_url)
    payload = load_api_payload(db_url)

    by_el = {int(e["id"]): e for e in elements}
    by_db = {r["id"]: r for r in db_rows}
    by_api = {r["id"]: r for r in payload}

    def el_name(e: dict) -> str:
        full = f"{e.get('first_name', '')} {e.get('second_name', '')}".strip()
        return f"{e['web_name']} ({full})" if full and full != e["web_name"] else e["web_name"]

    # --- boundary A ---------------------------------------------------------
    banner("A  bootstrap-static elements -> players table")
    print(f"  bootstrap elements: {len(elements)}")
    print(f"  players rows:       {len(db_rows)}")
    dropped_a = [el_name(by_el[i]) for i in sorted(set(by_el) - set(by_db))]
    extra_a = [by_db[i]["web_name"] for i in sorted(set(by_db) - set(by_el))]
    if dropped_a:
        show("DROPPED BY SYNC (in FPL's API, missing from our table)", dropped_a)
        print("    ^ ours. sync_fpl truncates and bulk-inserts every element, so a")
        print("      difference here means the DB is STALE, not that rows were filtered.")
        print("      Re-run: .venv/Scripts/python -m fpl sync")
    else:
        print("  dropped by sync: 0  (no parse filter, no status filter, no per-row skip)")
    if extra_a:
        show("in our table, no longer in FPL's API (departed/removed players)", extra_a)

    # --- boundary B ---------------------------------------------------------
    banner("B  players table -> /api/players payload")
    print(f"  payload rows: {len(payload)}")
    dropped_b = [by_db[i]["web_name"] for i in sorted(set(by_db) - set(by_api))]
    if dropped_b:
        show("DROPPED BY THE PAYLOAD", dropped_b)
        print("    ^ ours. service.players_list has no WHERE and no LIMIT — a")
        print("      difference here is a regression.")
    else:
        print("  dropped by the payload: 0  (no WHERE, no LIMIT, every status shipped)")
    by_status: dict[str, int] = {}
    for r in payload:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
    print("  shipped by status: " + ", ".join(
        f"{STATUS_WORD.get(k, k)} {v}" for k, v in sorted(by_status.items())))
    missing_fields = [k for k in ("full_name", "status")
                      if payload and k not in payload[0]]
    if missing_fields:
        print(f"  !! payload is missing {missing_fields} — the picker cannot search "
              f"full names or flag unavailable players without them.")

    # --- boundary C ---------------------------------------------------------
    banner("C  payload -> what the picker renders (empty search box)")
    visible = _picker_visible(payload)
    unreachable = [r["web_name"] for r in payload
                   if r["id"] not in {v["id"] for v in visible}]
    print(f"  rendered with no search: {len(visible)} of {len(payload)}")
    if unreachable:
        show("UNREACHABLE WITHOUT TYPING (per-position row cap)", unreachable)
        print(f"    ^ ours. ROWS_PER_POS={ROWS_PER_POS} in DraftPicker.tsx, sorted")
        print("      price-desc, so the cut lands on the cheap end: every budget")
        print("      enabler and bench filler is invisible until searched for.")
    else:
        print("  unreachable without typing: 0  (no row cap)")

    # --- boundary D ---------------------------------------------------------
    banner("D  what you type -> what the search matches")
    non_ascii = [r for r in payload if not str(r["web_name"]).isascii()]
    print(f"  players whose web_name is not plain ASCII: {len(non_ascii)}")

    # Type each accented name the way someone on a UK keyboard would — its
    # folded spelling — and check the search actually returns him.
    unfindable = [f"{r['web_name']} — typing \"{fold(r['web_name'])}\" finds nothing"
                  for r in non_ascii if not _picker_search(fold(r["web_name"]), r)]
    if unfindable:
        show("UNFINDABLE WITHOUT THE EXACT GLYPH", unfindable)
        print("    ^ ours. The search does not fold diacritics.")
    else:
        print(f"  unfindable without the exact glyph: 0  "
              f"(all {len(non_ascii)} accented names match their plain spelling)")

    # And the other half of the search: a real first name that web_name hides.
    if payload and not payload[0].get("full_name"):
        print("  !! full_name absent from the payload: searching a player's REAL first")
        print("     name (\"mohamed\" for M.Salah) matches nothing.")
    else:
        hidden = [r for r in payload
                  if r.get("full_name")
                  and set(fold(r["full_name"]).split()) - set(fold(r["web_name"]).split())]
        findable = [r for r in hidden if _picker_search(
            sorted(set(fold(r["full_name"]).split()) - set(fold(r["web_name"]).split()))[0], r)]
        print(f"  players whose full name carries a word web_name does not: {len(hidden)}"
              f" — searchable by it: {len(findable)}")

    # --- the names you asked about ------------------------------------------
    wanted = [n.strip() for n in args.look_for.split(",") if n.strip()]
    if wanted:
        banner("Your names, traced through every boundary")
        upstream_absent = []
        for name in wanted:
            needle = fold(name)
            in_el = [e for e in elements
                     if needle in fold(f"{e.get('web_name','')} "
                                       f"{e.get('first_name','')} {e.get('second_name','')}")]
            in_db = [r for r in db_rows
                     if needle in fold(f"{r['web_name']} {r.get('full_name') or ''}")]
            in_api = [r for r in payload if _picker_search(name, r)]
            in_view = [r for r in _picker_visible(payload) if _picker_search(name, r)]
            print(f"\n  \"{name}\"")
            if not in_el:
                print("    FPL bootstrap-static : ABSENT  <-- upstream, not ours")
                upstream_absent.append(name)
            else:
                print(f"    FPL bootstrap-static : {', '.join(el_name(e) for e in in_el[:4])}")
            print(f"    players table        : {len(in_db)} match"
                  f"{'' if len(in_db) == 1 else 'es'}"
                  + (f"  ({', '.join(r['web_name'] + ' [' + STATUS_WORD.get(r['status'], r['status']) + ']' for r in in_db[:4])})" if in_db else ""))
            print(f"    /api/players payload : {len(in_api)} match"
                  f"{'' if len(in_api) == 1 else 'es'}")
            print(f"    findable in picker   : {'YES' if in_view else 'NO'}")
        if upstream_absent:
            banner("Not ours: FPL has not published these yet")
            for n in upstream_absent:
                print(f"  - {n}")
            print("\n  These are absent from bootstrap-static itself. FPL adds")
            print("  transfer-window signings on their own schedule, sometimes days")
            print("  after the move is announced. Nothing in this repo can conjure a")
            print("  player FPL has not published. Re-run after their next update.")

    print(f"\ndone in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
