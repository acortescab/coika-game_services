from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response

from coika_game_service.api.dependencies import current_player, get_match_service
from coika_game_service.api.schemas.matches import CreateMatchResponse
from coika_game_service.api.services.match_service import MatchService

router = APIRouter(prefix="/game-modes/{game_mode_id}", tags=["matches"])

@router.post("/matches", response_model=CreateMatchResponse, status_code=201)
async def create_match(
    game_mode_id: UUID,
    player_id: Annotated[UUID, Depends(current_player)],
    match_service: Annotated[MatchService, Depends(get_match_service)],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    response: Response):
    """
    Create a new match for the specified game mode. Retrying with the same Idempotency-Key
    returns the original match (200) instead of creating another one.
    """
    match, created = await match_service.create_match(player_id, game_mode_id, idempotency_key)
    if not created:
        response.status_code = 200
    return CreateMatchResponse(match_id=str(match.id), status=match.status)
