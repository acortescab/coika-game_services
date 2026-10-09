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
    AUTH_JWKS_URL: str = "http://localhost:8001/.well-known/jwks.json"
    AUTH_ISSUER: str = "coika-auth"
    AUTH_AUDIENCE: str = "coika-game"
    JWKS_CACHE_TTL_SECONDS: int = 300


settings = Settings()
