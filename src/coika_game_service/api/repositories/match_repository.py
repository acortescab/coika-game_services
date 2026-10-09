from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.db.models import GameMode, Match, MatchStatus


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

    async def get_by_id(self, match_id: UUID) -> Match | None:
        """
        Get a match by its id.
        """
        query = select(Match).where(Match.id == match_id)
        result = await self.read_db.execute(query)
        
        return result.scalars().first()

    async def get_for_update(self, match_id: UUID) -> Match | None:
        """
        Get a match by its id locking its row until the end of the transaction: whoever wants
        to change it (another submission, or the abandon by a new match) waits. Read from the
        writer, and refresh the object so it shows the state after the wait.
        """
        query = (
            select(Match)
            .where(Match.id == match_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.write_db.execute(query)

        return result.scalars().first()

    async def abandon_open_matches(self, player_id: UUID) -> None:
        """
        Closes the player's unfinished match, if any. Only flushes: it belongs to the same
        transaction as the insert of the new match.
        """
        statement = (
            update(Match)
            .where(Match.player_id == player_id, Match.status == MatchStatus.IN_PROGRESS)
            .values(status=MatchStatus.ABANDONED, finish_at=func.now())
            .execution_options(synchronize_session=False)
        )
        await self.write_db.execute(statement)

    async def finish_match(self, match_id: UUID) -> None:
        """
        Finish a match.
        Only flushes: the caller decides which operations form one transaction.
        The match must be in progress.
        """

        statement = (
            update(Match)
            .where(Match.id == match_id)
            .values(status=MatchStatus.FINISHED, finish_at=func.now())
            # "fetch" also refreshes the already loaded Match; True is not a valid value
            .execution_options(synchronize_session="fetch")
        )
        await self.write_db.execute(statement)


    async def lock_player(self, player_id: UUID) -> None:
        """
        Serializes the transactions of one player: a second request of the same player waits
        here until the first one commits or rolls back. The lock is released automatically
        at the end of the transaction. A hash collision between two players only makes them
        wait for each other, it breaks nothing.
        """
        # A plain SELECT ... FOR UPDATE cannot be used: there is no players table and a new
        # player has no row to lock yet. An advisory lock locks a key instead of a row.
        await self.write_db.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(str(player_id), 0)))
        )

    async def create_match(
        self, player_id: UUID, game_mode_id: UUID, idempotency_key: UUID
    ) -> Match:
        """
        Creates a match. Only flushes. The caller holds lock_player, so the unique
        constraints (idempotency key, one open match per player) cannot be hit by a
        concurrent request; if one fails anyway it is a bug and the error propagates.
        """
        match = Match(
            player_id=player_id,
            game_mode_id=game_mode_id,
            idempotency_key=idempotency_key,
        )

        self.write_db.add(match)
        await self.write_db.flush()

        return match

