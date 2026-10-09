import uuid

# Predefined game modes. The UUIDs are fixed here and in the migration that seeds them
# (alembic/versions/*_seed_game_modes.py), so they are identical in every environment.
# Never change an existing id: add a new mode with a new migration instead.
CLASSIC_GAME_MODE_ID = uuid.UUID("af6e8f8c-cab7-4e4d-8ca5-5c564eed4728")

GAME_MODES: dict[str, uuid.UUID] = {
    "classic": CLASSIC_GAME_MODE_ID,
}
