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
    #: The Facebook-scoped user id from the same `FB.login` response as the popup's code
    #: (`response.authResponse.userID`) -- Meta's own bare-digit id format, so the same pattern as
    #: the two ids above. Captured going forward so a later deauthorize or data-deletion callback
    #: naming this id can be attributed back to this connection; never verified against Meta (there
    #: is no Graph call that confirms it), so treated as a hint like the other two.
    meta_user_id: str | None = Field(default=None, pattern=_DIGITS)


class SignupCompleteOut(BaseModel):
    setup_id: uuid.UUID
    status: Literal["running"] = "running"


class SignupStepOut(BaseModel):
    key: Literal["token", "number", "webhook", "register", "templates", "verify"]
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
    #: Whether the number is registered for the Cloud API (`registered`), still waiting for
    #: registration or verification (`not_registered`), or not known. Derived from Meta's
    #: `platform_type` and `status`.
    registration: Literal["registered", "not_registered", "unknown"] = "unknown"
    #: Meta's `code_verification_status`, verbatim (`VERIFIED`, `NOT_VERIFIED`, ...), or null.
    code_verification_status: str | None = None
    #: Meta's `throughput.level` (`STANDARD`, `HIGH`, `NOT_APPLICABLE`), or null.
    throughput_level: str | None = None


class NumbersOut(BaseModel):
    items: list[NumberOut]


class WhatsAppOkOut(BaseModel):
    ok: Literal[True] = True


class DisconnectOut(WhatsAppOkOut):
    """The connection is gone either way. `detail` is present only when Meta could not be asked
    to stop sending the old account's events here, so the person can remove the app from that
    account themselves; it never carries a token."""

    detail: str | None = None


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
