"""HU-10: automatic rebuild at startup when Redis is empty (configurable). No databases here."""
from types import SimpleNamespace

import pytest

from coika_game_service import main
from coika_game_service.api.core.config import Settings
from coika_game_service.api.core.exceptions import RebuildAlreadyRunning


class FakeRedis:
    def __init__(self, keys):
        self.keys = keys

    async def dbsize(self):
        return self.keys


def make_app(keys):
    state = SimpleNamespace(
        redis=FakeRedis(keys), session_writer="writer", session_reader="reader", http="http")
    return SimpleNamespace(state=state)


@pytest.fixture
def rebuild_calls(monkeypatch):
    """Replaces the rebuild with a recorder; returns the list of its calls."""
    calls = []

    async def fake_rebuild(*args):
        calls.append(args)
        return 1

    monkeypatch.setattr(main, "rebuild_leaderboards_process", fake_rebuild)
    return calls


def test_it_is_off_by_default():
    # Without an env file, so the developer's .env does not decide the result
    assert Settings(_env_file=None).REBUILD_ON_EMPTY_REDIS is False


async def test_nothing_runs_when_the_option_is_off(monkeypatch, rebuild_calls):
    monkeypatch.setattr(main.settings, "REBUILD_ON_EMPTY_REDIS", False)

    await main.redis_leaderboard_populate(make_app(keys=0))

    assert rebuild_calls == []


async def test_an_empty_redis_triggers_a_rebuild_of_everything(monkeypatch, rebuild_calls):
    monkeypatch.setattr(main.settings, "REBUILD_ON_EMPTY_REDIS", True)
    app = make_app(keys=0)

    await main.redis_leaderboard_populate(app)

    # No game mode (all of them), with the resources the app already has
    assert rebuild_calls == [(None, "writer", "reader", app.state.redis, "http")]


async def test_a_redis_with_data_is_left_alone(monkeypatch, rebuild_calls):
    monkeypatch.setattr(main.settings, "REBUILD_ON_EMPTY_REDIS", True)

    await main.redis_leaderboard_populate(make_app(keys=12))

    assert rebuild_calls == []


async def test_a_failing_rebuild_does_not_stop_the_startup(monkeypatch, capsys):
    monkeypatch.setattr(main.settings, "REBUILD_ON_EMPTY_REDIS", True)

    async def broken(*args):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(main, "rebuild_leaderboards_process", broken)

    await main.redis_leaderboard_populate(make_app(keys=0))  # must not raise

    assert "failed" in capsys.readouterr().out


async def test_another_rebuild_in_progress_skips_without_failing(monkeypatch, capsys):
    monkeypatch.setattr(main.settings, "REBUILD_ON_EMPTY_REDIS", True)

    async def locked(*args):
        raise RebuildAlreadyRunning()

    monkeypatch.setattr(main, "rebuild_leaderboards_process", locked)

    await main.redis_leaderboard_populate(make_app(keys=0))  # must not raise

    assert "skipped" in capsys.readouterr().out
