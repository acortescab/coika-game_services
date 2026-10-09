import uuid
from datetime import UTC, datetime

# Predefined game modes. The UUIDs are fixed here and in the migrations that seed them
# (alembic/versions/*_seed_game_modes.py, *_daily_game_mode.py), so they are identical in
# every environment. Never change an existing id: add a new mode with a new migration instead.
CLASSIC_GAME_MODE_ID = uuid.UUID("af6e8f8c-cab7-4e4d-8ca5-5c564eed4728")
DAILY_GAME_MODE_ID = uuid.UUID("299a2835-4881-4f0c-98f4-0fceda03acca")

GAME_MODES: dict[str, uuid.UUID] = {
    "classic": CLASSIC_GAME_MODE_ID,
    "daily": DAILY_GAME_MODE_ID,
}


def is_daily(game_mode_id: uuid.UUID) -> bool:
    """
    True for the daily mode: everyone plays the same seed on a given UTC date. The mode is
    recognised by its fixed id, so no column is needed in the catalog.
    """
    return game_mode_id == DAILY_GAME_MODE_ID


def daily_seed(moment: datetime) -> int:
    """
    The seed of the daily mode: the UTC date of `moment` as yyyyMMdd (e.g. 20261010). The date
    is always the UTC one, whatever the zone of `moment`, so everybody gets the same number
    on the same day. `moment` must carry a time zone.
    """
    day = moment.astimezone(UTC)
    return day.year * 10_000 + day.month * 100 + day.day
