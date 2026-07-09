"""FPL client 404 semantics — the two 404s must never be conflated (§5.3)."""
import httpx
import pytest

from fpl.client import FPLClient
from fpl.errors import InvalidTeamId, NoPicksYet


def _client(handler) -> FPLClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://example.test/api/")
    return FPLClient(base_url="https://example.test/api/", client=http)


def test_entry_404_is_invalid_team_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _client(handler) as c:
        with pytest.raises(InvalidTeamId) as ei:
            c.get_entry(999)
        assert ei.value.code == "invalid_team_id"


def test_picks_404_on_valid_entry_is_no_picks_yet():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/picks/"):
            return httpx.Response(404)
        # entry/{id}/ resolves -> valid team
        return httpx.Response(200, json={"id": 123, "name": "Valid Team"})

    with _client(handler) as c:
        with pytest.raises(NoPicksYet) as ei:
            c.resolve_squad(123, 1)
        assert ei.value.code == "no_picks_yet"
        assert ei.value.gameweek == 1


def test_resolve_squad_bad_id_surfaces_invalid_team_id_not_no_picks():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)  # even entry/{id}/ 404s

    with _client(handler) as c:
        with pytest.raises(InvalidTeamId):
            c.resolve_squad(999, 1)


def test_bootstrap_and_fixtures_parse():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bootstrap-static/"):
            return httpx.Response(200, json={"elements": [], "teams": [], "events": []})
        if request.url.path.endswith("/fixtures/"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    with _client(handler) as c:
        assert c.get_bootstrap() == {"elements": [], "teams": [], "events": []}
        assert c.get_fixtures() == []
