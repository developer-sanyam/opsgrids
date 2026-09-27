"""The Graph API calls WhatsApp Embedded Signup makes: exchange the popup's code for a token,
discover the business's WABAs and numbers, subscribe the app to a WABA's webhooks, read a number.

Same shape and same rules as `w2_graph` (which owns the template calls and whose error type this
reuses): the HTTP transport is injectable so tests drive it with `httpx.MockTransport`; the Graph
base is platform configuration (`Settings.whatsapp_api_base`), never caller input, and a
`paging.next` URL from Meta is never followed.

**Where a secret travels.** A tenant's access token goes in the `Authorization` header, never a
query string. The one exception is the code exchange, whose query carries the vendor's app secret
and the popup's one-time code, because that is the shape Meta documents for `oauth/access_token`;
the `httpx` logger is held at WARNING by `platform_core.logging`, and no error raised from here
formats a URL or a response body into its text (`GraphError.public_message` is Meta's own
end-user message or a fixed sentence), so neither value reaches a log line or a response.
"""

from __future__ import annotations

from typing import Any, Final

import httpx

from app import w2_graph
from app.config import Settings, meta_app_secret
from app.w2_graph import GraphError
from platform_core.logging import get_logger

log = get_logger("notification.w1_graph")

__all__ = ["NUMBER_FIELDS", "CodeExpiredError", "GraphError", "MetaError", "MetaGraph"]

#: What is read for each number on a WABA (the contract's numbers list, one field per column).
#: `code_verification_status`, `platform_type` and `throughput` say whether the number is
#: registered for the Cloud API and how fast it may send.
NUMBER_FIELDS: Final = (
    "id,display_phone_number,verified_name,name_status,quality_rating,messaging_limit_tier,status,"
    "code_verification_status,platform_type,throughput"
)

#: The sentence shown when Meta refuses the popup's code as expired or spent.
CODE_EXPIRED_MESSAGE: Final = (
    "That Meta sign-in expired or was already used. Start WhatsApp setup again."
)
#: Meta's `error_subcode`s for an authorization code that is spent (36007) or expired (36009).
#: Unverified against Meta's reference (the docs page for `oauth/access_token` did not load);
#: the wording check in `_is_code_error` is the second signal.
_CODE_SUBCODES: Final = frozenset({36007, 36009})


class MetaError(GraphError):
    """A Graph refusal with the two extra fields the setup needs to classify it: Meta's
    `error_subcode` and its own `message` (developer-facing, so it is used to decide what
    happened and never shown or logged)."""

    def __init__(self, base: GraphError, *, subcode: int | None, message: str) -> None:
        super().__init__(
            status=base.status,
            code=base.code,
            public_message=base.public_message,
            retryable=base.retryable,
        )
        self.subcode = subcode
        self.meta_message = message


class CodeExpiredError(GraphError):
    """The popup's one-time code was refused as expired or already used. Meta's code lives about
    thirty seconds and works once."""

    def __init__(self, *, status: int | None) -> None:
        super().__init__(
            status=status, code=100, public_message=CODE_EXPIRED_MESSAGE, retryable=False
        )


def _error_extras(response: httpx.Response) -> tuple[int | None, str]:
    try:
        body = response.json()
    except ValueError:
        return None, ""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, ""
    subcode = error.get("error_subcode")
    message = error.get("message")
    return (
        subcode if isinstance(subcode, int) else None,
        message[:300] if isinstance(message, str) else "",
    )


def _is_code_error(error: MetaError) -> bool:
    if error.subcode in _CODE_SUBCODES:
        return True
    if error.code not in (100, 190, None):
        return False
    text = error.meta_message.lower()
    return "authorization code" in text or "verification code" in text


_MAX_NUMBERS_PAGES: Final = 10
_PAGE_SIZE: Final = 100


class MetaGraph:
    """Embedded Signup's view of Graph. One instance per operation; holds no connection."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._base = settings.whatsapp_api_base.rstrip("/")
        self._timeout = settings.whatsapp_timeout_seconds
        self._transport = transport

    # ----------------------------------------------------------------- plumbing

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def _call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, str | int] | None = None,
        json: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with self._client() as client:
                response = await client.request(
                    method, f"{self._base}/{path}", headers=headers, params=params, json=json
                )
        except httpx.HTTPError as exc:
            # The type only: an httpx error's text can echo the URL it tried.
            log.warning("w1_graph.unreachable", error_type=type(exc).__name__)
            raise GraphError(
                status=None, code=None, public_message=w2_graph._UNREACHABLE, retryable=True
            ) from exc
        if response.status_code >= 400:
            subcode, message = _error_extras(response)
            error = MetaError(w2_graph._error_from(response), subcode=subcode, message=message)
            log.warning("w1_graph.refused", status=error.status, code=error.code, subcode=subcode)
            raise error
        return w2_graph._json_object(response)

    # ----------------------------------------------------------------- token

    async def exchange_code(self, code: str) -> str:
        """The popup's one-time `code` for a business token (`oauth/access_token`)."""
        app_id = self._settings.meta_app_id
        secret = meta_app_secret(self._settings)
        if not app_id or not secret:
            raise GraphError(
                status=None,
                code=None,
                public_message="WhatsApp signup is not configured on this platform.",
                retryable=False,
            )
        try:
            body = await self._call(
                "GET",
                "oauth/access_token",
                params={"client_id": app_id, "client_secret": secret, "code": code},
            )
        except MetaError as exc:
            if _is_code_error(exc):
                raise CodeExpiredError(status=exc.status) from exc
            raise
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise GraphError(
                status=200, code=None, public_message=w2_graph._GENERIC, retryable=False
            )
        return token

    async def debug_token(self, token: str) -> dict[str, Any]:
        """What Meta knows about `token`: its scopes (`granular_scopes`, each with the WABA ids
        it was granted on) and `expires_at`. Called with the app's own credential."""
        app_id = self._settings.meta_app_id
        secret = meta_app_secret(self._settings)
        if not app_id or not secret:
            raise GraphError(
                status=None, code=None, public_message=w2_graph._GENERIC, retryable=False
            )
        body = await self._call(
            "GET",
            "debug_token",
            token=f"{app_id}|{secret}",
            params={"input_token": token},
        )
        data = body.get("data")
        return data if isinstance(data, dict) else {}

    async def token_is_valid(self, token: str) -> bool:
        """Can `token` still read anything at all? False only when Meta *refuses the token*;
        a network failure or a 5xx raises, so "Meta was down" is never recorded as "invalid"."""
        try:
            await self._call("GET", "me", token=token, params={"fields": "id"})
        except GraphError as exc:
            if exc.status in (400, 401, 403) or exc.code == 190:
                return False
            raise
        return True

    # ----------------------------------------------------------------- discovery

    @staticmethod
    def wabas_from_debug(data: dict[str, Any]) -> list[str]:
        """WABA ids the token was granted management of, in Meta's order, without repeats."""
        seen: list[str] = []
        scopes = data.get("granular_scopes")
        for scope in scopes if isinstance(scopes, list) else []:
            if not isinstance(scope, dict):
                continue
            if scope.get("scope") not in (
                "whatsapp_business_management",
                "whatsapp_business_messaging",
            ):
                continue
            for target in scope.get("target_ids") or []:
                target_id = str(target)
                if target_id and target_id not in seen:
                    seen.append(target_id)
        return seen

    async def list_numbers(self, waba_id: str, token: str) -> list[dict[str, Any]]:
        """Every phone number on the WABA, with the fields the numbers screen shows."""
        items: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(_MAX_NUMBERS_PAGES):
            params: dict[str, str | int] = {"fields": NUMBER_FIELDS, "limit": _PAGE_SIZE}
            if after:
                params["after"] = after
            body = await self._call("GET", f"{waba_id}/phone_numbers", token=token, params=params)
            data = body.get("data")
            if not isinstance(data, list):
                raise GraphError(
                    status=200, code=None, public_message=w2_graph._GENERIC, retryable=False
                )
            items.extend(item for item in data if isinstance(item, dict))
            paging = body.get("paging")
            cursors = paging.get("cursors") if isinstance(paging, dict) else None
            after = cursors.get("after") if isinstance(cursors, dict) else None
            if not (isinstance(paging, dict) and paging.get("next") and isinstance(after, str)):
                return items
        return items

    async def get_number(self, phone_number_id: str, token: str) -> dict[str, Any]:
        return await self._call(
            "GET", phone_number_id, token=token, params={"fields": NUMBER_FIELDS}
        )

    async def register_number(self, phone_number_id: str, token: str, pin: str) -> None:
        """Register the number for the Cloud API (`POST /{phone_number_id}/register`).

        The PIN travels in the JSON body, never the URL. Meta allows ten calls per number per
        72 hours (error 133016), so the caller never retries this blindly. Success is
        `{"success": true}`; a `false` is treated as a refusal."""
        body = await self._call(
            "POST",
            f"{phone_number_id}/register",
            token=token,
            json={"messaging_product": "whatsapp", "pin": pin},
        )
        if body.get("success") is False:
            raise GraphError(
                status=200, code=None, public_message=w2_graph._GENERIC, retryable=False
            )

    # ----------------------------------------------------------------- webhooks

    async def subscribe_app(self, waba_id: str, token: str) -> None:
        """Subscribe the app to the WABA's webhooks (`POST /{waba}/subscribed_apps`)."""
        body = await self._call("POST", f"{waba_id}/subscribed_apps", token=token)
        if body.get("success") is False:
            raise GraphError(
                status=200, code=None, public_message=w2_graph._GENERIC, retryable=False
            )

    async def unsubscribe_app(self, waba_id: str, token: str) -> None:
        """Stop the WABA's webhooks reaching the app (`DELETE /{waba}/subscribed_apps`), so a
        WABA an organization has left no longer delivers messages here."""
        body = await self._call("DELETE", f"{waba_id}/subscribed_apps", token=token)
        if body.get("success") is False:
            raise GraphError(
                status=200, code=None, public_message=w2_graph._GENERIC, retryable=False
            )

    async def subscribed_app_ids(self, waba_id: str, token: str) -> list[str]:
        """The ids of the apps currently subscribed to the WABA."""
        body = await self._call("GET", f"{waba_id}/subscribed_apps", token=token)
        data = body.get("data")
        ids: list[str] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            app = item.get("whatsapp_business_api_data")
            raw = app.get("id") if isinstance(app, dict) else item.get("id")
            if raw:
                ids.append(str(raw))
        return ids
