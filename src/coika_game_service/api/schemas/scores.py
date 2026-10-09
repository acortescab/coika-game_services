from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_TIER = 10


class CreateScoreRequest(BaseModel):
    """
    CreateScore request body: the figures of the end-of-match screen. The match duration is
    not sent by the client, the server takes it from the match's `started_at`.
    """
    score: int = Field(ge=0, le=2_147_483_647)
    pieces_dropped: int = Field(ge=0, le=2_147_483_647)
    highest_tier: int = Field(ge=0, le=MAX_TIER)


class CreateScoreResponse(BaseModel):
    """
    CreateScore response body. Always built from the stored score, so a retry gets exactly
    the same response as the original request.
    """
    model_config = ConfigDict(from_attributes=True)

    match_id: UUID
    score: int
    pieces_dropped: int
    highest_tier: int
    created_at: datetime
