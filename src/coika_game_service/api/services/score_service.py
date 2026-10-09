from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.db.models import MatchStatus, Score
from coika_game_service.api.repositories.match_repository import MatchRepository
from coika_game_service.api.repositories.score_repository import ScoreRepository
from coika_game_service.api.schemas.scores import CreateScoreRequest


class MatchNotFound(Exception):
    """The match does not exist or belongs to another player."""


class MatchNotOpen(Exception):
    """The match is not in progress (abandoned or rejected), so it accepts no score."""


class ScoreAlreadyExists(Exception):
    """The match already has a score and the new submission carries different figures."""


class ScoreService:
    """
    Score submission service
    """
    def __init__(
        self, score_repo: ScoreRepository, match_repo: MatchRepository, write_db: AsyncSession
    ):
        """
        Initializes the ScoreService. `write_db` must be the session both repositories write
        with: they only flush, and this service commits the transaction once.
        """
        self.score_repo = score_repo
        self.match_repo = match_repo
        self.write_db = write_db

    async def create_score(
        self, player_id: UUID, match_id: UUID, payload: CreateScoreRequest
    ) -> Score:
        """
        Stores the score of a match and finishes the match, in one transaction. Sending the
        same figures again for a finished match is a retry: it returns the stored score.

        The match row stays locked until the commit, so concurrent submissions of the same
        match, and the abandon of that match by a new one, run one after another.

        Raises MatchNotFound (not the owner or no such match), ScoreAlreadyExists (finished
        with other figures) and MatchNotOpen (abandoned or rejected).
        """
        match = await self.match_repo.get_for_update(match_id)
        if match is None or match.player_id != player_id:
            raise MatchNotFound(str(match_id))

        if match.status == MatchStatus.FINISHED:
            # Read from the writer: the score was committed by the request we waited for
            stored = await self.score_repo.get_by_match_id(match_id, use_writer=True)
            if stored is not None and self.is_same_submission(stored, payload):
                return stored
            raise ScoreAlreadyExists(str(match_id))

        if match.status != MatchStatus.IN_PROGRESS:
            raise MatchNotOpen(str(match_id))

        score = await self.score_repo.create_score(match_id, payload)
        await self.match_repo.finish_match(match_id)
        await self.write_db.commit()
        return score

    @staticmethod
    def is_same_submission(stored: Score, payload: CreateScoreRequest) -> bool:
        """
        True if the stored score was saved from these same figures (a retry of the request).
        """
        return (stored.score, stored.pieces_dropped, stored.highest_tier) == (
            payload.score,
            payload.pieces_dropped,
            payload.highest_tier,
        )
