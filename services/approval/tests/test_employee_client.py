"""`HttpHierarchySource`'s retry and timeout behaviour (ADR-0006 §2.1), against a stubbed HTTP
layer — no real employee-service, no real network. `httpx.MockTransport` intercepts every
request, so each test controls exactly which attempt succeeds and asserts exactly how many were
made.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.config import settings
from app.employee_client import (
    MAX_ATTEMPTS,
    HttpHierarchySource,
    RetryExhaustedError,
)

pytestmark = pytest.mark.integration

ORG_ID = uuid.uuid4()
EMPLOYEE_ID = uuid.uuid4()


def _page_body(next_cursor: uuid.UUID | None = None) -> dict:
    return {
        "items": [
            {
                "employee_id": str(EMPLOYEE_ID),
                "reporting_manager_id": None,
                "dotted_line_manager_id": None,
                "status": "active",
                "display_name": "Ananya Rao",
                "employee_no": "EMP0001",
                "location_code": "MUM",
                "hierarchy_version": 1,
            }
        ],
        "next_cursor": str(next_cursor) if next_cursor else None,
    }


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backoff schedule tops out at 16 s; tests assert *how many times* it slept, not how
    long — patching sleep to a no-op keeps five-attempt tests fast without weakening what they
    prove about attempt counts and status-code handling."""

    async def _instant_sleep(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.employee_client.asyncio.sleep", _instant_sleep)


def _source(handler) -> HttpHierarchySource:
    transport = httpx.MockTransport(handler)
    source = HttpHierarchySource("http://employee.test", settings())
    source._client = httpx.AsyncClient(base_url="http://employee.test", transport=transport)
    return source


class TestSuccessOnFirstAttempt:
    async def test_no_retry_when_the_first_response_is_ok(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=_page_body())

        source = _source(handler)
        page = await source.fetch_page(ORG_ID, cursor=None)

        assert calls == 1
        assert len(page.items) == 1
        assert page.items[0].employee_id == EMPLOYEE_ID
        assert page.next_cursor is None


class TestRetryOn5xx:
    async def test_a_5xx_is_retried_and_succeeds_before_exhaustion(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                return httpx.Response(503, text="upstream unhappy")
            return httpx.Response(200, json=_page_body())

        source = _source(handler)
        page = await source.fetch_page(ORG_ID, cursor=None)

        assert calls == 3
        assert len(page.items) == 1

    async def test_5xx_on_every_attempt_exhausts_retries(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500, text="still unhappy")

        source = _source(handler)
        with pytest.raises(RetryExhaustedError):
            await source.fetch_page(ORG_ID, cursor=None)

        assert calls == MAX_ATTEMPTS

    async def test_it_does_not_sleep_after_the_final_failed_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sleep after the last attempt only delays raising `RetryExhaustedError` — up to 16 s
        of backoff for a retry that is never going to happen."""
        sleep_calls = 0

        async def _counting_sleep(*_args: object, **_kwargs: object) -> None:
            nonlocal sleep_calls
            sleep_calls += 1

        monkeypatch.setattr("app.employee_client.asyncio.sleep", _counting_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="still unhappy")

        source = _source(handler)
        with pytest.raises(RetryExhaustedError):
            await source.fetch_page(ORG_ID, cursor=None)

        assert sleep_calls == MAX_ATTEMPTS - 1


class TestRetryOnConnectionAndTimeoutErrors:
    async def test_a_connection_error_is_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json=_page_body())

        source = _source(handler)
        page = await source.fetch_page(ORG_ID, cursor=None)

        assert calls == 2
        assert len(page.items) == 1

    async def test_a_timeout_is_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(200, json=_page_body())

        source = _source(handler)
        await source.fetch_page(ORG_ID, cursor=None)

        assert calls == 2


class TestA4xxIsNeverRetried:
    async def test_a_401_fails_on_the_first_attempt(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(401, json={"detail": "bad service token"})

        source = _source(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await source.fetch_page(ORG_ID, cursor=None)

        assert calls == 1, (
            "a 4xx means the audience/secret or the cursor is wrong; retrying repeats it"
        )

    async def test_a_422_malformed_cursor_fails_on_the_first_attempt(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(422, json={"detail": "malformed cursor"})

        source = _source(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await source.fetch_page(ORG_ID, cursor=uuid.uuid4())

        assert calls == 1


class TestServiceTokenMintedPerAttempt:
    async def test_a_fresh_token_is_sent_on_every_retry(self) -> None:
        tokens: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            tokens.append(request.headers["x-internal-token"])
            if len(tokens) < 3:
                return httpx.Response(503)
            return httpx.Response(200, json=_page_body())

        source = _source(handler)
        await source.fetch_page(ORG_ID, cursor=None)

        assert len(tokens) == 3
        # Each attempt mints its own token rather than reusing one across the page's retries
        # (module docstring): a `jti`-bearing JWT is different every time it is minted.
        assert len(set(tokens)) == 3
