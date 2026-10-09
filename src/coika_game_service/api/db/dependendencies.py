from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_writer_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_writer() as session:
        yield session

async def get_reader_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_reader() as session:
        yield session

def get_redis(request: Request):
    return request.app.state.redis