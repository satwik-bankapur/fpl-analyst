"""FPL API client (spec §1).

Endpoints 1.1–1.4 only. 1.5 (transfers) and 1.6 (element-summary) are NOT used in v1.

No auth; send a browser-like User-Agent (default Python UA is occasionally 403'd).
No published rate limits: we stay polite — back off on 429/403, retry transient errors.

404 handling is endpoint-specific and must never be conflated (spec §5.3):
  - get_entry 404 -> InvalidTeamId   (bad ID; user error)
  - get_picks 404 -> NoPicksYet      (pre-season / GW not started -> draft flow)
"""
from __future__ import annotations

import logging
import time

import httpx

from .config import settings
from .errors import FPLApiError, InvalidTeamId, NoPicksYet

log = logging.getLogger("fpl.client")


class FPLClient:
    def __init__(self, base_url: str | None = None, client: httpx.Client | None = None):
        self.base_url = base_url or settings.api_base_url
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            timeout=settings.http_timeout_s,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FPLClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low-level GET with polite retry/back-off ---------------------------
    def _get(self, path: str, *, allow_404: bool = False) -> httpx.Response | None:
        """GET with retries. Returns the Response, or None on a 404 when allow_404."""
        last_exc: Exception | None = None
        for attempt in range(settings.http_max_retries):
            try:
                resp = self._client.get(path)
            except httpx.HTTPError as exc:  # network/timeout
                last_exc = exc
                self._sleep(attempt)
                continue

            if resp.status_code == 404:
                if allow_404:
                    return None
                raise FPLApiError(f"GET {path} -> 404 (unexpected)")
            if resp.status_code in (429, 403):
                # Rate-limited / blocked — back off and retry.
                log.warning("GET %s -> %s, backing off (attempt %s)", path, resp.status_code, attempt + 1)
                self._sleep(attempt)
                last_exc = FPLApiError(f"GET {path} -> {resp.status_code}")
                continue
            if resp.status_code >= 500:
                last_exc = FPLApiError(f"GET {path} -> {resp.status_code}")
                self._sleep(attempt)
                continue
            resp.raise_for_status()
            return resp

        raise FPLApiError(f"GET {path} failed after {settings.http_max_retries} attempts: {last_exc}")

    @staticmethod
    def _sleep(attempt: int) -> None:
        time.sleep(settings.http_backoff_base_s * (2 ** attempt))

    def _get_json(self, path: str) -> dict:
        resp = self._get(path)
        assert resp is not None  # allow_404 not set -> never None
        try:
            return resp.json()
        except ValueError as exc:
            raise FPLApiError(f"GET {path} returned non-JSON body") from exc

    # -- 1.1 bootstrap-static ----------------------------------------------
    def get_bootstrap(self) -> dict:
        """Primary sync target (~2MB): elements, teams, events."""
        return self._get_json("bootstrap-static/")

    # -- 1.2 fixtures ------------------------------------------------------
    def get_fixtures(self) -> list[dict]:
        """All 380 fixtures. Pull all; filter locally."""
        resp = self._get("fixtures/")
        assert resp is not None
        data = resp.json()
        if not isinstance(data, list):
            raise FPLApiError("fixtures/ did not return a list")
        return data

    # -- 1.3 entry (manager profile) — the team-ID validation call ---------
    def get_entry(self, team_id: int) -> dict:
        """404 here means the team ID does not exist -> InvalidTeamId."""
        resp = self._get(f"entry/{team_id}/", allow_404=True)
        if resp is None:
            raise InvalidTeamId(team_id)
        return resp.json()

    # -- 1.4 picks (squad for a gameweek) ----------------------------------
    def get_picks(self, team_id: int, gameweek: int) -> dict:
        """404 here (for a valid entry) means pre-season / GW not started -> NoPicksYet."""
        resp = self._get(f"entry/{team_id}/event/{gameweek}/picks/", allow_404=True)
        if resp is None:
            raise NoPicksYet(team_id, gameweek)
        return resp.json()

    def resolve_squad(self, team_id: int, gameweek: int) -> dict:
        """Fetch picks, disambiguating the two 404s: validate the entry FIRST.

        A picks 404 alone is ambiguous (bad ID vs. GW-not-started). We call get_entry
        first so a bad ID surfaces as InvalidTeamId; a picks 404 on a VALID entry then
        cleanly means NoPicksYet.
        """
        self.get_entry(team_id)  # raises InvalidTeamId on bad ID
        return self.get_picks(team_id, gameweek)  # raises NoPicksYet on valid-but-empty
