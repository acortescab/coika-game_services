from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings
    """
    model_config = SettingsConfigDict(env_file=".env", env_prefix="COIKA_", extra="ignore")

    ENV: str = "dev"
    REDIS_URL: str = "redis://localhost:6379/0"
    DATABASE_URL_WRITER: str = ""
    DATABASE_URL_READER: str = ""
    AUTH_JWKS_URL: str = "http://localhost:8000/.well-known/jwks.json"
    AUTH_ISSUER: str = "coika-auth"
    AUTH_AUDIENCE: str = "coika-game"
    JWKS_CACHE_TTL_SECONDS: int = 300
    # Lookup of player names (the auth service owns name and avatar) and how long they stay cached
    AUTH_PLAYERS_URL: str = "http://localhost:8000/v0/players/lookup"
    PLAYER_NAME_CACHE_TTL_SECONDS: int = 300
    # How long a daily ranking stays in Redis after its last score (48 h)
    SCORES_TTL_SECONDS: int = 172800
    RATE_LIMIT_COUNTER: int = 50
    RATE_LIMIT_WINDOW: int = 120


settings = Settings()
