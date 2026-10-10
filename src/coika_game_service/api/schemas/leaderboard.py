from pydantic import BaseModel


class LeaderboardResponse(BaseModel):
    """
    Schema for leaderboard response
    """
    player_id: str
    position: int
    name: str
    score: int