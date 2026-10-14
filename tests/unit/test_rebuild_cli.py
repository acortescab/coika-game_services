"""HU-10: the rebuild-leaderboards command, without touching any database."""
from argparse import Namespace

import pytest

from coika_game_service.api.core import cli
from coika_game_service.api.core.exceptions import RebuildAlreadyRunning


def test_game_mode_is_optional():
    args = cli.build_parser().parse_args(["rebuild-leaderboards"])

    assert args.command == "rebuild-leaderboards"
    assert args.game_mode is None


def test_game_mode_is_taken_from_the_options():
    args = cli.build_parser().parse_args(["rebuild-leaderboards", "--game-mode", "zen"])

    assert args.game_mode == "zen"


def test_an_unknown_game_mode_is_refused_before_running_anything(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["rebuild-leaderboards", "--game-mode", "clasic"])

    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_a_command_is_required(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_it_prints_how_many_rankings_were_rebuilt(monkeypatch, capsys):
    async def fake_rebuild(args):
        assert args.game_mode == "classic"
        return 3

    monkeypatch.setattr(cli, "rebuild_leaderboards_async", fake_rebuild)

    cli.rebuild_leaderboards(Namespace(game_mode="classic"))

    assert "3" in capsys.readouterr().out


def test_it_exits_with_an_error_when_another_rebuild_is_running(monkeypatch, capsys):
    async def locked(args):
        raise RebuildAlreadyRunning()

    monkeypatch.setattr(cli, "rebuild_leaderboards_async", locked)

    with pytest.raises(SystemExit) as exit_info:
        cli.rebuild_leaderboards(Namespace(game_mode=None))

    # sys.exit("message") prints the message to stderr and exits with code 1
    assert exit_info.value.code != 0
    assert "already running" in str(exit_info.value.code)
    assert "leaderboards executed" not in capsys.readouterr().out
