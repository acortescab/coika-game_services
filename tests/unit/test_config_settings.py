from coika_game_service.api.core.config import Settings


def test_defaults_without_env(monkeypatch):
    monkeypatch.delenv("COIKA_REDIS_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.REDIS_URL == "redis://localhost:6379/0"
    assert settings.ENV == "dev"


def test_reads_prefixed_env_vars(monkeypatch):
    monkeypatch.setenv("COIKA_DATABASE_URL_WRITER", "postgresql+asyncpg://w/db")
    monkeypatch.setenv("COIKA_DATABASE_URL_READER", "postgresql+asyncpg://r/db")
    monkeypatch.setenv("COIKA_REDIS_URL", "redis://cache:6379/1")

    settings = Settings(_env_file=None)

    assert settings.DATABASE_URL_WRITER == "postgresql+asyncpg://w/db"
    assert settings.DATABASE_URL_READER == "postgresql+asyncpg://r/db"
    assert settings.REDIS_URL == "redis://cache:6379/1"


def test_ignores_unprefixed_env_vars(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://should-be-ignored:6379/0")
    monkeypatch.setenv("DATABASE_URL_WRITER", "postgresql+asyncpg://ignored/db")

    settings = Settings(_env_file=None)

    assert settings.REDIS_URL == "redis://localhost:6379/0"
    assert settings.DATABASE_URL_WRITER == ""


def test_reads_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COIKA_REDIS_URL=redis://from-file:6379/0\n"
        "COIKA_DATABASE_URL_WRITER=postgresql+asyncpg://file/db\n"
    )

    settings = Settings(_env_file=env_file)

    assert settings.REDIS_URL == "redis://from-file:6379/0"
    assert settings.DATABASE_URL_WRITER == "postgresql+asyncpg://file/db"


def test_redis_url_is_a_valid_url_by_default():
    # `redis.asyncio.from_url` necesita esquema; "localhost" a secas no vale.
    assert Settings(_env_file=None).REDIS_URL.startswith(("redis://", "rediss://", "unix://"))
