"""Structured logging with request correlation.

One request crossing five services must be one searchable thing. `X-Correlation-ID` is
minted at the edge if absent and threaded through every log line and every event envelope.
"""

from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)
actor_id_ctx: ContextVar[str | None] = ContextVar("actor_id", default=None)
org_id_ctx: ContextVar[str | None] = ContextVar("org_id", default=None)

QUIET_PATHS = frozenset({"/health", "/health/ready", "/metrics", "/.well-known/jwks.json"})


def _inject_context(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key, var in (
        ("request_id", request_id_ctx),
        ("correlation_id", correlation_id_ctx),
        ("actor_id", actor_id_ctx),
        ("org_id", org_id_ctx),
    ):
        value = var.get()
        if value is not None:
            event.setdefault(key, value)
    return event


def configure_logging(service_name: str, *, level: str = "INFO", json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    for noisy in ("uvicorn.access", "aiormq", "aio_pika", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _inject_context,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service_name)


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Mint or propagate request and correlation ids, and log completion."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        # A correlation id spans the whole causal chain; a request id is one hop.
        correlation_id = request.headers.get("X-Correlation-ID") or request_id

        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        rid = request_id_ctx.set(request_id)
        cid = correlation_id_ctx.set(correlation_id)
        log = get_logger("http")
        status: int | None = None
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            if request.url.path not in QUIET_PATHS:
                log.info(
                    "request.completed",
                    method=request.method,
                    path=request.url.path,
                    status=status,
                )
            request_id_ctx.reset(rid)
            correlation_id_ctx.reset(cid)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Correlation-ID"] = correlation_id
        return response
