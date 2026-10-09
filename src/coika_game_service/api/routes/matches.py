from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from coika_game_service.api.dependencies import current_player, get_match_service
from coika_game_service.api.schemas.matches import CreateMatchRequest, CreateMatchResponse
from coika_game_service.api.services.match_service import MatchService

router = APIRouter(prefix="/matches", tags=["matches"])

@router.post("", response_model=CreateMatchResponse, status_code=201)
async def create_match(
    body: CreateMatchRequest,
    player_id: Annotated[UUID, Depends(current_player)],
    match_service: Annotated[MatchService, Depends(get_match_service)],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")]):
    """
    Create a new match for the game mode of the body. Retrying with the same Idempotency-Key
    does not create another one: it gets the same response as the original request (201).
    """
    match, _ = await match_service.create_match(player_id, body.game_mode_id, idempotency_key)
    return CreateMatchResponse(match_id=str(match.id), status=match.status)
