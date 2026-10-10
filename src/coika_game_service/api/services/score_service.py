from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.core.exceptions import (
    InvalidScore,
    MatchNotFound,
    MatchNotOpen,
    RateLimitBlock,
    ScoreAlreadyExists,
)
from coika_game_service.api.db.models import (
    GameMode,
    GameModeName,
    Match,
    MatchStatus,
    RejectReason,
    Score,
)
from coika_game_service.api.repositories.game_mode_repository import GameModeRepository
from coika_game_service.api.repositories.match_repository import MatchRepository
from coika_game_service.api.repositories.redis_repository import CacheRepository
from coika_game_service.api.repositories.score_repository import ScoreRepository
from coika_game_service.api.schemas.scores import CreateScoreRequest


class ScoreService:
    """
    Score submission service
    """
    def __init__(
        self, score_repo: ScoreRepository, 
        match_repo: MatchRepository, 
        cache_repo: CacheRepository,
        game_mode_repo: GameModeRepository, 
        write_db: AsyncSession
    ):
        """
        Initializes the ScoreService. `write_db` must be the session both repositories write
        with: they only flush, and this service commits the transaction once.
        """
        self.score_repo = score_repo
        self.match_repo = match_repo
        self.cache_repo = cache_repo
        self.game_mode_repo = game_mode_repo
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
        with other figures), MatchNotOpen (abandoned or rejected) and InvalidScore (the
        anti-cheat rules refuse it: the match is marked rejected, with the reason, and no
        score is stored).
        """
        block, remaining, ttl = await self.cache_repo.hit_rate_limit(player_id, "create_score")
        if block:
            raise RateLimitBlock(str(player_id), remaining, ttl)

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

        max_score = await self.score_repo.get_best_score(match.game_mode_id, player_id)
        game_mode = await self.game_mode_repo.get_game_mode(match.game_mode_id)
        
        is_valid, reject_reason = self.is_score_valid(payload, match, game_mode)
        if is_valid:
            score = await self.score_repo.create_score(match_id, payload)
            await self.match_repo.finish_match(match_id)
            await self.write_db.commit()

            if (max_score is None or score.score > max_score):
                # The rankings in Redis are keyed by the name of the mode, as the endpoint
                # reads them
                if game_mode.game_mode == GameModeName.DAILY:
                    await self.cache_repo.update_max_score_daily(
                        player_id, game_mode.game_mode, score.score, match.seed)
                elif game_mode.game_mode is not None:
                    await self.cache_repo.update_max_score(
                        player_id, game_mode.game_mode, score.score)
            return score
        else:
            await self.match_repo.reject_match(match_id, reject_reason)
            await self.write_db.commit()

            raise InvalidScore(reject_reason)

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

    def is_score_valid(self, payload: CreateScoreRequest, match: Match, 
                       game_mode: GameMode) -> tuple[bool, RejectReason | None]:
        """
        Validates that the submitted score meet all the valid criteria
        """

        # The duration comes from the server's started_at, never from the client
        seconds = (datetime.now(UTC) - match.started_at).total_seconds()

        if seconds > game_mode.max_duration_s:
            return False, RejectReason.DURATION

        if seconds < game_mode.min_duration_s:
            return False, RejectReason.DURATION_MIN

        if payload.score > game_mode.max_score:
            return False, RejectReason.SCORE_MAX

        if payload.score < game_mode.min_score:
            return False, RejectReason.SCORE_MIN

        if payload.pieces_dropped > seconds * 1000 / game_mode.min_piece_interval_ms + 1:
            return False, RejectReason.PIECES

        # max_score_per_s is a Decimal when it comes from PostgreSQL
        if payload.score > seconds * float(game_mode.max_score_per_s):
            return False, RejectReason.SCORE_RATE

        if payload.score < game_mode.min_score_by_tier[payload.highest_tier]:
            return False, RejectReason.SCORE_TIER

        return True, None
