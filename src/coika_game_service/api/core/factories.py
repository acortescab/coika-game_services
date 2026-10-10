from typing import Annotated

import httpx
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.clients.auth_client import AuthClient
from coika_game_service.api.core.config import settings
from coika_game_service.api.core.dependencies import (
    get_auth_client,
    get_reader_session,
    get_redis,
    get_writer_session,
)
from coika_game_service.api.repositories.game_mode_repository import GameModeRepository
from coika_game_service.api.repositories.match_repository import MatchRepository
from coika_game_service.api.repositories.redis_repository import RedisRepository
from coika_game_service.api.repositories.score_repository import ScoreRepository
from coika_game_service.api.services.leaderboard_service import LeaderboardService
from coika_game_service.api.services.match_service import MatchService
from coika_game_service.api.services.player_name_service import PlayerNameService
from coika_game_service.api.services.score_service import ScoreService


def get_match_service(
        write_db: Annotated[AsyncSession, Depends(get_writer_session)], 
        read_db: Annotated[AsyncSession, Depends(get_reader_session)],
        cache: Annotated[AsyncSession, Depends(get_redis)]) -> MatchService:
    """
    Create a match service with the given database sessions.
    """
    match_repo = MatchRepository(read_db, write_db)
    game_mode_repo = GameModeRepository(read_db, write_db)
    cache_repo = RedisRepository(cache)
    return MatchService(match_repo, game_mode_repo, cache_repo, write_db)

def get_score_service(
        write_db: Annotated[AsyncSession, Depends(get_writer_session)], 
        read_db: Annotated[AsyncSession, Depends(get_reader_session)],
        cache: Annotated[AsyncSession, Depends(get_redis)]) -> ScoreService:
    """
    Create a score service with the given database sessions.
    """
    score_repo = ScoreRepository(read_db, write_db)
    match_repo = MatchRepository(read_db, write_db)
    game_mode_repo = GameModeRepository(read_db, write_db)
    cache_repo = RedisRepository(cache)
    return ScoreService(score_repo, match_repo, cache_repo, game_mode_repo, write_db)

def get_player_name_service(
        client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
        cache: Annotated[AsyncSession, Depends(get_redis)]) -> PlayerNameService:
    """
    Get the service that resolves player names through the auth service, cached in Redis.
    """
    client_con = AuthClient(client, settings.AUTH_PLAYERS_URL)
    cache_repo = RedisRepository(cache)
    return PlayerNameService(client_con, cache_repo)

def get_leaderboard_service(
        player_name_service: Annotated[PlayerNameService, Depends(get_player_name_service)],
        cache: Annotated[AsyncSession, Depends(get_redis)]) -> LeaderboardService:
    """
    Create a leaderboard service. It only needs Redis and the player names: no database.
    """
    cache_repo = RedisRepository(cache)
    return LeaderboardService(cache_repo, player_name_service)


