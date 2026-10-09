# Coika Game Service

API de partidas, puntuaciones y leaderboards para Coika.

## Objetivos

- **FastAPI totalmente async**, SQLAlchemy 2.0 async con asyncpg.
- **Auth**: validación de los JWT del auth service mediante su JWKS, con caché de claves y rotación por `kid`.
- **Leaderboards** en Redis (sorted sets); PostgreSQL es la fuente de verdad.
- **Envío de puntuaciones** idempotente por diseño (una puntuación por partida, garantizada en base de datos) y con protección básica contra trampas: validación del lado servidor y rate limit por jugador en Redis.
- **Paginación por cursor** y caché con invalidación explícita.
- **Tests de carga** con Locust (ver [Resultados de carga](#resultados-de-carga)).

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) + asyncpg · Alembic · Redis · PostgreSQL · uv · Locust

## Puesta en marcha

Requisitos: [uv](https://docs.astral.sh/uv/) y Docker.

```bash
cp .env.example .env
docker compose up -d        # PostgreSQL + Redis
uv sync                     # instala dependencias
uv run coika-game-service   # API en http://localhost:8001 (docs en /docs)
```

## Desarrollo

```bash
uv run pytest               # tests
uv run ruff check .         # lint
uv run ruff format .        # formato
```

## Estructura

```
src/coika_game_service/
  main.py        # app factory y arranque
  config.py      # settings (variables COIKA_*)
  api/           # routers
tests/
loadtests/       # locustfile
```

## Resultados de carga

```bash
uv run locust -f loadtests/locustfile.py --host http://localhost:8001
```

_Pendiente de completar:_

| Escenario | Usuarios | RPS | p95 (ms) | Errores |
|-----------|----------|-----|----------|---------|
| _por medir_ | | | | |

**Cuello de botella encontrado:** _por documentar._

## Licencia

[MIT](LICENSE)
