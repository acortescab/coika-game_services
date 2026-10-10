from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from coika_game_service.api.core.dependencies import CurrentPlayer
from coika_game_service.api.core.factories import get_score_service
from coika_game_service.api.schemas.scores import CreateScoreRequest, CreateScoreResponse
from coika_game_service.api.services.score_service import ScoreService

router = APIRouter(tags=["scores"])


@router.post("/matches/{match_id}/score", response_model=CreateScoreResponse, status_code=201)
async def create_score(
    payload: CreateScoreRequest,
    player_id: CurrentPlayer,
    match_id: UUID,
    score_service: Annotated[ScoreService, Depends(get_score_service)]):
    """
    Submit the score of a match. Only the owner can, and only while the match is in progress.
    Sending the same score again returns the original response instead of storing it twice.
    """
    return await score_service.create_score(player_id, match_id, payload)
