from pydantic import BaseModel


class LeaderboardResponse(BaseModel):
    """
    Schema for leaderboard response
    """
    player_id: str
    name: str
    score: int