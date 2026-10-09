"""scores pieces_dropped and highest_tier

The client reports these two figures with the score (the end-of-match screen of the game shows
them). The anti-cheat rules (HU-06) check them against the score and the match duration.

Revision ID: d3a91c5e8f20
Revises: c8d2f5a1e7b4
Create Date: 2026-10-09 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3a91c5e8f20'
down_revision: Union[str, Sequence[str], None] = 'c8d2f5a1e7b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NOT NULL without a default: no score is stored before this migration (HU-05 adds the
    # endpoint that writes them), so there are no rows to fill.
    op.add_column('scores', sa.Column('pieces_dropped', sa.Integer(), nullable=False))
    op.add_column('scores', sa.Column('highest_tier', sa.Integer(), nullable=False))
    op.create_check_constraint(
        op.f('ck_scores_score_non_negative'), 'scores', 'score >= 0'
    )
    op.create_check_constraint(
        op.f('ck_scores_pieces_dropped_non_negative'), 'scores', 'pieces_dropped >= 0'
    )
    op.create_check_constraint(
        op.f('ck_scores_highest_tier_valid'), 'scores', 'highest_tier BETWEEN 0 AND 10'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_scores_highest_tier_valid'), 'scores', type_='check')
    op.drop_constraint(op.f('ck_scores_pieces_dropped_non_negative'), 'scores', type_='check')
    op.drop_constraint(op.f('ck_scores_score_non_negative'), 'scores', type_='check')
    op.drop_column('scores', 'highest_tier')
    op.drop_column('scores', 'pieces_dropped')
