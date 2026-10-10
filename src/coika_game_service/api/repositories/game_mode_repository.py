from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coika_game_service.api.db.models import GameMode


class GameModeRepository:
    """
    This class is responsible for handling all database operations related to GameModes.
    """
    def __init__(self, db_reader: AsyncSession, db_writer: AsyncSession):
        """
        Initialize the GameModeRepository with a database session.
        """
        self.read_db = db_reader
        self.write_db = db_writer

    async def get_game_mode(self, game_mode_id: UUID) -> GameMode:
        """
        Get a game mode by its id.
        """
        query = select(GameMode.id).where(GameMode.id == game_mode_id)
        result = await self.read_db.execute(query)
    
        return result.scalars().first()
    