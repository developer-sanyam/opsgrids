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
TemplateCategory = Literal["utility", "marketing", "authentication"]
ParameterFormat = Literal["positional", "named"]
TemplateStatus = Literal["draft", "pending", "approved", "rejected", "paused", "disabled"]
VariableComponent = Literal["body", "header", "button"]
VariableKind = Literal["text", "payload"]
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


class TemplateVariableIn(BaseModel):
    """One value a provider template expects.

    `key` is our vocabulary — the same name a rule's payload path resolves — while `position`
    is the provider's. Keeping both here is what lets the rest of the service speak one
    language and confines the provider's ordering to this row.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    component: VariableComponent = "body"
    position: int = Field(default=0, ge=0)
    button_index: int = Field(default=0, ge=0)
    kind: VariableKind = "text"


class TemplateVariableOut(TemplateVariableIn):
    pass


class ProviderTemplateOut(BaseModel):
    id: str
    channel: Channel
    provider_name: str
    provider_template_id: str | None
    language: str
    category: TemplateCategory
    parameter_format: ParameterFormat
    status: TemplateStatus
    status_reason: str | None
    status_checked_at: datetime | None
    variables: list[TemplateVariableOut]
    preview_text: str
    #: Whether this template may be sent right now. Only an approved one may.
    sendable: bool
    created_at: datetime
    updated_at: datetime


class ProviderTemplateIn(BaseModel):
    """Register or update a template the provider holds.

    `status` is the provider's answer, not ours, so it defaults to `draft` and is expected to be
    corrected by a sync rather than asserted here. Setting it to `approved` by hand is possible
    and is exactly how a test fixture works, but in production it is a claim about somebody
    else's system.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    channel: Channel
    provider_name: str = Field(min_length=1, max_length=200)
    language: str = Field(default="en", max_length=20)
    category: TemplateCategory = "utility"
    parameter_format: ParameterFormat = "positional"
    status: TemplateStatus = "draft"
    status_reason: str | None = Field(default=None, max_length=2000)
    variables: list[TemplateVariableIn] = Field(default_factory=list)
    preview_text: str = ""


class RuleOut(BaseModel):
    id: str
    event_type: str
    template_code: str | None
    provider_template_code: str | None
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
    channel: Channel
    #: Exactly one of these. `template_code` renders a body of ours as free text;
    #: `provider_template_code` sends wording the provider holds, which is the only thing that
    #: reaches a WhatsApp user outside the 24-hour customer service window.
    template_code: str | None = Field(default=None, max_length=80)
    provider_template_code: str | None = Field(default=None, max_length=80)
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
