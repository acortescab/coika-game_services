"""game mode enum and zen mode

Restricts game_modes.game_mode to the values of the GameModeName enum and adds the zen mode.

The id is written literally (not imported from the app) so this migration keeps producing the
same rows even if the application code changes later.

Revision ID: f7c3a8d2b915
Revises: e5b7c2a94d16
Create Date: 2026-10-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7c3a8d2b915'
down_revision: Union[str, Sequence[str], None] = 'e5b7c2a94d16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ZEN_GAME_MODE_ID = 'c20a3c46-4347-4780-b11d-fff8ecf4c7a8'
CHECK_NAME = op.f('ck_game_modes_game_mode_valid')


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        sa.text(
            "INSERT INTO game_modes (id, game_mode) VALUES (CAST(:id AS uuid), 'zen') "
            "ON CONFLICT (game_mode) DO NOTHING"
        ).bindparams(id=ZEN_GAME_MODE_ID)
    )
    # Fails if a row holds a value outside the enum: fix that data before upgrading.
    op.create_check_constraint(
        CHECK_NAME, 'game_modes', "game_mode IN ('daily', 'classic', 'zen')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(CHECK_NAME, 'game_modes', type_='check')
    # A mode that already has matches cannot be deleted (FK): the downgrade fails loudly then.
    op.execute(
        sa.text("DELETE FROM game_modes WHERE id = CAST(:id AS uuid)").bindparams(
            id=ZEN_GAME_MODE_ID
        )
    )
