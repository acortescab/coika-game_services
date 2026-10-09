from pydantic import BaseModel


class CreateMatchResponse(BaseModel):
    match_id: str
    status: str
