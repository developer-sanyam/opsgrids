"""Pins the FastAPI behaviour `tests/infra/test_session_dependency_scope.py` relies on.

Under a real server, a `yield` dependency's exit code runs after the response has been sent
unless the dependency is declared `scope="function"`. `httpx.ASGITransport` cannot show this —
it awaits the whole application call — so this starts uvicorn on a free port. If a FastAPI
upgrade changes either half, this fails and the scope rule needs revisiting.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import uvicorn
from fastapi import Depends, FastAPI

EXIT_DELAY = 0.4
EXITED_AT: dict[str, float] = {}


async def request_scoped() -> AsyncIterator[str]:
    yield "request"
    await asyncio.sleep(EXIT_DELAY)
    EXITED_AT["request"] = time.monotonic()


async def function_scoped() -> AsyncIterator[str]:
    yield "function"
    await asyncio.sleep(EXIT_DELAY)
    EXITED_AT["function"] = time.monotonic()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_only_function_scope_finishes_exit_code_before_the_response() -> None:
    EXITED_AT.clear()
    app = FastAPI()

    @app.get("/request")
    async def via_request(value: Annotated[str, Depends(request_scoped)]) -> dict[str, str]:
        return {"value": value}

    @app.get("/function")
    async def via_function(
        value: Annotated[str, Depends(function_scoped, scope="function")],
    ) -> dict[str, str]:
        return {"value": value}

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    serving = asyncio.create_task(server.serve())
    try:
        while not server.started:  # noqa: ASYNC110 - uvicorn exposes a flag, not an event
            await asyncio.sleep(0.01)
        received_at: dict[str, float] = {}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for label in ("request", "function"):
                response = await client.get(f"/{label}")
                assert response.status_code == 200
                received_at[label] = time.monotonic()
            await asyncio.sleep(EXIT_DELAY * 2)
    finally:
        server.should_exit = True
        await serving

    assert EXITED_AT["request"] > received_at["request"], (
        "default-scoped exit code now finishes before the response — FastAPI changed; the "
        "scope rule may be relaxable"
    )
    assert EXITED_AT["function"] < received_at["function"], (
        "scope='function' no longer runs exit code before the response — every tenant write "
        "is reported before it commits"
    )
