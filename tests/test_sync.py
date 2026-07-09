"""End-to-end sync + mode derivation + rollover, all against frozen assets."""
from decimal import Decimal

from sqlalchemy import func, select

from fpl.models import (
    EpScore,
    Fixture,
    Gameweek,
    Player,
    PlayerArchive,
    PlayerHistory,
    Team,
)
from fpl.sync import PRESEASON, IN_SEASON, derive_mode, next_gameweek, sync_fpl


def test_sync_populates_all_tables(session_factory, fake_client):
    result = sync_fpl(client=fake_client, session_factory=session_factory)
    assert result.mode == PRESEASON
    assert result.players_updated == 3
    assert result.fixtures == 5
    assert result.ep_rows == 3
    assert result.rollover is False
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Team)) == 3
        assert s.scalar(select(func.count()).select_from(Gameweek)) == 4
        assert s.scalar(select(func.count()).select_from(Player)) == 3
        assert s.scalar(select(func.count()).select_from(EpScore)) == 3
        assert s.scalar(select(func.count()).select_from(PlayerHistory)) == 3


def test_price_stored_divided_by_ten(session_factory, fake_client):
    sync_fpl(client=fake_client, session_factory=session_factory)
    with session_factory() as s:
        saka = s.get(Player, 1)
        assert saka.price == Decimal("7.5")
        assert saka.chance_of_playing is None  # NULL preserved


def test_mode_is_preseason_today(session_factory, fake_client):
    sync_fpl(client=fake_client, session_factory=session_factory)
    with session_factory() as s:
        assert derive_mode(s) == PRESEASON
        assert next_gameweek(s).id == 1  # GW1 is_next


def test_sync_is_idempotent(session_factory, fake_client):
    sync_fpl(client=fake_client, session_factory=session_factory)
    sync_fpl(client=fake_client, session_factory=session_factory)
    with session_factory() as s:
        # players overwritten (not duplicated), history de-duped for the day
        assert s.scalar(select(func.count()).select_from(Player)) == 3
        assert s.scalar(select(func.count()).select_from(PlayerHistory)) == 3
        assert s.scalar(select(func.count()).select_from(EpScore)) == 3


def test_null_event_fixture_preserved_in_db(session_factory, fake_client):
    sync_fpl(client=fake_client, session_factory=session_factory)
    with session_factory() as s:
        postponed = s.get(Fixture, 5)
        assert postponed.gameweek is None


def test_abort_leaves_db_untouched(session_factory, fake_client):
    # First a good sync, then a broken payload must NOT wipe the DB.
    sync_fpl(client=fake_client, session_factory=session_factory)

    class BrokenClient:
        def get_bootstrap(self):
            return {"teams": [], "events": []}  # missing 'elements'

        def get_fixtures(self):
            return []

        def close(self):
            pass

    import pytest

    from fpl.errors import SyncAborted

    with pytest.raises(SyncAborted):
        sync_fpl(client=BrokenClient(), session_factory=session_factory)
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Player)) == 3  # stale, intact


def test_draft_squad_joins_archive_end_to_end(session_factory, fake_client):
    """DoD: a fake draft squad can be joined against archive data end-to-end."""
    import uuid

    from fpl.models import DraftSquad

    sync_fpl(client=fake_client, session_factory=session_factory)
    # Simulate a prior season sitting in the archive.
    with session_factory() as s, s.begin():
        cols = [c.key for c in Player.__table__.columns if c.key != "updated_at"]
        for p in s.scalars(select(Player)).all():
            row = {c: getattr(p, c) for c in cols}
            row["season"] = "2025-26"
            s.merge(PlayerArchive(**row))
        s.add(DraftSquad(id=uuid.uuid4(), player_ids=[1, 2, 3], captain_id=1))

    with session_factory() as s:
        draft = s.scalars(select(DraftSquad)).first()
        joined = s.execute(
            select(PlayerArchive.web_name)
            .where(PlayerArchive.id.in_(draft.player_ids))
            .where(PlayerArchive.season == "2025-26")
        ).all()
        assert {r[0] for r in joined} == {"Saka", "Watkins", "Palmer"}
