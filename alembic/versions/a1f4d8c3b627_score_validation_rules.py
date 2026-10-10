"""score validation rules (anti-cheat)

Adds the anti-cheat rules to game_modes (HU-06) and the reason a match was rejected.

The rule values of the three modes are PROVISIONAL: the tier minimums come from the GDD (§3.2)
and the piece interval from the GDD (§3.5), but `max_score_per_s` (and the score/duration
limits) still have to be calibrated with the simulation harness of the game repo (change
`m2-test-suite-signoff`). They are loose on purpose so no legitimate match is rejected until
then. Change them with an UPDATE of game_modes, no deploy needed.

Revision ID: a1f4d8c3b627
Revises: f7c3a8d2b915
Create Date: 2026-10-10 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a1f4d8c3b627'
down_revision: Union[str, Sequence[str], None] = 'f7c3a8d2b915'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REASONS = (
    'Duration exceeds max duration',
    'Duration is below min duration',
    'Score exceeds max score',
    'Score is below min score',
    'Pieces exceeds permitted rate',
    'Score exceeds score rate calculation',
    'Score is below min score of the highest tier',
)
GAME_MODE_CHECKS = {
    'max_score_ge_min_score': 'max_score >= min_score',
    'min_score_positive': 'min_score > 0',
    'max_score_per_second_positive': 'max_score_per_s > 0',
    'max_duration_positive': 'max_duration_s > 0',
    'min_duration_valid': 'min_duration_s >= 0 AND min_duration_s <= max_duration_s',
    'min_piece_interval_positive': 'min_piece_interval_ms > 0',
    'min_score_by_tier_size': 'cardinality(min_score_by_tier) = 11',
}


def upgrade() -> None:
    """Upgrade schema."""
    # Added nullable, filled for the rows that exist, then made NOT NULL
    op.add_column('game_modes', sa.Column('max_score', sa.Integer(), nullable=True))
    op.add_column('game_modes', sa.Column('min_score', sa.Integer(), nullable=True))
    op.add_column('game_modes', sa.Column('max_score_per_s', sa.Numeric(10, 2), nullable=True))
    op.add_column('game_modes', sa.Column('min_duration_s', sa.Integer(), nullable=True))
    op.add_column('game_modes', sa.Column('max_duration_s', sa.Integer(), nullable=True))
    op.add_column('game_modes', sa.Column('min_piece_interval_ms', sa.Integer(), nullable=True))
    op.add_column(
        'game_modes',
        sa.Column('min_score_by_tier', postgresql.ARRAY(sa.Integer()), nullable=True),
    )
    # Tier minimums: 0, 3, 9, 19, 34, 55, 83, 119, 164, 219, 285 (GDD §3.2)
    op.execute(
        """
        UPDATE game_modes SET
            max_score = 1000000,
            min_score = 1,
            max_score_per_s = 100.00,
            min_duration_s = 5,
            max_duration_s = 3600,
            min_piece_interval_ms = 500,
            min_score_by_tier = ARRAY[0, 3, 9, 19, 34, 55, 83, 119, 164, 219, 285]
        """
    )
    for column in (
        'max_score', 'min_score', 'max_score_per_s', 'min_duration_s', 'max_duration_s',
        'min_piece_interval_ms', 'min_score_by_tier',
    ):
        op.alter_column('game_modes', column, nullable=False)
    for name, condition in GAME_MODE_CHECKS.items():
        op.create_check_constraint(op.f(f'ck_game_modes_{name}'), 'game_modes', condition)

    op.add_column('matches', sa.Column('rejection_reason', sa.String(100), nullable=True))
    op.create_check_constraint(
        op.f('ck_matches_rejection_reason_valid'), 'matches',
        "rejection_reason IN (" + ", ".join(f"'{r}'" for r in REASONS) + ")",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_matches_rejection_reason_valid'), 'matches', type_='check')
    op.drop_column('matches', 'rejection_reason')
    for name in GAME_MODE_CHECKS:
        op.drop_constraint(op.f(f'ck_game_modes_{name}'), 'game_modes', type_='check')
    for column in (
        'min_score_by_tier', 'min_piece_interval_ms', 'max_duration_s', 'min_duration_s',
        'max_score_per_s', 'min_score', 'max_score',
    ):
        op.drop_column('game_modes', column)
