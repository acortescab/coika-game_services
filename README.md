# Coika Game Service

Matches, scores and leaderboards API for Coika.

## Goals

- **Fully async FastAPI**, SQLAlchemy 2.0 async with asyncpg, separate writer and reader database connections.
- **Auth**: validates the auth service's JWTs through its JWKS, with key caching and rotation by `kid`.
- **Idempotent by design**: creating a match requires an `Idempotency-Key`, and a match has at most one score (enforced in the database). Retrying either request returns the original response.
- **One open match per player** (enforced by a partial unique index in PostgreSQL). Starting a new match abandons the unfinished one.
- **Daily mode**: every player gets the same seed on a given UTC date (`yyyyMMdd`).
- **Load tests** with Locust (see [Load test results](#load-test-results)).

Planned, not implemented yet: Redis leaderboards (sorted sets, with PostgreSQL as the source of truth), a per-player rate limit on score submission, cursor pagination and cache invalidation.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) + asyncpg · Alembic · Redis · PostgreSQL · uv · Locust

## Getting started

Requirements: [uv](https://docs.astral.sh/uv/) and Docker.

```bash
cp .env.example .env        # then replace the data_here values
docker compose up -d --build   # PostgreSQL + Redis + API (runs migrations on start)
```

The API listens on http://localhost:8001 (interactive docs at `/docs`).

To run the API outside Docker instead, start only the databases and use uv:

```bash
docker compose up -d postgres redis
uv sync
uv run alembic upgrade head
uv run coika-game-service
```

The API needs the [auth service](#configuration) running to validate tokens (by default at http://localhost:8000).

## Configuration

Settings are read from environment variables prefixed with `COIKA_` (or from `.env`). See `.env.example`.

| Variable | Description | Default |
|----------|-------------|---------|
| `COIKA_ENV` | Environment name | `dev` |
| `COIKA_DATABASE_URL_WRITER` | PostgreSQL URL for writes | |
| `COIKA_DATABASE_URL_READER` | PostgreSQL URL for reads | |
| `COIKA_REDIS_URL` | Redis URL | `redis://localhost:6379/0` |
| `COIKA_AUTH_JWKS_URL` | Auth service JWKS endpoint | `http://localhost:8000/.well-known/jwks.json` |
| `COIKA_AUTH_ISSUER` | Expected token issuer | `coika-auth` |
| `COIKA_AUTH_AUDIENCE` | Expected token audience | `coika-game` |
| `COIKA_JWKS_CACHE_TTL_SECONDS` | How long the JWKS stays cached | `300` |
| `COIKA_AUTH_PLAYERS_URL` | Auth service player lookup (names and avatars) | `http://localhost:8000/v0/players/lookup` |
| `COIKA_PLAYER_NAME_CACHE_TTL_SECONDS` | How long player names stay cached | `300` |

`COIKA_DATABASE_USER`, `COIKA_DATABASE_PASSWORD` and `COIKA_DATABASE_DB` are only used by `docker-compose.yml` to create the database and build the URLs of the `api` container.

## API

All endpoints except `/health*` require `Authorization: Bearer <JWT>` issued by the auth service.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/health/ready` | Readiness check: reader DB, writer DB and Redis (503 if any fails) |
| `POST` | `/matches` | Start a match |
| `POST` | `/matches/{match_id}/score` | Submit the score of a match |

### `POST /matches`

Header `Idempotency-Key: <uuid>` (required). Body:

```json
{ "game_mode_id": "af6e8f8c-cab7-4e4d-8ca5-5c564eed4728" }
```

Responds `201` with `{ "match_id", "status", "seed" }`. Retrying with the same key returns the same response without creating another match. `seed` is `null` except in the daily mode.

Game modes (fixed ids, identical in every environment):

| Mode | Id |
|------|----|
| `classic` | `af6e8f8c-cab7-4e4d-8ca5-5c564eed4728` |
| `daily` | `299a2835-4881-4f0c-98f4-0fceda03acca` |

Errors: `401` invalid token, `409` the key was already used for a different game mode, `422` unknown game mode or invalid body, `503` auth service unavailable.

### `POST /matches/{match_id}/score`

```json
{ "score": 1200, "pieces_dropped": 85, "highest_tier": 6 }
```

Responds `201` with `{ "match_id", "score", "pieces_dropped", "highest_tier", "created_at" }` and finishes the match in the same transaction. The match duration is not sent by the client; the server derives it from the match's `started_at`. Sending the same figures again returns the stored score.

Errors: `401` invalid token, `404` no such match or it belongs to another player, `409` the match already has a score with different figures, or the match is not in progress (abandoned or rejected), `422` invalid body.

## Development

```bash
uv run pytest               # tests (integration tests are skipped without PostgreSQL and Redis)
uv run ruff check .         # lint
uv run ruff format .        # format
```

New migrations:

```bash
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

CI (`.github/workflows/ci.yml`) runs on pull requests to `main`: lint, starts the compose stack, smoke-tests `/health/ready` and runs the full test suite.

## Project structure

```
src/coika_game_service/
  main.py              # app factory, error handlers, lifespan (DB, Redis, HTTP client, JWKS)
  api/
    core/              # settings, JWT validation, JWKS cache, game modes
    clients/           # auth service client
    db/                # SQLAlchemy base, models, session dependencies
    repositories/      # database access (flush only; services commit)
    routes/            # health, matches, scores
    schemas/           # request/response models
    services/          # business logic (matches, scores, auth, player names)
alembic/versions/      # migrations
scripts/alembic.sh     # runs migrations on container start
tests/                 # unit, integration (real PostgreSQL + Redis) and e2e
loadtests/             # Locust scenarios
```

## Load test results

```bash
uv run locust -f loadtests/locustfile.py --host http://localhost:8001
```

_To be completed:_

| Scenario | Users | RPS | p95 (ms) | Errors |
|----------|-------|-----|----------|--------|
| _to measure_ | | | | |

**Bottleneck found:** _to document._

## License

[MIT](LICENSE)
