from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from coika_game_service.api.core.dependencies import BearerToken
from coika_game_service.api.core.factories import get_leaderboard_service
from coika_game_service.api.schemas.leaderboard import LeaderboardResponse
from coika_game_service.api.services.leaderboard_service import LeaderboardService

router = APIRouter(tags=["leaderboard"])

@router.get("/game-modes/{game_mode_id}/leaderboard", 
            response_model=list[LeaderboardResponse])
async def get_leaderboard(
    game_mode_id: UUID,
    token: BearerToken,
    leaderboard_service: Annotated[LeaderboardService, Depends(get_leaderboard_service)],
    limit:  Annotated[int, Query(ge=1, le=100)] = 50,
    date: Annotated[str | None, Query(pattern=r"^\d{8}$")] = None):
    """
    Get leaderboard for a game mode
    """
    return await leaderboard_service.get_leaderboard(token, game_mode_id, limit, date)
