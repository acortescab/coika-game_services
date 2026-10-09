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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coika_game_service.api.db.base import Base


class MatchStatus(enum.StrEnum):
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"
    REJECTED = "rejected"
    ABANDONED = "abandoned"

class GameMode(Base):
    """Model for game_modes table."""
    __tablename__ = "game_modes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4)
    game_mode: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)

    matches: Mapped[list["Match"]] = relationship(back_populates="game_mode")

class Match(Base):
    """Model for matches table"""
    __tablename__= "matches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4)
    # The auth service's `sub`. Players live in the auth service, so there is no FK here.
    player_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    game_mode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("game_modes.id"), nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[MatchStatus] = mapped_column(String(30), nullable=False, 
                                                default=MatchStatus.IN_PROGRESS)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, 
                                                 default=lambda: datetime.now(UTC),
                                                 server_default=func.now())
    finish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    game_mode: Mapped["GameMode"] = relationship(back_populates="matches")
    score: Mapped["Score | None"] = relationship(back_populates="match", uselist=False)

    __table_args__ = (
        CheckConstraint("status IN (" + ", ".join(f"'{s.value}'" for s in MatchStatus) + ")",
                        name="status_valid"),
        UniqueConstraint("player_id", "idempotency_key", name="uq_player_idempotency_key"),
        Index("ix_matches_player_started", "player_id", desc("started_at"), desc("id")),
        Index("uq_matches_one_open_per_player", "player_id", unique=True,
              postgresql_where=text(f"status = '{MatchStatus.IN_PROGRESS.value}'")))

class Score(Base):
    """Model for scores table"""
    __tablename__= "scores"

    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matches.id"),primary_key=True)
    score: Mapped[int] = mapped_column(nullable=False)
    pieces_dropped: Mapped[int] = mapped_column(nullable=False)
    highest_tier: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=lambda: datetime.now(UTC),
                                                 server_default=func.now())
    
    match: Mapped["Match"] = relationship(back_populates="score")

    __table_args__ = (
        CheckConstraint("score >= 0", name="score_non_negative"),
        CheckConstraint("pieces_dropped >= 0", name="pieces_dropped_non_negative"),
        CheckConstraint("highest_tier BETWEEN 0 AND 10", name="highest_tier_valid"),
    )

