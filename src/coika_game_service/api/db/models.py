import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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

class GameModeName(enum.StrEnum):
    DAILY = "daily"
    CLASSIC = "classic"
    ZEN = "zen"

class RejectReason(enum.StrEnum):
    DURATION = "Duration exceeds max duration"
    DURATION_MIN = "Duration is below min duration"
    SCORE_MAX = "Score exceeds max score"
    SCORE_MIN = "Score is below min score"
    PIECES = "Pieces exceeds permitted rate"
    SCORE_RATE = "Score exceeds score rate calculation"
    SCORE_TIER = "Score is below min score of the highest tier"

class GameMode(Base):
    """Model for game_modes table."""
    __tablename__ = "game_modes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4)
    game_mode: Mapped[GameModeName] = mapped_column(String(30), nullable=False, unique=True)
    max_score: Mapped[int] = mapped_column(nullable=False)
    min_score: Mapped[int] = mapped_column(nullable=False)
    max_score_per_s: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    min_duration_s: Mapped[int] = mapped_column(nullable=False)
    max_duration_s: Mapped[int] = mapped_column(nullable=False)
    min_piece_interval_ms: Mapped[int] = mapped_column(nullable=False)
    min_score_by_tier: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)

    matches: Mapped[list["Match"]] = relationship(back_populates="game_mode")

    __table_args__ = (
        CheckConstraint("game_mode IN (" + ", ".join(f"'{m.value}'" for m in GameModeName) + ")",
                        name="game_mode_valid"),
        CheckConstraint("max_score >= min_score", name="max_score_ge_min_score"),
        CheckConstraint("min_score > 0", name="min_score_positive"),
        CheckConstraint("max_score_per_s > 0", name="max_score_per_second_positive"),
        CheckConstraint("max_duration_s > 0", name="max_duration_positive"),
        CheckConstraint("min_duration_s >= 0 AND min_duration_s <= max_duration_s",
                        name="min_duration_valid"),
        CheckConstraint("min_piece_interval_ms > 0", name="min_piece_interval_positive"),
        # One minimum per tier, 0 to 10
        CheckConstraint("cardinality(min_score_by_tier) = 11", name="min_score_by_tier_size"),
    )

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
    # yyyyMMdd of the UTC date the server gave in the daily mode; null in the other modes
    seed: Mapped[int | None] = mapped_column(nullable=True)
    # Why the anti-cheat rules rejected the match (HU-06); null in any other status
    rejection_reason: Mapped[RejectReason | None] = mapped_column(String(100), nullable=True)

    game_mode: Mapped["GameMode"] = relationship(back_populates="matches")
    score: Mapped["Score | None"] = relationship(back_populates="match", uselist=False)

    __table_args__ = (
        CheckConstraint("status IN (" + ", ".join(f"'{s.value}'" for s in MatchStatus) + ")",
                        name="status_valid"),
        CheckConstraint(
            "rejection_reason IN (" + ", ".join(f"'{s.value}'" for s in RejectReason) + ")",
            name="rejection_reason_valid"),
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

