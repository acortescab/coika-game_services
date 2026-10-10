from typing import Annotated

from fastapi import APIRouter, Depends, Query

from coika_game_service.api.core.dependencies import BearerToken, CurrentPlayer
from coika_game_service.api.core.factories import get_leaderboard_service
from coika_game_service.api.db.models import GameModeName
from coika_game_service.api.schemas.leaderboard import LeaderboardResponse
from coika_game_service.api.services.leaderboard_service import LeaderboardService

router = APIRouter(tags=["leaderboard"])

@router.get("/game-modes/{game_mode}/leaderboard",
            response_model=list[LeaderboardResponse])
async def get_leaderboard(
    game_mode: GameModeName,
    token: BearerToken,
    leaderboard_service: Annotated[LeaderboardService, Depends(get_leaderboard_service)],
    limit:  Annotated[int, Query(ge=1, le=100)] = 50,
    date: Annotated[str | None, Query(pattern=r"^\d{8}$")] = None):
    """
    Get leaderboard for a game mode
    """
    return await leaderboard_service.get_leaderboard(token, game_mode, limit, date)

@router.get("/game-modes/{game_mode}/leaderboard/me",
            response_model=list[LeaderboardResponse],
            responses={404: {"description": "The player has no score in this leaderboard"}})
async def get_leaderboard_me(
    game_mode: GameModeName,
    player_id: CurrentPlayer,
    token: BearerToken,
    leaderboard_service: Annotated[LeaderboardService, Depends(get_leaderboard_service)],
    around:  Annotated[int, Query(ge=1, le=50)] = 25,
    date: Annotated[str | None, Query(pattern=r"^\d{8}$")] = None):
    """
    Get the leaderboard around the authenticated player: the player and up to `around`
    neighbours above and below, each with its position, name and score.
    """
    return await leaderboard_service.get_leaderboard_me(token, player_id, game_mode, around, date)
