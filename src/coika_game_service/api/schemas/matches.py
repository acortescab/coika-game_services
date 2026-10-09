from uuid import UUID

from pydantic import BaseModel


class CreateMatchRequest(BaseModel):
    """
    CreateMatch request body
    """
    game_mode_id: UUID


class CreateMatchResponse(BaseModel):
    """
    CreateMatch response body
    """
    match_id: str
    status: str
