#!/usr/bin/env python
"""The local pre-deploy gate: one command, one pass/fail line per check.

    .venv/Scripts/python scripts/smoke.py              # live FPL API for the sync
    .venv/Scripts/python scripts/smoke.py --offline    # frozen snapshot instead
    .venv/Scripts/python scripts/smoke.py --keep       # keep the temp dir + logs

What this is FOR. `pytest` proves the units and the seams; it never starts a
server, never opens a socket, and never runs a migration. This starts the real
uvicorn process against a real migrated SQLite file and drives the real HTTP
surface with a real cookie jar — the layer between "the tests pass" and "the
deploy works", which is exactly where GO-LIVE's first hour is spent.

WHAT IT COSTS: nothing. No browser, no Paddle, no Google, no Resend, no LLM. The
only network call is the FPL public API for the sync, and `--offline` removes
even that. Every provider-shaped thing is either off or seeded straight into the
database, which is the honest way to exercise our side of a boundary we have
never crossed.

## The two things worth knowing before you edit this

1. **A magic link cannot be read back out of the database.** `magic_link_tokens`
   stores a SHA-256, by design — the plaintext exists once, inside the email.
   With `RESEND_API_KEY` unset there is no email, so `mail.send_magic_link` logs
   the link instead and this script reads it out of the server's log file, then
   proves it is the real one by hashing it and comparing against the stored row.
   That log line is load-bearing for local development, not debug residue.

2. **`PAYWALL_ENABLED` is read at request time but the app validates it at
   boot**, so the paywall-on checks need their own server process. There are
   three launches here on purpose: paywall off, paywall on, and one that MUST
   fail to boot (paywall on with no `BRIEF_TOKEN_SECRET` — the fail-closed rule
   from `api/main.py`, which is only observable as a dead process).

Exit code is 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ASSETS = ROOT / "tests" / "assets"
SEASON = "2025-26"

# Everything below is scoped to this run and thrown away with the temp DB.
ADMIN_TOKEN = "smoke-" + secrets.token_urlsafe(24)
BRIEF_SECRET = secrets.token_urlsafe(32)
TERMS_VERSION = "smoke-terms-v1"      # pinned so the script and server agree
EMAIL = "smoke@example.com"
PAID_EMAIL = "smoke-paid@example.com"
TEAM_ID = 7_654_321                   # no real FPL entry; the row is seeded
GW = 1

FREE_KEYS = ["verdict", "transfer", "captaincy"]
PAID_KEYS = ["bench_plan", "chips", "watchlist"]

# Hand-written on purpose (same rule as tests/test_sections.py): deriving these
# headers from SECTION_TITLES would make the gating assertions vacuous.
SEEDED_BRIEF_MD = """# Scout report — GW1

## The verdict
Seeded by scripts/smoke.py.

## This week's transfer
Seeded by scripts/smoke.py.

## Captaincy
Seeded by scripts/smoke.py.

## Bench order & the plan
Seeded by scripts/smoke.py.

## Chips
Seeded by scripts/smoke.py.

## Watchlist
Seeded by scripts/smoke.py.
"""

DB_URL = ""          # set in main()
FAILURES: list[str] = []
S: dict = {}         # state shared between checks (ids, tokens, clients)


# --- the runner -------------------------------------------------------------

def check(name: str):
    """Run one check immediately; print one line. Used as a decorator.

    A check is a function that raises on failure, so `assert` is the whole
    vocabulary and a check that forgets to assert reads as obviously empty.
    """
    def run(fn):
        try:
            detail = fn()
        except Exception as exc:
            FAILURES.append(name)
            text = f"{type(exc).__name__}: {exc}"
            print(f"FAIL  {name}\n        {text.strip()[:400]}")
        else:
            print(f"PASS  {name}" + (f"  -- {detail}" if detail else ""))
        return fn
    return run


def skip(name: str, why: str) -> None:
    """A check that could not run counts as a FAILURE, loudly.

    A gate that goes quiet when a prerequisite dies is a gate that passes when
    the product is broken.
    """
    FAILURES.append(name)
    print(f"FAIL  {name}\n        skipped: {why}")


def phase(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


# --- process plumbing -------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def api_env(**over: str) -> dict:
    """The env every server launch starts from: everything paid switched OFF.

    Explicit rather than inherited — a stray `PAYWALL_ENABLED=true` in the
    developer's shell must not decide what this gate measures.
    """
    env = dict(
        os.environ,
        FPL_DATABASE_URL=DB_URL,
        ADMIN_TOKEN=ADMIN_TOKEN,
        TERMS_VERSION=TERMS_VERSION,
        RESEND_API_KEY="",            # => the magic link is logged, not mailed
        PAYWALL_ENABLED="false",
        SEND_ENABLED="false",
        RUN_SCHEDULER="false",
        DOCS_ENABLED="false",
        GOOGLE_CLIENT_ID="",
        GOOGLE_CLIENT_SECRET="",
        PADDLE_WEBHOOK_SECRET="",
        PADDLE_API_KEY="",
        BRIEF_TOKEN_SECRET="",
        # Plain http locally, so the session cookie must not be Secure — the one
        # setting that turns "signed in" into "silently signed out" over http.
        SESSION_COOKIE_SECURE="false",
        SESSION_COOKIE_DOMAIN="",
    )
    env.update(over)
    return env


class Server:
    """One uvicorn process, its log file, and a health-gated start."""

    def __init__(self, tmp: Path, name: str, **env_over: str):
        self.name = name
        self.port = free_port()
        self.log = tmp / f"{name}.log"
        self.env = api_env(**env_over)
        self.env["PUBLIC_API_URL"] = self.url
        # Never contacted: the auth routes 303 here and the script reads the
        # Location header instead of following it.
        self.env["PUBLIC_WEB_URL"] = "http://127.0.0.1:3000"
        self.env["FRONTEND_ORIGIN"] = "http://127.0.0.1:3000"
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._fh = self.log.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api.main:app",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=ROOT, env=self.env, stdout=self._fh, stderr=subprocess.STDOUT)

    def wait_ready(self, timeout: float = 60.0) -> bool:
        import httpx

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                if httpx.get(f"{self.url}/api/health", timeout=3).status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.4)
        return False

    def wait_exit(self, timeout: float = 60.0) -> int | None:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "_fh", None):
            self._fh.close()

    def text(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")

    def tail(self, n: int = 25) -> str:
        return "\n".join(self.text().splitlines()[-n:])


def cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "fpl", *args], cwd=ROOT,
                          env=env or api_env(), capture_output=True, text=True)


# --- database helpers -------------------------------------------------------

def session_factory():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(DB_URL, future=True)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def pick_squad(Session) -> tuple[list[int], int]:
    """A legal 15 from the synced `players`: 2/5/5/3, max 3 per club, under budget.

    Cheapest-first, so the budget rule is satisfied by construction rather than
    by a search — this is a fixture, not a team.
    """
    from sqlalchemy import select

    from fpl.models import Player

    with Session() as s:
        rows = s.scalars(
            select(Player).where(Player.status == "a").order_by(Player.price)).all()
    quota = {1: 2, 2: 5, 3: 5, 4: 3}
    picked: list[Player] = []
    clubs: dict[int, int] = {}
    for p in rows:
        if quota.get(p.position, 0) <= 0 or clubs.get(p.team_id, 0) >= 3:
            continue
        quota[p.position] -= 1
        clubs[p.team_id] = clubs.get(p.team_id, 0) + 1
        picked.append(p)
        if len(picked) == 15:
            break
    if len(picked) != 15:
        raise RuntimeError(f"could not assemble a legal squad from {len(rows)} players")
    ids = [p.id for p in picked]
    captain = max(picked, key=lambda p: p.total_points).id
    return ids, captain


def seed_paid_fixtures(Session) -> None:
    """A persisted team brief, an account that accepted the terms, and an ACTIVE
    subscription for that team — the state a Paddle webhook would have written.

    Seeded directly because this repo has never spoken to Paddle. It is the
    boundary's *effect* on our tables, not a claim that the webhook works.
    """
    from fpl.models import Account, Brief, Subscription

    now = datetime.now(timezone.utc)
    with Session() as s, s.begin():
        s.add(Brief(fpl_team_id=TEAM_ID, gameweek=GW, brief_md=SEEDED_BRIEF_MD,
                    input_hash="smoke", model="smoke"))
        account = Account(email=PAID_EMAIL, auth_provider="magic_link",
                          terms_accepted_at=now, terms_version=TERMS_VERSION,
                          created_at=now)
        s.add(account)
        s.flush()
        s.add(Subscription(
            email=PAID_EMAIL, account_id=account.id, fpl_team_id=TEAM_ID,
            provider="paddle", provider_subscription_id="sub_smoke",
            plan="monthly", status="active",
            current_period_end=now + timedelta(days=30), created_at=now))
        S["paid_account_id"] = account.id


def mint_session(Session, account_id: int) -> str:
    """A session row for `account_id`; returns the plaintext cookie value.

    Hashed with the app's own `accounts.hash_token`, so a change to how sessions
    are stored breaks this loudly instead of silently signing nobody in.
    """
    from api.accounts import hash_token
    from fpl.models import AuthSession

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with Session() as s, s.begin():
        s.add(AuthSession(token_hash=hash_token(token), account_id=account_id,
                          expires_at=now + timedelta(days=1), created_at=now))
    return token


def set_subscription_status(Session, status: str) -> None:
    from sqlalchemy import select

    from fpl.models import Subscription

    with Session() as s, s.begin():
        row = s.scalars(select(Subscription)).first()
        row.status = status


def set_terms_version(Session, account_id: int, version: str | None) -> None:
    from fpl.models import Account

    with Session() as s, s.begin():
        s.get(Account, account_id).terms_version = version


# --- phase 1: a database from nothing ---------------------------------------

def phase_data(offline: bool) -> bool:
    """init-db -> backfill-archive -> sync. Returns False if the DB is unusable.

    Backfill runs BEFORE sync, which is the order a fresh database actually
    needs: pre-season scoring resolves through `players_archive`, and an empty
    one collapses every EP onto the position/price baseline — the documented
    "top 20 is all goalkeepers" trap. Doing it after would need a second sync.
    """
    from sqlalchemy import func, select

    phase("1. Fresh database: migrations, archive, sync")

    r = cli("init-db")
    if r.returncode != 0:
        skip("migrations apply to an empty database", r.stderr.strip()[:300])
        return False
    print("PASS  migrations apply to an empty database")

    @check("backfill-archive populates players_archive")
    def _():
        out = cli("backfill-archive", "--season", SEASON,
                  "--from", str(ASSETS / f"bootstrap_{SEASON}.json"))
        assert out.returncode == 0, out.stderr.strip()
        n = int(re.search(r"archived (\d+) players", out.stdout).group(1))
        assert n > 500, f"only {n} archived rows"
        return f"{n} rows"

    if offline:
        @check("sync (offline, frozen snapshot) writes players + ep_scores")
        def _():
            S["sync"] = offline_sync()
            assert S["sync"]["players"] > 0 and S["sync"]["ep_rows"] > 0, S["sync"]
            return f"mode={S['sync']['mode']} players={S['sync']['players']} " \
                   f"ep_rows={S['sync']['ep_rows']}"
    else:
        @check("sync (LIVE FPL API) writes players + ep_scores")
        def _():
            out = cli("sync")
            assert out.returncode == 0, out.stderr.strip()
            line = out.stdout.strip().splitlines()[-1]
            S["sync"] = {"line": line}
            assert "players=0" not in line and "ep_rows=0" not in line, line
            return line

    @check("top-20 EP is not all goalkeepers (the empty-archive trap)")
    def _():
        from fpl.models import EpScore, Player

        Session = session_factory()
        with Session() as s:
            rows = s.execute(
                select(Player.position).join(EpScore, EpScore.player_id == Player.id)
                .order_by(EpScore.ep_next4.desc()).limit(20)).scalars().all()
        assert rows, "ep_scores is empty"
        positions = sorted(set(rows))
        assert positions != [1], "every top-20 player is a goalkeeper — archive is empty"
        return f"positions {positions} in the top 20"

    Session = session_factory()
    with Session() as s:
        from fpl.models import Player

        if not s.scalar(select(func.count()).select_from(Player)):
            skip("database is usable by the API", "no players rows after sync")
            return False
    return True


def offline_sync() -> dict:
    """`sync_fpl` against the frozen 2025-26 snapshot instead of the live API."""
    import copy

    from fpl.sync import sync_fpl

    bootstrap = json.loads((ASSETS / f"bootstrap_{SEASON}.json").read_text("utf-8"))
    fixtures = json.loads((ASSETS / f"fixtures_{SEASON}.json").read_text("utf-8"))

    class FakeClient:
        def get_bootstrap(self):
            return copy.deepcopy(bootstrap)

        def get_fixtures(self):
            return copy.deepcopy(fixtures)

        def close(self):
            pass

    result = sync_fpl(client=FakeClient(), session_factory=session_factory())
    return {"mode": result.mode, "players": result.players_updated,
            "ep_rows": result.ep_rows, "fixtures": result.fixtures}


# --- phase 2: the open surface, paywall OFF ---------------------------------

def phase_open(srv: Server, Session) -> None:
    import httpx

    phase("2. Free funnel + admin auth (PAYWALL_ENABLED=false)")
    c = httpx.Client(base_url=srv.url, timeout=60, follow_redirects=False)
    S["client"] = c

    @check("GET /api/health is 200")
    def _():
        r = c.get("/api/health")
        assert r.status_code == 200, r.text
        return f"mode={r.json().get('mode')}"

    @check("GET /api/players is 200 and non-empty")
    def _():
        r = c.get("/api/players")
        assert r.status_code == 200, r.text
        assert len(r.json()) > 100, len(r.json())
        return f"{len(r.json())} players"

    @check("POST /api/brief (draft, anonymous) generates a brief")
    def _():
        ids, captain = pick_squad(Session)
        r = c.post("/api/brief", json={"draft": {"player_ids": ids,
                                                 "captain_id": captain}})
        assert r.status_code == 200, r.text
        body = r.json()
        S["draft_id"] = body["draft_id"]
        missing = [k for k in FREE_KEYS if k not in body["sections"]]
        assert not missing, f"missing free sections {missing}"
        assert body["locked_sections"] == [], body["locked_sections"]
        return f"{len(body['sections'])} sections, nothing locked"

    @check("GET /api/brief/draft/{id} serves the cached brief")
    def _():
        r = c.get(f"/api/brief/draft/{S['draft_id']}")
        assert r.status_code == 200, r.text
        assert r.json()["cached"] is True, "second read was not a cache hit"
        return "cached=true"

    @check("GET /api/brief/{team}/{gw} anonymous is 200, never 401/redirect")
    def _():
        r = c.get(f"/api/brief/{TEAM_ID}/{GW}")
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        assert r.json()["locked_sections"] == [], "paywall is off; nothing may lock"
        return f"{len(r.json()['sections'])} sections"

    @check("POST /api/brief with 3 players is 400 squad_size")
    def _():
        r = c.post("/api/brief", json={"draft": {"player_ids": [1, 2, 3]}})
        assert r.status_code == 400, r.status_code
        assert r.json()["detail"]["error"] == "squad_size", r.text
        return "400 squad_size"

    @check("POST /api/brief with a duplicated player is 400 duplicate_players")
    def _():
        ids, _cap = pick_squad(Session)
        r = c.post("/api/brief", json={"draft": {"player_ids": ids[:14] + [ids[0]]}})
        assert r.status_code == 400, r.status_code
        assert r.json()["detail"]["error"] == "duplicate_players", r.text
        return "400 duplicate_players"

    @check("POST /api/brief with neither team_id nor draft is 422")
    def _():
        r = c.post("/api/brief", json={})
        assert r.status_code == 422, r.status_code
        return "422"

    @check("GET /api/brief/draft/{unknown} is 404 invalid_draft_id")
    def _():
        r = c.get(f"/api/brief/draft/{uuid.uuid4()}")
        assert r.status_code == 404, r.status_code
        assert r.json()["detail"]["error"] == "invalid_draft_id", r.text
        return "404 invalid_draft_id"

    @check("GET /api/brief/{unknown team}/{gw} is 404 invalid_team_id")
    def _():
        r = c.get(f"/api/brief/{TEAM_ID + 1}/{GW}")
        assert r.status_code == 404, r.status_code
        assert r.json()["detail"]["error"] == "invalid_team_id", r.text
        return "404 invalid_team_id"

    @check("GET /api/admin/overview without a token is 401")
    def _():
        r = c.get("/api/admin/overview")
        assert r.status_code == 401, r.status_code
        return "401"

    @check("GET /api/admin/overview with a wrong token is 401")
    def _():
        r = c.get("/api/admin/overview", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401, r.status_code
        return "401"

    @check("GET /api/admin/overview with ADMIN_TOKEN is 200")
    def _():
        r = c.get("/api/admin/overview",
                  headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        return "200"

    @check("GET /openapi.json is 404 while DOCS_ENABLED is false")
    def _():
        r = c.get("/openapi.json")
        assert r.status_code == 404, r.status_code
        return "404"


# --- phase 3: magic link, sessions, the failure paths -----------------------

def phase_auth(srv: Server, Session) -> None:
    from sqlalchemy import func, select

    from api.accounts import hash_token
    from fpl.models import Account, AuthSession, MagicLinkToken

    phase("3. Magic link, sessions, and the auth failure paths")
    c = S["client"]

    @check("GET /api/auth/me anonymous is 200 signed_in=false")
    def _():
        r = c.get("/api/auth/me")
        assert r.status_code == 200, r.status_code
        assert r.json()["signed_in"] is False, r.text
        return "200 signed_in=false"

    @check("GET /api/auth/google/start is 404 while Google is unconfigured")
    def _():
        r = c.get("/api/auth/google/start")
        assert r.status_code == 404, r.status_code
        return "404"

    @check("POST /api/auth/request-link with a malformed address is 422")
    def _():
        r = c.post("/api/auth/request-link", json={"email": "not-an-address"})
        assert r.status_code == 422, r.status_code
        return "422"

    @check("POST /api/auth/request-link writes an unconsumed token row")
    def _():
        r = c.post("/api/auth/request-link", json={"email": EMAIL, "next": "/account"})
        assert r.status_code == 200 and r.json() == {"ok": True}, r.text
        with Session() as s:
            rows = s.scalars(select(MagicLinkToken)
                             .where(MagicLinkToken.email == EMAIL)).all()
        assert len(rows) == 1, f"{len(rows)} token rows"
        assert rows[0].consumed_at is None, "issued already-consumed"
        return "1 row, consumed_at=NULL"

    @check("the link is LOGGED when RESEND_API_KEY is unset, and it is the real token")
    def _():
        pattern = re.compile(r"magic link for \S+: (\S+)")
        deadline = time.time() + 10
        while time.time() < deadline:
            found = pattern.search(srv.text())
            if found:
                break
            time.sleep(0.3)
        assert found, "no magic link in the server log — local sign-in is impossible"
        url = found.group(1)
        S["verify_url"] = url
        token = re.search(r"[?&]token=([^&\s]+)", url).group(1)
        S["magic_token"] = token
        with Session() as s:
            row = s.get(MagicLinkToken, hash_token(token))
        assert row is not None, "logged token does not hash to any stored row"
        assert row.email == EMAIL, row.email
        return f"{url.split('token=')[0]}token=..."

    @check("GET /api/auth/verify sets a session cookie and consumes the token")
    def _():
        r = c.get("/api/auth/verify",
                  params={"token": S["magic_token"], "next": "/account"})
        assert r.status_code == 303, f"{r.status_code} {r.text[:200]}"
        assert r.headers["location"].endswith("/account"), r.headers["location"]
        assert c.cookies.get("fpl_session"), "no session cookie was set"
        with Session() as s:
            row = s.get(MagicLinkToken, hash_token(S["magic_token"]))
            sessions = s.scalar(select(func.count()).select_from(AuthSession))
            account = s.scalars(select(Account).where(Account.email == EMAIL)).first()
        assert row.consumed_at is not None, "token was not consumed"
        assert sessions == 1, f"{sessions} session rows"
        assert account is not None, "no account was created at verify time"
        S["account_id"] = account.id
        return "303 + cookie, token consumed, account created"

    @check("GET /api/auth/me with the cookie reports the signed-in account")
    def _():
        body = c.get("/api/auth/me").json()
        assert body["signed_in"] is True, body
        assert body["email"] == EMAIL, body
        assert body["entitled"] is False, "a fresh account must not be entitled"
        assert body["terms_required"] is True, "unaccepted terms must be reported"
        return "signed_in, terms_required=true, entitled=false"

    @check("replaying the same magic link is refused and mints no second session")
    def _():
        r = c.get("/api/auth/verify", params={"token": S["magic_token"]})
        assert r.status_code == 303, r.status_code
        assert "error=link_invalid" in r.headers["location"], r.headers["location"]
        with Session() as s:
            n = s.scalar(select(func.count()).select_from(AuthSession))
        assert n == 1, f"replay created a session ({n} rows)"
        return "303 link_invalid, still 1 session"

    @check("a garbage magic link is refused identically")
    def _():
        r = c.get("/api/auth/verify", params={"token": "nonsense"})
        assert r.status_code == 303, r.status_code
        assert "error=link_invalid" in r.headers["location"], r.headers["location"]
        return "303 link_invalid"

    @check("POST /api/auth/accept-terms records the current version")
    def _():
        r = c.post("/api/auth/accept-terms")
        assert r.status_code == 200, r.text
        assert r.json()["terms_version"] == TERMS_VERSION, r.text
        assert c.get("/api/auth/me").json()["terms_required"] is False
        return TERMS_VERSION

    @check("the free brief still works while signed in (no login wall either way)")
    def _():
        r = c.get(f"/api/brief/{TEAM_ID}/{GW}")
        assert r.status_code == 200, r.status_code
        return "200"

    @check("POST /api/auth/logout deletes the row, not just the cookie")
    def _():
        r = c.post("/api/auth/logout")
        assert r.status_code == 200, r.text
        with Session() as s:
            n = s.scalar(select(func.count()).select_from(AuthSession))
        assert n == 0, f"{n} session rows survived logout"
        c.cookies.clear()
        assert c.get("/api/auth/me").json()["signed_in"] is False
        return "0 session rows"

    @check("POST /api/auth/accept-terms anonymous is 401")
    def _():
        r = c.post("/api/auth/accept-terms")
        assert r.status_code == 401, r.status_code
        return "401"

    @check("POST /api/account/delete anonymous is 401")
    def _():
        r = c.post("/api/account/delete")
        assert r.status_code == 401, r.status_code
        return "401"

    @check("POST /api/account/delete removes the account and its sessions")
    def _():
        c.cookies.set("fpl_session", mint_session(Session, S["account_id"]))
        r = c.post("/api/account/delete")
        assert r.status_code == 200, r.text
        with Session() as s:
            gone = s.get(Account, S["account_id"]) is None
            n = s.scalar(select(func.count()).select_from(AuthSession)
                         .where(AuthSession.account_id == S["account_id"]))
        assert gone, "the account row survived its own deletion"
        assert n == 0, f"{n} sessions survived"
        c.cookies.clear()
        return "account + sessions gone"


# --- phase 4: the paywall, on ------------------------------------------------

def phase_paywall(srv: Server, Session) -> None:
    import httpx

    phase("4. Paywall gating (PAYWALL_ENABLED=true)")
    c = httpx.Client(base_url=srv.url, timeout=60, follow_redirects=False)

    @check("anonymous team brief is still 200 - locked, not walled")
    def _():
        r = c.get(f"/api/brief/{TEAM_ID}/{GW}")
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        body = r.json()
        assert sorted(body["locked_sections"]) == sorted(PAID_KEYS), body["locked_sections"]
        assert sorted(body["sections"]) == sorted(FREE_KEYS), sorted(body["sections"])
        return f"locked {body['locked_sections']}"

    @check("the draft brief locks paid sections and serves the free ones")
    def _():
        ids, captain = pick_squad(Session)
        r = c.post("/api/brief", json={"draft": {"player_ids": ids,
                                                 "captain_id": captain}})
        assert r.status_code == 200, r.text
        body = r.json()
        leaked = [k for k in PAID_KEYS if k in body["sections"]]
        assert not leaked, f"paid sections served free: {leaked}"
        assert body["locked_sections"], "nothing locked with the paywall on"
        missing = [k for k in FREE_KEYS if k not in body["sections"]]
        assert not missing, f"free sections withheld: {missing}"
        return f"locked {body['locked_sections']}"

    @check("an entitled account with accepted terms reads the full brief")
    def _():
        c.cookies.set("fpl_session", mint_session(Session, S["paid_account_id"]))
        r = c.get(f"/api/brief/{TEAM_ID}/{GW}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["locked_sections"] == [], body["locked_sections"]
        assert sorted(body["sections"]) == sorted(FREE_KEYS + PAID_KEYS), \
            sorted(body["sections"])
        return "6 sections, nothing locked"

    @check("GET /api/auth/me reports entitled=true for that account")
    def _():
        body = c.get("/api/auth/me").json()
        assert body["signed_in"] and body["entitled"], body
        return "entitled=true"

    @check("an outstanding terms version degrades a payer to the free view, not a 403")
    def _():
        set_terms_version(Session, S["paid_account_id"], "an-older-version")
        r = c.get(f"/api/brief/{TEAM_ID}/{GW}")
        assert r.status_code == 200, f"{r.status_code} — must never 403"
        assert r.json()["locked_sections"], "terms gate did not withhold paid content"
        me = c.get("/api/auth/me").json()
        assert me["entitled"] is True and me["terms_required"] is True, me
        set_terms_version(Session, S["paid_account_id"], TERMS_VERSION)
        return "200, locked, entitled+terms_required both reported"

    @check("an unrecognised subscription status is NOT entitled (allow-list holds)")
    def _():
        set_subscription_status(Session, "some_new_paddle_status")
        r = c.get(f"/api/brief/{TEAM_ID}/{GW}")
        assert r.status_code == 200, r.status_code
        assert r.json()["locked_sections"], "an unknown status unlocked paid content"
        assert c.get("/api/auth/me").json()["entitled"] is False
        set_subscription_status(Session, "active")
        return "locked, entitled=false"

    @check("a past_due subscription is NOT entitled")
    def _():
        set_subscription_status(Session, "past_due")
        r = c.get(f"/api/brief/{TEAM_ID}/{GW}")
        assert r.json()["locked_sections"], "past_due unlocked paid content"
        set_subscription_status(Session, "active")
        return "locked"

    c.close()


# --- phase 5: the boot gate --------------------------------------------------

def phase_failclosed(tmp: Path) -> None:
    phase("5. Fail-closed startup")

    @check("PAYWALL_ENABLED=true with an empty BRIEF_TOKEN_SECRET refuses to boot")
    def _():
        srv = Server(tmp, "failclosed", PAYWALL_ENABLED="true", BRIEF_TOKEN_SECRET="")
        srv.start()
        try:
            assert not srv.wait_ready(timeout=25), \
                "the app served traffic with an unusable signing secret"
            rc = srv.wait_exit(timeout=20)
            assert rc not in (None, 0), f"exit code {rc}"
            assert "BRIEF_TOKEN_SECRET" in srv.text(), srv.tail(10)
        finally:
            srv.stop()
        return f"exited {rc}"


# --- main --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    global DB_URL

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true",
                    help="sync from the frozen snapshot instead of the live FPL API")
    ap.add_argument("--keep", action="store_true",
                    help="keep the temp database and server logs, and print the path")
    args = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="fpl-smoke-"))
    DB_URL = f"sqlite:///{(tmp / 'smoke.db').as_posix()}"
    started = time.time()
    print(f"scratch: {tmp}\ndatabase: {DB_URL}")

    servers: list[Server] = []
    try:
        if phase_data(args.offline):
            Session = session_factory()
            seed_paid_fixtures(Session)

            open_srv = Server(tmp, "paywall-off")
            servers.append(open_srv)
            open_srv.start()
            if open_srv.wait_ready():
                phase_open(open_srv, Session)
                phase_auth(open_srv, Session)
            else:
                skip("server boots with the paywall off", open_srv.tail())
            open_srv.stop()

            paid_srv = Server(tmp, "paywall-on", PAYWALL_ENABLED="true",
                              BRIEF_TOKEN_SECRET=BRIEF_SECRET)
            servers.append(paid_srv)
            paid_srv.start()
            if paid_srv.wait_ready():
                phase_paywall(paid_srv, Session)
            else:
                skip("server boots with the paywall on", paid_srv.tail())
            paid_srv.stop()

        phase_failclosed(tmp)
    finally:
        for srv in servers:
            srv.stop()

    took = time.time() - started
    print()
    if FAILURES:
        print(f"FAILED - {len(FAILURES)} check(s) in {took:.0f}s:")
        for name in FAILURES:
            print(f"  - {name}")
        print(f"logs: {tmp}")
        return 1

    print(f"ALL CHECKS PASSED in {took:.0f}s")
    if args.keep:
        print(f"kept: {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
