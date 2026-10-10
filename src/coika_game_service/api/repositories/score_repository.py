from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.db.models import Match, MatchStatus, Score
from coika_game_service.api.schemas.scores import CreateScoreRequest


class ScoreRepository:
    """
    Score repository
    """
    def __init__(self, db_reader: AsyncSession, db_writer: AsyncSession):
        """
        Initializes the Score repository with reader and writer database sessions.
        """
        self.read_db = db_reader
        self.write_db = db_writer

    async def create_score(self, match_id: UUID, payload: CreateScoreRequest) -> Score:
        """
        Creates the score of a match. Only flushes: the caller commits the transaction.
        """
        score = Score(
            match_id=match_id,
            score=payload.score,
            pieces_dropped=payload.pieces_dropped,
            highest_tier=payload.highest_tier
        )

        self.write_db.add(score)
        await self.write_db.flush()

        return score

    async def get_by_match_id(self, match_id: UUID, use_writer: bool = False) -> Score | None:
        """
        Gets the score of a match. Use the writer to see what was just committed, since the
        reader can lag behind.
        """
        query = select(Score).where(Score.match_id == match_id)
        db = self.write_db if use_writer else self.read_db
        result = await db.execute(query)

        return result.scalars().first()

    async def get_best_score(self, game_mode_id: UUID, player_id: UUID) -> int | None:
        """
        Gets the best score (the number) of a player in a game mode, or None if there is none.
        """
        query = select(func.max(Score.score)).join(Match, Match.id == Score.match_id).where(
            Match.game_mode_id == game_mode_id,
            Match.player_id == player_id,
            Match.status == MatchStatus.FINISHED
        )
        result = await self.read_db.execute(query)
        
        return result.scalar()

