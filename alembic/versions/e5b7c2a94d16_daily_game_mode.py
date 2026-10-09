"""daily game mode

Adds the daily mode and the seed of a match. In the daily mode everybody plays the same seed on
a given UTC date, so the server decides it (the clock of the phone can be changed). The seed is
null in the other modes.

The id is written literally (not imported from the app) so this migration keeps producing the
same rows even if the application code changes later.

Revision ID: e5b7c2a94d16
Revises: d3a91c5e8f20
Create Date: 2026-10-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5b7c2a94d16'
down_revision: Union[str, Sequence[str], None] = 'd3a91c5e8f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DAILY_GAME_MODE_ID = '299a2835-4881-4f0c-98f4-0fceda03acca'


def upgrade() -> None:
    """Upgrade schema."""
    # yyyyMMdd (e.g. 20261010) fits an int32; null for the modes without a daily seed
    op.add_column('matches', sa.Column('seed', sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            "INSERT INTO game_modes (id, game_mode) VALUES (CAST(:id AS uuid), 'daily') "
            "ON CONFLICT (game_mode) DO NOTHING"
        ).bindparams(id=DAILY_GAME_MODE_ID)
    )


def downgrade() -> None:
    """Downgrade schema."""
    # A mode that already has matches cannot be deleted (FK): the downgrade fails loudly then.
    op.execute(
        sa.text("DELETE FROM game_modes WHERE id = CAST(:id AS uuid)").bindparams(
            id=DAILY_GAME_MODE_ID
        )
    )
    op.drop_column('matches', 'seed')
