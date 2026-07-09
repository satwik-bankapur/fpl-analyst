"""Season rollover detection + archive routine (§6.2).

The archive MUST run before the new-season sync overwrites `players` — otherwise the
only data pre-season analysis has is silently destroyed.
"""
from sqlalchemy import func, select

from fpl.models import EpScore, Gameweek, Player, PlayerArchive
from fpl.sync import detect_rollover, get_meta, sync_fpl


def _make_finished_prior_season(session_factory, fake_client):
    """Seed the DB with a finished prior season (via a normal sync, then mark GWs done)."""
    sync_fpl(client=fake_client, session_factory=session_factory)
    with session_factory() as s, s.begin():
        for gw in s.scalars(select(Gameweek)).all():
            gw.finished = True
            gw.is_current = False
            gw.is_next = False


def test_detect_rollover_true_when_api_reset(session_factory, fake_client, bootstrap):
    _make_finished_prior_season(session_factory, fake_client)
    # New-season bootstrap: all events unfinished, all points 0.
    events = [dict(e, finished=False) for e in bootstrap["events"]]
    elements = [dict(e, total_points=0) for e in bootstrap["elements"]]
    with session_factory() as s:
        assert detect_rollover(s, events, elements) is True


def test_no_rollover_when_points_nonzero(session_factory, fake_client, bootstrap):
    _make_finished_prior_season(session_factory, fake_client)
    events = [dict(e, finished=False) for e in bootstrap["events"]]
    elements = bootstrap["elements"]  # points still non-zero
    with session_factory() as s:
        assert detect_rollover(s, events, elements) is False


def test_no_rollover_on_fresh_db(session_factory, bootstrap):
    with session_factory() as s:
        assert detect_rollover(s, bootstrap["events"], bootstrap["elements"]) is False


def test_sync_archives_before_overwrite(session_factory, fake_client, bootstrap):
    _make_finished_prior_season(session_factory, fake_client)

    # A new-season client: all events unfinished, points reset to 0, prices bumped.
    class RolloverClient:
        def get_bootstrap(self):
            b = {k: [dict(x) for x in v] for k, v in bootstrap.items()}
            for e in b["events"]:
                e["finished"] = False
            for el in b["elements"]:
                el["total_points"] = 0
                el["now_cost"] = el["now_cost"] + 5  # new-season prices
            return b

        def get_fixtures(self):
            return []

        def close(self):
            pass

    result = sync_fpl(client=RolloverClient(), session_factory=session_factory)
    assert result.rollover is True

    with session_factory() as s:
        # Old season captured in the archive before players were overwritten.
        archived = s.scalar(select(func.count()).select_from(PlayerArchive))
        assert archived == 3
        # season_current recorded in meta.
        assert get_meta(s, "season_current") is not None
        # ep_scores was truncated as part of the archive routine, then repopulated
        # by this sync's stub writer (fixtures empty -> rows exist but all zero EP).
        assert s.scalar(select(func.count()).select_from(EpScore)) == 3
