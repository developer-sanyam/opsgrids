"""Request and response contracts for the notification service.

This module is the published API surface — a change here is a contract change. Times are
ISO-8601 UTC; `id` is the stable code an operator typed (`ticket_acknowledged`) wherever one
exists, because those are the identifiers that appear in a rule and in a support conversation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Channel = Literal["email", "whatsapp"]
MessageState = Literal["pending", "sent", "failed", "suppressed"]
Outcome = Literal["sent", "failed"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- templates


class TemplateOut(BaseModel):
    id: str
    channel: Channel
    subject: str | None
    body: str
    description: str
    #: Every `{{placeholder}}` the body and subject refer to — what an event must supply for
    #: this template to render. Derived, so an operator never has to read the body to find out.
    placeholders: list[str]
    created_at: datetime
    updated_at: datetime


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    channel: Channel
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1)
    description: str = ""


# --------------------------------------------------------------------- rules


class RuleOut(BaseModel):
    id: str
    event_type: str
    template_code: str
    channel: Channel
    category: str
    recipient_path: str | None
    recipient_literal: str | None
    dedupe_key_path: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class RuleIn(BaseModel):
    """Author one automation.

    `recipient_path` or `recipient_literal` — exactly one. The coherence rules live in
    `domain.validate_rule` so the same check guards the API and the consumer.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    event_type: str = Field(min_length=1, max_length=120)
    template_code: str = Field(min_length=1, max_length=80)
    channel: Channel
    category: str = Field(default="operational", max_length=40)
    recipient_path: str | None = Field(default=None, max_length=120)
    recipient_literal: str | None = Field(default=None, max_length=254)
    dedupe_key_path: str | None = Field(default=None, max_length=120)
    active: bool = True


# --------------------------------------------------------------------- messages


class DeliveryOut(BaseModel):
    attempt_no: int
    provider: str
    outcome: Outcome
    detail: str | None
    at: datetime


class MessageOut(BaseModel):
    id: str
    channel: Channel
    category: str
    to_address: str
    subject: str | None
    body: str
    state: MessageState
    rule_code: str | None
    event_type: str | None
    attempts: int
    last_error: str | None
    sent_at: datetime | None
    provider_message_id: str | None
    created_at: datetime
    deliveries: list[DeliveryOut]


class SendIn(BaseModel):
    """Send one message now, outside any rule.

    The direct path exists because not everything worth sending is a reaction to an event — a
    one-off notice to a customer is a legitimate thing for an operator to do. It renders through
    the same template and obeys the same suppression, so the direct path cannot be used to
    reach someone who opted out.
    """

    model_config = ConfigDict(extra="forbid")

    template_code: str = Field(min_length=1, max_length=80)
    channel: Channel
    to_address: str = Field(min_length=1, max_length=254)
    category: str = Field(default="operational", max_length=40)
    #: Values the template's placeholders resolve against.
    context: dict[str, object] = Field(default_factory=dict)


# --------------------------------------------------------------------- preferences


class PreferenceOut(BaseModel):
    recipient: str
    channel: Channel
    category: str
    suppressed: bool
    reason: str | None
    updated_at: datetime


class PreferenceIn(BaseModel):
    """Record or lift a refusal. `category` of `*` covers the whole channel."""

    model_config = ConfigDict(extra="forbid")

    recipient: str = Field(min_length=1, max_length=254)
    channel: Channel
    category: str = Field(default="*", max_length=40)
    suppressed: bool = True
    reason: str | None = Field(default=None, max_length=200)
