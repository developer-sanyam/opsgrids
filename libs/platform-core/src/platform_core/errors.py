"""RFC 7807 problem+json — one error shape for the whole platform.

Every service, every gateway and every client parses the same body. The `request_id` is
carried through so a user-visible failure can be tied to a log line without asking them to
reproduce it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

CONTENT_TYPE = "application/problem+json"

#: Overridden per deployment so the `type` URI resolves to real documentation.
PROBLEM_BASE = "https://errors.opsgrids.dev"


class ProblemError(Exception):
    """Base for every deliberate failure. Subclasses set status and type."""

    status_code: int = 400
    problem_type: str = "bad-request"
    title: str = "Bad request"

    def __init__(
        self,
        detail: str,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors = errors
        self.headers = headers or {}

    def to_problem(self, request: Request) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"{PROBLEM_BASE}/{self.problem_type}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "instance": request.url.path,
            "request_id": getattr(request.state, "request_id", None),
        }
        if self.errors:
            body["errors"] = self.errors
        return body


class ValidationProblem(ProblemError):
    status_code = 422
    problem_type = "validation"
    title = "Validation failed"


class NotFoundProblem(ProblemError):
    status_code = 404
    problem_type = "not-found"
    title = "Not found"


class ConflictProblem(ProblemError):
    status_code = 409
    problem_type = "conflict"
    title = "Conflict"


class UnauthorizedProblem(ProblemError):
    status_code = 401
    problem_type = "unauthorized"
    title = "Authentication required"


class ForbiddenProblem(ProblemError):
    status_code = 403
    problem_type = "forbidden"
    title = "Not permitted"


class PaymentRequiredProblem(ProblemError):
    """The organization has not activated (or is no longer paying for) this product.

    Deliberately distinct from 403: the caller's *permissions* are fine, the
    *subscription* is not, and the client should offer an upgrade rather than an error.
    """

    status_code = 402
    problem_type = "product-not-entitled"
    title = "Product not available on this plan"


class BusinessRuleProblem(ProblemError):
    """Well-formed, authorized, and still not allowed — a domain rule refused it."""

    status_code = 422
    problem_type = "business-rule"
    title = "Business rule violation"


class RateLimitedProblem(ProblemError):
    status_code = 429
    problem_type = "rate-limited"
    title = "Too many requests"


class UpstreamUnavailableProblem(ProblemError):
    status_code = 503
    problem_type = "upstream-unavailable"
    title = "Service unavailable"


def _json(problem: dict[str, Any], headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=int(problem["status"]),
        content=problem,
        media_type=CONTENT_TYPE,
        headers=headers or None,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Install the four handlers every service needs."""

    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return _json(exc.to_problem(request), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                # Drop the "body"/"query" prefix — the client cares about the field.
                "field": ".".join(str(p) for p in err["loc"] if p not in ("body", "query")),
                "code": err["type"],
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        problem = ValidationProblem("Request payload failed validation", errors=errors)
        return _json(problem.to_problem(request))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        problem = ProblemError(str(exc.detail))
        problem.status_code = exc.status_code
        problem.problem_type = "http-error"
        problem.title = "Request failed"
        return _json(problem.to_problem(request))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        # A unique or check constraint fired. The constraint name is deliberately not
        # echoed — it leaks schema detail to whoever is probing.
        problem = ConflictProblem("That conflicts with something that already exists")
        return _json(problem.to_problem(request))
