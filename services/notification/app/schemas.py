"""Request and response contracts for the notification service.

This module is the published API surface — a change here is a contract change. Times are
ISO-8601 UTC; `id` is the stable code an operator typed (`ticket_acknowledged`) wherever one
exists, because those are the identifiers that appear in a rule and in a support conversation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

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

    `key` is our vocabulary — the dotted path a rule's payload resolves, the same name the ERP
    writes as `{{customer_name}}`. The provider's side is `position` for a positional template
    or `parameter_name` for a named one. Keeping both here is the mapping layer: the ERP speaks
    one language and nothing outside this row has to know which form Meta wants.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    component: VariableComponent = "body"
    position: int = Field(default=0, ge=0)
    button_index: int = Field(default=0, ge=0)
    kind: VariableKind = "text"
    #: Meta's label for this placeholder. Required for a named-format template, ignored for a
    #: positional one. Lowercase letters, digits and underscores — Meta's own constraint.
    parameter_name: str = Field(default="", max_length=120, pattern=r"^[a-z0-9_]*$")


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
    recipient_contact_path: str | None
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
    #: Exactly one of these three. `recipient_path` takes an address straight from the event;
    #: `recipient_literal` is a fixed address; `recipient_contact_path` names a **contact id**
    #: in the event and has the address resolved from customer-service, which is the only way
    #: to reach a customer whose number is sealed.
    recipient_path: str | None = Field(default=None, max_length=120)
    recipient_literal: str | None = Field(default=None, max_length=254)
    recipient_contact_path: str | None = Field(default=None, max_length=120)
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


# --------------------------------------------------------------------- engage sending identity
#
# The `engage` product's contract, in this module rather than a second one because both surfaces
# are served by the same process and splitting a service's published shapes across two files
# makes "what does this service return" a question with two answers. The vocabularies do not
# overlap: nothing below is reachable from a notification route.

ConnectionKind = Literal["google", "microsoft", "smtp", "platform"]
ConnectionStatus = Literal["connected", "disconnected"]
DomainStatus = Literal["pending", "verified", "failed"]
DnsRecordKind = Literal["spf", "dkim", "dmarc", "return_path", "tracking"]


class ConnectionIn(BaseModel):
    """Connect the organization's email provider.

    `platform` is in the enum although the writer refuses it. It is a choice the wizard really
    offers, and "platform-managed sending is not available yet, connect Workspace or your own
    SMTP" is a better answer than an enum error listing three values and explaining none.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ConnectionKind
    username: str = Field(min_length=1, max_length=255)
    #: Write-only, and the only field of that kind in this module. It is sealed on the way in
    #: and has no counterpart on the way out — not masked, not truncated, not counted.
    secret: str = Field(min_length=1, max_length=2000)
    #: An `smtp` connection has no submission host implied by its kind, so it must give one.
    #: A named provider (`google`, `microsoft`) derives both — and `services.create_connection`
    #: ignores this field entirely for those, rather than letting it override the derived host,
    #: because this process is about to open a socket to whatever it names. Shape only, here:
    #: this is free text because telling a literal IP or a private name from a real one needs a
    #: DNS answer this layer has no business making, so that check — and the one that matters,
    #: repeated immediately before every dial — lives in `app/dns_check.py`.
    host: str = Field(default="", max_length=255)
    #: The mail submission ports, and the only ones `app/dns_check.py` will dial regardless of
    #: what this accepts — narrowed here too so a bad port is a 422 rather than a round trip.
    port: Literal[25, 465, 587, 2525] | None = None


class ConnectionOut(BaseModel):
    """How this organization sends, minus the one value that must never leave the process.

    There is no secret here in any form. A mask, a length or a last-four would each be the
    credential with a hint attached. The shape is asserted key-for-key by a test, so adding one
    later is a red build rather than a leak nobody notices.
    """

    kind: ConnectionKind
    username: str
    host: str
    port: int
    status: ConnectionStatus
    #: What the provider accepts in a day. Shown because a campaign larger than this is refused,
    #: and the customer should learn the number before they build one.
    daily_cap: int


class WhatsAppConnectionIn(BaseModel):
    """Connect the organization's WhatsApp Cloud API account.

    `access_token` is write-only, sealed on the way in the same way an email connection's
    secret is — it has no counterpart on the way out, not masked, not truncated.
    """

    model_config = ConfigDict(extra="forbid")

    waba_id: str = Field(min_length=1, max_length=80)
    phone_number_id: str = Field(min_length=1, max_length=80)
    display_number: str = Field(default="", max_length=40)
    access_token: str = Field(min_length=1, max_length=4000)


class WhatsAppConnectionOut(BaseModel):
    """How this organization sends WhatsApp, minus the one value that must never leave the
    process. No field here is derived from the access token — no mask, no length, no hint."""

    waba_id: str
    phone_number_id: str
    display_number: str
    status: ConnectionStatus


class DomainIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=3, max_length=253)


class DnsRecordOut(BaseModel):
    """One record the customer publishes at their registrar, and whether DNS has it yet.

    The DKIM record's `value` carries the public half of the signing key. The private half has
    no field anywhere in this module: it is sealed in the row and read only when mail is signed.
    """

    kind: DnsRecordKind
    type: str
    host: str
    value: str
    #: DMARC is the one that is recommended rather than required — a domain whose security team
    #: has not settled a policy yet still sends.
    required: bool
    verified: bool


class DomainOut(BaseModel):
    id: str
    domain: str
    status: DomainStatus
    #: Returned in full on every read, because the setup screen's job is to show the customer
    #: exactly what to paste — and to show the same values it showed yesterday.
    records: list[DnsRecordOut]
    last_checked_at: datetime | None
    created_at: datetime


class SenderIn(BaseModel):
    """Add a named identity mail may go out as.

    The address is checked against the domain by the writer rather than here: whether
    `sales@company.com` belongs to a domain this organization has proven is a fact about rows,
    not about the string.
    """

    model_config = ConfigDict(extra="forbid")

    domain_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(min_length=3, max_length=254)
    reply_to: str = Field(default="", max_length=254)
    signature: str = ""
    department: str = Field(default="", max_length=80)


class SenderOut(BaseModel):
    id: str
    domain_id: str
    name: str
    address: str
    reply_to: str
    signature: str
    department: str
    #: Derived from `verified_at` rather than stored, so it cannot disagree with the one fact
    #: that decides it: a test message actually reached the provider.
    usable: bool
    verified_at: datetime | None
    created_at: datetime


class TestSendIn(BaseModel):
    """Where to send the message that opens the gate.

    The subject and body are fixed by the service. Their only job is to prove the path works,
    and a customisable test message is a send nobody reviewed going to an address of the
    caller's choosing.
    """

    model_config = ConfigDict(extra="forbid")

    to_address: str = Field(min_length=3, max_length=254)


# --------------------------------------------------------------------- engage audience

#: Why an address may not be mailed. Only `manual` is written or lifted by a person through the
#: API; the others are reported by the recipient or their mailbox provider.
SuppressionReason = Literal["unsubscribed", "bounced", "complained", "manual"]

#: The most rows one import call may carry. Big enough for a spreadsheet paste, small enough that
#: one request is one bounded transaction rather than a job.
CONTACT_IMPORT_MAX = 1000


class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    name: str = Field(default="", max_length=200)
    attributes: dict[str, Any] = Field(default_factory=dict)


class ContactPatch(BaseModel):
    """Partial update. An absent field is left alone; the address may be changed, and is held to
    the same uniqueness as on create."""

    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, min_length=3, max_length=254)
    name: str | None = Field(default=None, max_length=200)
    attributes: dict[str, Any] | None = None


class ContactOut(BaseModel):
    id: str
    #: Masked. The address is sealed at rest and no read route opens it.
    email: str
    name: str
    attributes: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ContactImportRow(BaseModel):
    """One row of a bulk import. Deliberately loose: a bad row is reported back as `invalid`
    rather than failing the other 999, so shape checks happen in the writer, per row."""

    email: str = ""
    name: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


ContactImportIn = Annotated[list[ContactImportRow], Field(max_length=CONTACT_IMPORT_MAX)]


class ContactImportResult(BaseModel):
    index: int
    status: Literal["created", "updated", "invalid"]
    id: str | None = None
    error: str | None = None


class ContactImportOut(BaseModel):
    created: int
    updated: int
    invalid: int
    results: list[ContactImportResult]


class SuppressionIn(BaseModel):
    """A manual suppression. The reason is not a field: a person adding one through the API is
    by definition `manual`, and letting them claim `complained` would forge a provider fact."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)


class SuppressionOut(BaseModel):
    id: str
    email: str
    reason: SuppressionReason
    created_at: datetime


class UnsubscribeOut(BaseModel):
    unsubscribed: bool


# --------------------------------------------------------------------- engage lists

LIST_MEMBERS_MAX = 1000


class ContactListIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class ContactListPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class ContactListOut(BaseModel):
    id: str
    name: str
    description: str
    member_count: int
    created_at: datetime
    updated_at: datetime


class ListMembersIn(BaseModel):
    """Contact ids to add or remove. Ids that are not this organization's contacts are reported
    back rather than failing the batch."""

    model_config = ConfigDict(extra="forbid")

    contact_ids: list[uuid.UUID] = Field(min_length=1, max_length=LIST_MEMBERS_MAX)


class ListMembersOut(BaseModel):
    changed: int
    unknown: list[str]


# --------------------------------------------------------------------- engage campaigns

CampaignStatus = Literal["draft", "scheduled", "sending", "sent", "cancelled"]
CampaignAudience = Literal["list", "all_sendable"]
RecipientStatus = Literal["queued", "sent", "failed", "suppressed"]


class CampaignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    sender_id: uuid.UUID
    subject: str = Field(default="", max_length=255)
    html_body: str = Field(default="", max_length=500_000)
    text_body: str = Field(default="", max_length=500_000)
    audience: CampaignAudience = "all_sendable"
    list_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None


class CampaignPatch(BaseModel):
    """Partial edit of a draft. An absent field is left alone."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    sender_id: uuid.UUID | None = None
    subject: str | None = Field(default=None, max_length=255)
    html_body: str | None = Field(default=None, max_length=500_000)
    text_body: str | None = Field(default=None, max_length=500_000)
    audience: CampaignAudience | None = None
    list_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None


class CampaignStats(BaseModel):
    recipients: int
    queued: int
    sent: int
    failed: int
    suppressed: int


class CampaignOut(BaseModel):
    id: str
    name: str
    sender_id: str
    subject: str
    html_body: str
    text_body: str
    audience: CampaignAudience
    list_id: str | None
    status: CampaignStatus
    scheduled_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    stats: CampaignStats
    created_at: datetime
    updated_at: datetime


class CampaignRecipientOut(BaseModel):
    id: str
    contact_id: str
    #: Masked. Copied sealed at expansion; no read route opens it.
    email: str
    status: RecipientStatus
    provider_message_id: str | None
    error: str
    sent_at: datetime | None
