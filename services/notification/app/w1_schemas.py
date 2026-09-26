"""Pydantic contracts for slice W1 (WhatsApp Embedded Signup, numbers, health).

Requests refuse unknown fields (422). Responses match the sprint's API contract field for field;
none carries a token, a sealed value or a code.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

_DIGITS = r"^\d{1,30}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignupConfigOut(BaseModel):
    enabled: bool
    app_id: str | None
    config_id: str | None
    graph_version: str
    reason: Literal["not_configured"] | None = None

    @model_serializer(mode="wrap")
    def _drop_absent_reason(self, handler: Any) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("reason") is None:
            data.pop("reason", None)
        return data


class SignupCompleteIn(_Strict):
    #: Meta's one-time authorization code. Single use, short lived; sealed at rest until exchanged.
    code: str = Field(min_length=1, max_length=2048)
    waba_id: str | None = Field(default=None, pattern=_DIGITS)
    phone_number_id: str | None = Field(default=None, pattern=_DIGITS)


class SignupCompleteOut(BaseModel):
    setup_id: uuid.UUID
    status: Literal["running"] = "running"


class SignupStepOut(BaseModel):
    key: Literal["token", "number", "webhook", "templates", "verify"]
    label: str
    state: Literal["waiting", "running", "done", "failed"]
    detail: str | None = None


class SignupStatusOut(BaseModel):
    setup_id: uuid.UUID
    status: Literal["running", "done", "failed"]
    steps: list[SignupStepOut]
    error_code: str | None = None


class NumberOut(BaseModel):
    id: uuid.UUID
    phone_number_id: str
    #: Masked, like every address on the wire.
    display_phone_number: str
    verified_name: str | None
    name_status: Literal["approved", "pending_review", "declined", "none"]
    quality_rating: Literal["green", "yellow", "red", "unknown"]
    messaging_limit: Literal["TIER_250", "TIER_1K", "TIER_10K", "TIER_100K", "UNLIMITED", "unknown"]
    status: Literal["connected", "pending_verification", "disconnected"]
    is_default: bool


class NumbersOut(BaseModel):
    items: list[NumberOut]


class OkOut(BaseModel):
    ok: Literal[True] = True


class TokenHealthOut(BaseModel):
    state: Literal["valid", "expiring", "invalid", "unknown"]
    checked_at: str | None


class WebhookHealthOut(BaseModel):
    state: Literal["receiving", "silent", "unverified"]
    last_event_at: str | None


class TemplatesHealthOut(BaseModel):
    count: int
    synced_at: str | None


class NumberHealthOut(BaseModel):
    quality_rating: str
    messaging_limit: str


class HealthOut(BaseModel):
    connected: bool
    token: TokenHealthOut
    webhook: WebhookHealthOut
    templates: TemplatesHealthOut
    number: NumberHealthOut | None


class SendTestMessageIn(_Strict):
    #: E.164.
    to: str = Field(pattern=r"^\+[1-9]\d{6,14}$")
    template_name: str = Field(pattern=r"^[a-z0-9_]{1,512}$")
    language: str = Field(pattern=r"^[a-z]{2,3}(_[A-Za-z]{2,4})?$")


class SendTestMessageOut(BaseModel):
    message_id: uuid.UUID
