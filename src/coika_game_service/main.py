import uvicorn
from fastapi import FastAPI

from coika_game_service.api import health


def create_app() -> FastAPI:
    app = FastAPI(title="Coika Game Service", version="0.1.0")
    app.include_router(health.router)
    return app


app = create_app()


def run() -> None:
    uvicorn.run("coika_game_service.main:app", host="0.0.0.0", port=8000, reload=True)
