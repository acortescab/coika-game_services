"""drop players table

The auth service owns players; the game service only keeps the `sub` in matches.player_id.

Revision ID: 9cd03747e5e0
Revises: 047bcca5bc4f
Create Date: 2026-10-09 17:52:51.131410

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9cd03747e5e0'
down_revision: Union[str, Sequence[str], None] = '047bcca5bc4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('fk_matches_player_id_players', 'matches', type_='foreignkey')
    op.drop_table('players')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'players',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('nickname', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_players')),
    )
    # Rebuild the rows the FK needs; the nickname is lost, so a placeholder is used
    op.execute(
        "INSERT INTO players (id, nickname) "
        "SELECT DISTINCT player_id, 'unknown' FROM matches"
    )
    op.create_foreign_key(
        'fk_matches_player_id_players', 'matches', 'players', ['player_id'], ['id']
    )
