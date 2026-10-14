import argparse
import asyncio
import sys

import httpx
import redis.asyncio
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from coika_game_service.api.clients.auth_client import AuthClient
from coika_game_service.api.core.config import settings
from coika_game_service.api.core.exceptions import RebuildAlreadyRunning
from coika_game_service.api.db.models import GameModeName
from coika_game_service.api.repositories.game_mode_repository import GameModeRepository
from coika_game_service.api.repositories.match_repository import MatchRepository
from coika_game_service.api.repositories.redis_repository import RedisRepository
from coika_game_service.api.repositories.score_repository import ScoreRepository
from coika_game_service.api.services.leaderboard_service import LeaderboardService
from coika_game_service.api.services.player_name_service import PlayerNameService
from coika_game_service.api.services.score_service import ScoreService


def build_parser() -> argparse.ArgumentParser:
    """"""
    parser = argparse.ArgumentParser(prog="coika-game-service")
    sub = parser.add_subparsers(dest="command", required=True)

    rebuild = sub.add_parser("rebuild-leaderboards", help="Rebuild leaderboards")
    rebuild.add_argument("--game-mode", help="Rebuild only this game mode", 
                         choices=[m.value for m in GameModeName])
    rebuild.set_defaults(func=rebuild_leaderboards)

    return parser

def rebuild_leaderboards(args: argparse.Namespace) -> None:
    """"""
    try:
        count = asyncio.run(rebuild_leaderboards_async(args))
    except RebuildAlreadyRunning:
        sys.exit("Another rebuild is already running; try again when it finishes.")
    print (f"leaderboards executed {count}")


async def rebuild_leaderboards_async(args: argparse.Namespace) -> int:
    """Rebuild leaderboards, optionally for a single game mode."""
    engine_writer = create_async_engine(settings.DATABASE_URL_WRITER, pool_pre_ping=True)
    engine_reader = create_async_engine(settings.DATABASE_URL_READER, pool_pre_ping=True)
    cache = redis.asyncio.from_url(settings.REDIS_URL)
    http_client = httpx.AsyncClient(timeout=5.0)
    try:
        return await rebuild_leaderboards_process(
            args.game_mode, 
            async_sessionmaker(engine_writer),
            async_sessionmaker(engine_reader),
            cache,
            http_client)
    finally:
        await engine_writer.dispose()
        await engine_reader.dispose()
        await cache.aclose()
        await http_client.aclose()

async def rebuild_leaderboards_process(
        game_mode: str | None,
        write_session: async_sessionmaker, 
        read_session: async_sessionmaker,
        cache: Redis,
        auth: httpx.AsyncClient) -> int:
    """Rebuild leaderboards, optionally for a single game mode."""
    async with write_session() as write_db, read_session() as read_db:
        score_repo = ScoreRepository(read_db, write_db)
        match_repo = MatchRepository(read_db, write_db)
        cache_repo = RedisRepository(cache)
        game_mode_repo = GameModeRepository(read_db, write_db)
        score_service = ScoreService(
            score_repo, match_repo, cache_repo, game_mode_repo, write_db)
        auth_client = AuthClient(auth, settings.AUTH_PLAYERS_URL)
        player_name_service = PlayerNameService(auth_client, cache_repo)
        leaderboard_service = LeaderboardService(
            cache_repo, player_name_service, score_service)
        return await leaderboard_service.rebuild_ranking(game_mode)

        
def main() -> None:
    args = build_parser().parse_args()
    args.func(args)