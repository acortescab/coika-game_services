from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.db.models import GameMode, Match

UNIQUE_VIOLATION = "23505"
IDEMPOTENCY_CONSTRAINT = "uq_player_idempotency_key"


class MatchRepository:
    """
    Match repository
    """
    def __init__(self, db_reader: AsyncSession, db_writer: AsyncSession):
        """
        Initializes the Match repository with reader and writer database sessions.
        """
        self.read_db = db_reader
        self.write_db = db_writer

    async def game_mode_exists(self, game_mode_id: UUID) -> bool:
        """
        True if the game mode is in the catalog.
        """
        query = select(GameMode.id).where(GameMode.id == game_mode_id)
        result = await self.read_db.execute(query)

        return result.first() is not None

    async def get_by_idempotency_key(
        self, player_id: UUID, idempotency_key: UUID, use_writer: bool = False
    ) -> Match | None:
        """
        Get the match a player created with the given idempotency key.
        """
        query = select(Match).where(
            Match.player_id == player_id, Match.idempotency_key == idempotency_key
        )
        db = self.write_db if use_writer else self.read_db
        result = await db.execute(query)

        return result.scalars().first()

    async def create_match(
        self, player_id: UUID, game_mode_id: UUID, idempotency_key: UUID
    ) -> Match | None:
        """
        Creates a match. Returns None if the player already used this idempotency key
        (a retry, or a concurrent request that created it first).
        """
        match = Match(
            player_id=player_id,
            game_mode_id=game_mode_id,
            idempotency_key=idempotency_key,
        )

        self.write_db.add(match)
        try:
            await self.write_db.flush()
        except IntegrityError as exc:
            await self.write_db.rollback()
            if self._is_idempotency_conflict(exc):
                return None  # already exists: another request created it first
            raise

        return match

    async def commit(self):
        """
        Commits the pending changes of the writer session.
        Write methods only flush, so the caller decides which operations form one transaction.
        """
        await self.write_db.commit()

    @staticmethod
    def _is_idempotency_conflict(exc: IntegrityError) -> bool:
        """
        True only for the (player_id, idempotency_key) unique constraint, not for other
        unique violations such as the primary key.
        """
        if getattr(exc.orig, "sqlstate", None) != UNIQUE_VIOLATION:
            return False
        cause = getattr(exc.orig, "__cause__", None)
        return getattr(cause, "constraint_name", None) == IDEMPOTENCY_CONSTRAINT
