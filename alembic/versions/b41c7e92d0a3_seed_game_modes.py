"""seed game modes

Game modes are predefined. Their ids are written literally (not imported from the app) so this
migration keeps producing the same rows even if the application code changes later.

Revision ID: b41c7e92d0a3
Revises: 9cd03747e5e0
Create Date: 2026-10-09 19:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b41c7e92d0a3'
down_revision: Union[str, Sequence[str], None] = '9cd03747e5e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GAME_MODES = {
    'classic': 'af6e8f8c-cab7-4e4d-8ca5-5c564eed4728',
}


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint(
        op.f('uq_game_modes_game_mode'), 'game_modes', ['game_mode']
    )
    for name, mode_id in GAME_MODES.items():
        op.execute(
            sa.text(
                "INSERT INTO game_modes (id, game_mode) VALUES (CAST(:id AS uuid), :name) "
                "ON CONFLICT (game_mode) DO NOTHING"
            ).bindparams(id=mode_id, name=name)
        )


def downgrade() -> None:
    """Downgrade schema."""
    # A mode that already has matches cannot be deleted (FK): the downgrade fails loudly then.
    for mode_id in GAME_MODES.values():
        op.execute(sa.text("DELETE FROM game_modes WHERE id = CAST(:id AS uuid)").bindparams(id=mode_id))
    op.drop_constraint(op.f('uq_game_modes_game_mode'), 'game_modes', type_='unique')
