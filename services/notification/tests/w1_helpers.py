"""Shared fakes for the W1 tests: a small Graph API behind `httpx.MockTransport`, and seeding.

Kept out of `conftest.py` (shared by every slice) and named for the slice, so it merges cleanly.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models import SCHEMA, ProviderAccount
from app.w1_models import WhatsAppNumber
from platform_core import pii

APP_ID = "1122334455"
APP_SECRET = "ci-only-meta-app-secret"  # pragma: allowlist secret
CODE = "ci-only-one-time-code"
TOKEN = "ci-only-business-token"  # pragma: allowlist secret
WABA = "9001"

W1_TABLES = ("w1_setup_runs", "w1_numbers", "w1_connection_health")


def number(
    pnid: str,
    display: str,
    *,
    quality: str = "GREEN",
    tier: str = "TIER_1K",
    status: str = "CONNECTED",
    name_status: str = "APPROVED",
    name: str = "Acme Traders",
) -> dict[str, Any]:
    return {
        "id": pnid,
        "display_phone_number": display,
        "verified_name": name,
        "name_status": name_status,
        "quality_rating": quality,
        "messaging_limit_tier": tier,
        "status": status,
    }


def template(meta_id: str, name: str) -> dict[str, Any]:
    return {
        "id": meta_id,
        "name": name,
        "language": "en",
        "status": "APPROVED",
        "category": "UTILITY",
        "components": [
            {"type": "BODY", "text": "Hello {{1}}", "example": {"body_text": [["Ann"]]}}
        ],
    }


class FakeMeta:
    """Just enough Graph for Embedded Signup. `fail` maps a route name to the HTTP status it
    answers with (`oauth`, `debug_token`, `me`, `numbers`, `number`, `subscribe`, `unsubscribe`,
    `subscribed`, `templates`, `register`). `oauth_error` and `register_error` replace those two
    routes' failure body with a Meta-shaped one (`{"status": 400, "error": {...}}`)."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.fail: dict[str, int] = {}
        self.granted: list[str] = [WABA]
        self.numbers: list[dict[str, Any]] = [
            number("1001", "+91 98765 43210"),
            number("1002", "+91 91234 56789", quality="YELLOW", tier="TIER_250"),
        ]
        self.templates: list[dict[str, Any]] = [template("t1", "order_update")]
        self.subscribed: list[str] = [APP_ID]
        #: Epoch seconds `debug_token` reports; 0 is Meta's "does not expire".
        self.token_expires_at = 0
        self.oauth_error: dict[str, Any] | None = None
        self.register_error: dict[str, Any] | None = None
        #: What `POST /{id}/register` was sent, decoded.
        self.register_bodies: list[dict[str, Any]] = []
        #: Whether a successful register flips the number to CONNECTED (Meta does, after a moment).
        self.register_connects = True

    def _refuse(self, route: str) -> httpx.Response | None:
        status = self.fail.get(route)
        if status is None:
            return None
        return httpx.Response(
            status, json={"error": {"code": 190 if status == 401 else 100, "message": "boom"}}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        parts = request.url.path.strip("/").split("/")[1:]  # drop the version segment
        method = request.method
        route, response = self._route(method, parts, request)
        return self._refuse(route) or response

    def _route(
        self, method: str, parts: list[str], request: httpx.Request
    ) -> tuple[str, httpx.Response]:
        if parts == ["oauth", "access_token"]:
            if self.oauth_error is not None:
                return "oauth-error", httpx.Response(
                    int(self.oauth_error.get("status", 400)), json=self.oauth_error
                )
            return "oauth", httpx.Response(200, json={"access_token": TOKEN})
        if parts == ["debug_token"]:
            scopes = [{"scope": "whatsapp_business_management", "target_ids": self.granted}]
            return "debug_token", httpx.Response(
                200, json={"data": {"granular_scopes": scopes, "expires_at": self.token_expires_at}}
            )
        if parts == ["me"]:
            return "me", httpx.Response(200, json={"id": "biz"})
        if len(parts) == 2 and parts[1] == "register" and method == "POST":
            self.register_bodies.append(json.loads(request.content or b"{}"))
            if self.register_error is not None:
                return "register-error", httpx.Response(
                    int(self.register_error.get("status", 400)), json=self.register_error
                )
            if self.register_connects:
                for item in self.numbers:
                    if item["id"] == parts[0]:
                        item["status"] = "CONNECTED"
                        item["platform_type"] = "CLOUD_API"
            return "register", httpx.Response(200, json={"success": True})
        if len(parts) == 1:
            found = [n for n in self.numbers if n["id"] == parts[0]]
            if not found:
                return "number", httpx.Response(404, json={"error": {"code": 100, "message": "x"}})
            return "number", httpx.Response(200, json=found[0])
        if len(parts) == 2 and parts[1] == "phone_numbers":
            return "numbers", httpx.Response(200, json={"data": self.numbers})
        if len(parts) == 2 and parts[1] == "subscribed_apps":
            if method == "POST":
                return "subscribe", httpx.Response(200, json={"success": True})
            if method == "DELETE":
                return "unsubscribe", httpx.Response(200, json={"success": True})
            data = [{"whatsapp_business_api_data": {"id": app}} for app in self.subscribed]
            return "subscribed", httpx.Response(200, json={"data": data})
        if len(parts) == 2 and parts[1] == "message_templates":
            return "templates", httpx.Response(200, json={"data": self.templates})
        return "unknown", httpx.Response(404, json={"error": {"code": 100, "message": "x"}})

    def registers(self) -> list[httpx.Request]:
        """The `POST /{phone_number_id}/register` calls, in order."""
        return [c for c in self.calls if c.url.path.endswith("/register")]

    def routes_called(self) -> list[str]:
        return ["/".join(c.url.path.strip("/").split("/")[1:]) for c in self.calls]

    def unsubscribes(self) -> list[httpx.Request]:
        """The `DELETE /{waba}/subscribed_apps` calls, in order."""
        return [c for c in self.calls if c.method == "DELETE"]


@pytest.fixture(name="signup_configured")
def configure_signup(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = settings()
    monkeypatch.setattr(cfg, "meta_app_id", APP_ID)
    monkeypatch.setattr(cfg, "meta_app_secret", SecretStr(APP_SECRET))
    monkeypatch.setattr(cfg, "meta_embedded_signup_config_id", "cfg-123")


@pytest_asyncio.fixture(autouse=True)
async def clean_w1_tables(session: AsyncSession) -> AsyncIterator[None]:
    """`conftest.TENANT_TABLES` is shared, so a slice's own tables are truncated here."""
    engine = create_async_engine(
        settings().admin_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    async with engine.begin() as conn:
        tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in W1_TABLES)
        await conn.execute(text(f"TRUNCATE {tables}"))
    await engine.dispose()
    yield


async def connect(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    waba_id: str = WABA,
    phone_number_id: str = "1001",
    token: str | None = TOKEN,
    cached: list[tuple[str, str]] | None = None,
    refreshed_at: datetime | None = None,
    quality: str = "green",
) -> None:
    """A connected account, and (optionally) its numbers cache as `(phone_number_id, display)`."""
    sealed = pii.seal(token, kind="generic", settings=settings())[0] if token else None
    when = refreshed_at or datetime.now(UTC)
    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id=waba_id,
                phone_number_id=phone_number_id,
                display_number="+919876543210",
                access_token_enc=sealed,
                status="connected",
            )
        )
        for pnid, display in cached or []:
            session.add(
                WhatsAppNumber(
                    organization_id=org_id,
                    waba_id=waba_id,
                    phone_number_id=pnid,
                    display_phone_number=display,
                    verified_name="Acme Traders",
                    name_status="approved",
                    quality_rating=quality,
                    messaging_limit="TIER_1K",
                    status="connected",
                    is_default=pnid == phone_number_id,
                    refreshed_at=when,
                )
            )
