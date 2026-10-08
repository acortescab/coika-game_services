from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="COIKA_", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://coika:coika@localhost:5432/coika_game"
    redis_url: str = "redis://localhost:6379/0"
    auth_jwks_url: str = "http://localhost:8001/.well-known/jwks.json"
    auth_issuer: str = "coika-auth"
    auth_audience: str = "coika-game"
    jwks_cache_ttl_seconds: int = 300


settings = Settings()
