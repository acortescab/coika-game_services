import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    desc,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coika_game_service.api.db.base import Base


class MatchStatus(enum.StrEnum):
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"
    REJECTED = "rejected"

class Player(Base):
    """Model for players table."""
    __tablename__ = "players"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True)
    nickname: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(UTC), server_default=func.now())

    matches: Mapped[list["Match"]] = relationship(back_populates="player")
                                      
class GameMode(Base):
    """Model for game_modes table."""
    __tablename__ = "game_modes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4)
    game_mode: Mapped[str] = mapped_column(String(30), nullable=False)

    matches: Mapped[list["Match"]] = relationship(back_populates="game_mode")

class Match(Base):
    """Model for matches table"""
    __tablename__= "matches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable = False)
    game_mode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("game_modes.id"), nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[MatchStatus] = mapped_column(String(30), nullable=False, 
                                                default=MatchStatus.IN_PROGRESS)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, 
                                                 default=lambda: datetime.now(UTC),
                                                 server_default=func.now())
    finish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    player: Mapped["Player"] = relationship(back_populates="matches")
    game_mode: Mapped["GameMode"] = relationship(back_populates="matches")
    score: Mapped["Score | None"] = relationship(back_populates="match", uselist=False)

    __table_args__ = (
        CheckConstraint("status IN (" + ", ".join(f"'{s.value}'" for s in MatchStatus) + ")",
                        name="status_valid"),
        UniqueConstraint("player_id", "idempotency_key", name="uq_player_idempotency_key"),
        Index("ix_matches_player_started", "player_id", desc("started_at"), desc("id")))

class Score(Base):
    """Model for scores table"""
    __tablename__= "scores"

    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matches.id"),primary_key=True)
    score: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=lambda: datetime.now(UTC),
                                                 server_default=func.now())
    
    match: Mapped["Match"] = relationship(back_populates="score")

