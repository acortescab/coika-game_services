"""one open match per player

A player plays one match at a time, and the unfinished one is closed when a new one starts.
That closed match gets the new status 'abandoned', separate from 'rejected' (anti-cheat, HU-06).

Revision ID: c8d2f5a1e7b4
Revises: b41c7e92d0a3
Create Date: 2026-10-09 20:05:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c8d2f5a1e7b4'
down_revision: Union[str, Sequence[str], None] = 'b41c7e92d0a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECK_NAME = op.f('ck_matches_status_valid')


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(CHECK_NAME, 'matches', type_='check')
    op.create_check_constraint(
        CHECK_NAME, 'matches',
        "status IN ('in_progress', 'finished', 'rejected', 'abandoned')",
    )

    # Before enforcing one open match per player, close all but the latest one of each player
    op.execute(
        """
        UPDATE matches SET status = 'abandoned', finish_at = now()
        WHERE status = 'in_progress' AND id NOT IN (
            SELECT DISTINCT ON (player_id) id FROM matches WHERE status = 'in_progress'
            ORDER BY player_id, started_at DESC, id DESC
        )
        """
    )
    op.create_index(
        'uq_matches_one_open_per_player', 'matches', ['player_id'], unique=True,
        postgresql_where="status = 'in_progress'",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'uq_matches_one_open_per_player', table_name='matches',
        postgresql_where="status = 'in_progress'",
    )
    op.drop_constraint(CHECK_NAME, 'matches', type_='check')
    # The old schema has no 'abandoned': those matches were closed without a valid score, so
    # the closest old status is 'rejected'
    op.execute("UPDATE matches SET status = 'rejected' WHERE status = 'abandoned'")
    op.create_check_constraint(
        CHECK_NAME, 'matches', "status IN ('in_progress', 'finished', 'rejected')"
    )
