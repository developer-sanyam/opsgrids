"""The two Graph API calls template management makes: list a WABA's templates, create one.

Deliberately small and its own — other slices talk to Graph for other reasons, and this one must
not share a failure with them. The HTTP transport is injectable (`httpx.MockTransport` in tests);
nothing here reads a global.

The Graph base is platform configuration (`Settings.whatsapp_api_base`), the same fixed base
`whatsapp_provider` and `WhatsAppCloudTransport` use, never something a caller supplies — so this
does not go through the SSRF guard that exists for tenant-chosen URLs. For the same reason **a
`paging.next` URL from Meta's response is never followed**: paging continues by passing Meta's
`after` cursor back to the same fixed base, so a response cannot steer this process to another
host with the tenant's token in hand.

The token travels in the `Authorization` header, never a query string, and no error text carries
it: `GraphError.public_message` is either Meta's own `error_user_msg` (written for end users) or a
fixed sentence, and the raised exception's chain is never formatted into a response.
"""

from __future__ import annotations

from typing import Any, Final

import httpx

from app.config import Settings
from platform_core.logging import get_logger

log = get_logger("notification.w2_graph")

#: Graph answers here are JSON; a page of a hundred templates with components is well under this.
#: Refusing more keeps a hostile or broken upstream from being read whole into memory.
_MAX_RESPONSE_BYTES: Final = 2_000_000
_PAGE_SIZE: Final = 100
#: 100 x 50 = 5000 templates, past what a WABA may hold (Meta's ceiling is in the low thousands).
_MAX_PAGES: Final = 50
_FIELDS: Final = (
    "id,name,language,status,category,components,quality_score,rejected_reason,parameter_format"
)


class GraphError(Exception):
    """Meta refused, or could not be reached. Carries what is safe to show and what to act on."""

    def __init__(
        self,
        *,
        status: int | None,
        code: int | None,
        public_message: str,
        retryable: bool,
    ) -> None:
        super().__init__(public_message)
        self.status = status
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


_UNREACHABLE: Final = "Could not reach Meta. Try again in a moment."
_UNAUTHORISED: Final = (
    "Meta refused this connection's access token. Reconnect WhatsApp and try again."
)
_GENERIC: Final = "Meta could not process this request."


def _error_from(response: httpx.Response) -> GraphError:
    status = response.status_code
    code: int | None = None
    user_message: str | None = None
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            raw_code = error.get("code")
            code = raw_code if isinstance(raw_code, int) else None
            candidate = error.get("error_user_msg")
            if isinstance(candidate, str) and candidate.strip():
                user_message = candidate.strip()[:300]
    except ValueError:
        pass
    if status in (401, 403) or code == 190:
        return GraphError(status=status, code=code, public_message=_UNAUTHORISED, retryable=False)
    retryable = status >= 500 or status == 429
    return GraphError(
        status=status,
        code=code,
        public_message=user_message or (_UNREACHABLE if retryable else _GENERIC),
        retryable=retryable,
    )


def _json_object(response: httpx.Response) -> dict[str, Any]:
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise GraphError(
            status=response.status_code, code=None, public_message=_GENERIC, retryable=False
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise GraphError(
            status=response.status_code, code=None, public_message=_GENERIC, retryable=False
        ) from exc
    if not isinstance(body, dict):
        raise GraphError(
            status=response.status_code, code=None, public_message=_GENERIC, retryable=False
        )
    return body


class TemplateGraph:
    """One WABA's template endpoints, called with that organization's own token."""

    def __init__(
        self,
        settings: Settings,
        *,
        waba_id: str,
        access_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = settings.whatsapp_api_base.rstrip("/")
        self._waba_id = waba_id
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._timeout = settings.whatsapp_timeout_seconds
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def _send(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            response = await client.request(method, url, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            # The type only: an httpx error's text can echo the URL it tried.
            log.warning("w2_graph.unreachable", error_type=type(exc).__name__)
            raise GraphError(
                status=None, code=None, public_message=_UNREACHABLE, retryable=True
            ) from exc
        if response.status_code >= 400:
            error = _error_from(response)
            log.warning("w2_graph.refused", status=error.status, code=error.code)
            raise error
        return _json_object(response)

    async def list_templates(self) -> list[dict[str, Any]]:
        """Every template the WABA holds. All pages or an error — never a silent partial list,
        because the caller treats what is missing from this list as deleted."""
        items: list[dict[str, Any]] = []
        after: str | None = None
        async with self._client() as client:
            for _ in range(_MAX_PAGES):
                params: dict[str, str | int] = {"fields": _FIELDS, "limit": _PAGE_SIZE}
                if after:
                    params["after"] = after
                body = await self._send(
                    client, "GET", f"{self._base}/{self._waba_id}/message_templates", params=params
                )
                data = body.get("data")
                if not isinstance(data, list):
                    raise GraphError(
                        status=200, code=None, public_message=_GENERIC, retryable=False
                    )
                items.extend(item for item in data if isinstance(item, dict))
                paging = body.get("paging")
                cursors = paging.get("cursors") if isinstance(paging, dict) else None
                after = cursors.get("after") if isinstance(cursors, dict) else None
                has_next = isinstance(paging, dict) and bool(paging.get("next"))
                if not (has_next and isinstance(after, str) and after):
                    return items
        raise GraphError(status=200, code=None, public_message=_GENERIC, retryable=False)

    async def create_template(
        self, *, name: str, language: str, category: str, components: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Submit one template for review. Returns Meta's `{id, status, category}`."""
        async with self._client() as client:
            body = await self._send(
                client,
                "POST",
                f"{self._base}/{self._waba_id}/message_templates",
                json={
                    "name": name,
                    "language": language,
                    "category": category.upper(),
                    "components": components,
                },
            )
        if not body.get("id"):
            raise GraphError(status=200, code=None, public_message=_GENERIC, retryable=False)
        return body
