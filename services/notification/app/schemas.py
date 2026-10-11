"""Request and response contracts for the notification service.

This module is the published API surface — a change here is a contract change. Times are
ISO-8601 UTC; `id` is the stable code an operator typed (`ticket_acknowledged`) wherever one
exists, because those are the identifiers that appear in a rule and in a support conversation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Channel = Literal["email", "whatsapp", "in_app"]
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
    #: Why a `suppressed` message was not sent, e.g. `consent:no_opt_in`. Null otherwise.
    suppression_reason: str | None = None
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
#: Return-path and tracking are gone from this vocabulary along with the OpsGrids mail
#: infrastructure that would have carried them — nothing on this platform processes a bounce
#: envelope or rewrites a link to count a click. See `app/domain.py`'s sending-identity section.
DnsRecordKind = Literal["spf", "dkim", "dmarc"]
#: Whether DNS satisfies one record. `multiple` is SPF-specific (a domain may publish only one);
#: `mismatch` is something published at the right name that does not satisfy the requirement —
#: distinct from `missing` (nothing published at all) because a customer who pasted the wrong
#: value needs to know a value arrived.
DnsRecordStatus = Literal["verified", "missing", "multiple", "mismatch"]


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

    # Meta's own ids are bare digit strings. The pattern rejects anything else at the boundary —
    # a stray path segment, a URL, a Graph API query fragment — before it ever reaches a Graph API
    # call or a database row, rather than trusting length alone.
    waba_id: str = Field(min_length=1, max_length=30, pattern=r"^\d{1,30}$")
    phone_number_id: str = Field(min_length=1, max_length=30, pattern=r"^\d{1,30}$")
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

    #: Normalised by `dns_check.normalize_domain_name` before anything else sees it — a pasted
    #: URL, a trailing dot or slash, and mixed case are all accepted and canonicalised, so the
    #: length bound here is generous rather than exact.
    domain: str = Field(min_length=1, max_length=2048)


class DomainPatch(BaseModel):
    """Edit the two things a customer names for themselves. An absent field is left alone; a
    change to either resets the domain to `pending` — see `services.update_domain_settings`."""

    model_config = ConfigDict(extra="forbid")

    #: The DKIM selector this domain's records are published at. Read by `google` and `smtp`
    #: connections; ignored by `microsoft`, whose two selectors are fixed names
    #: (`selector1`/`selector2`) Microsoft itself chooses.
    dkim_selector: str | None = Field(default=None, min_length=1, max_length=63)
    #: An SMTP provider's SPF host — `amazonses.com`, `sendgrid.net` — validated as a bare
    #: hostname. Meaningless for `google`/`microsoft`, whose SPF target is derived from the
    #: connection kind alone.
    spf_include: str | None = Field(default=None, min_length=1, max_length=255)


class DnsRecordOut(BaseModel):
    """One record the customer publishes at their registrar, and whether DNS has it yet.

    `fqdn` is the full host DNS is actually asked about; `registrar_name` is the same record
    named the way a registrar's panel wants it — `@` for the apex, `google._domainkey` for a
    subdomain — because pasting `google._domainkey.company.com.company.com` into a panel that
    already appends the zone is the single most common way a customer publishes a record that
    can never verify. Neither the exact DKIM value nor a Microsoft CNAME's target is ever known
    here — the provider mints the key, not this platform — so `value` for those is instructional
    text, and there is nothing for `suggested_value` to say beyond SPF's own arithmetic.
    """

    kind: DnsRecordKind
    type: str
    fqdn: str
    registrar_name: str
    value: str
    #: Always populated for SPF; null everywhere else, where there is nothing to suggest beyond
    #: the provider's own instructions.
    suggested_value: str | None
    #: DMARC is the one that is recommended rather than required — a domain whose security team
    #: has not settled a policy yet still sends.
    required: bool
    status: DnsRecordStatus
    instruction: str


class DomainOut(BaseModel):
    id: str
    domain: str
    status: DomainStatus
    #: `google`, `microsoft`, `smtp`, or null when the organization has not connected a provider
    #: yet — in which case `records` is empty and the setup screen's job is to say so, not to show
    #: a record set derived from a provider nobody has chosen.
    connection_kind: ConnectionKind | None
    dkim_selector: str
    spf_include: str | None
    #: The records this domain needs *right now*, given its connection — regenerated on every
    #: read from the organization's current connection, not a snapshot frozen at creation. Empty
    #: until a provider is connected.
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


# --------------------------------------------------------------------- engage consent ledger

ConsentChannel = Literal["whatsapp", "email", "sms"]
ConsentPurpose = Literal["marketing", "transactional"]
ConsentStatus = Literal["opted_in", "opted_out"]
#: What a person may claim. The sources this service writes itself (`inbound_keyword`,
#: `unsubscribe_link`, `bounce`, `complaint`, `legacy_import`) are not writable — the same reason
#: `SuppressionIn` has no `reason` — but they do appear in what is read back, so the read side
#: types `source` as a plain string.
ConsentApiSource = Literal["checkout", "web_form", "import", "admin", "api"]


class ConsentEventIn(BaseModel):
    """Record that somebody opted in or out. `source` and `evidence` are required: a bare "yes" is
    exactly what the ledger exists to replace.

    Name the subject by `address` or, for an email contact, by `contact_id` (the address is then
    taken from the contact, sealed, and never returned). `evidence` is a short reference —
    "checkout order 1042", "signup form, 2026-09-20" — never the address itself; a request whose
    evidence contains the address is refused.
    """

    model_config = ConfigDict(extra="forbid")

    channel: ConsentChannel
    purpose: ConsentPurpose
    status: ConsentStatus
    source: ConsentApiSource
    evidence: str = Field(min_length=3, max_length=500)
    address: str | None = Field(default=None, min_length=3, max_length=254)
    contact_id: uuid.UUID | None = None
    #: When the person actually consented or refused. Defaults to now; may not be in the future.
    occurred_at: AwareDatetime | None = None
    #: Makes a retry return the original event instead of writing a second one.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)

    @model_validator(mode="after")
    def _needs_a_subject(self) -> ConsentEventIn:
        if self.address is None and self.contact_id is None:
            raise ValueError("Give an address or a contact_id")
        return self


class ConsentLookupIn(BaseModel):
    """A subject's history, looked up by address. A body rather than a query string so the address
    never lands in an access log."""

    model_config = ConfigDict(extra="forbid")

    channel: ConsentChannel
    address: str = Field(min_length=3, max_length=254)


class ConsentDecisionOut(BaseModel):
    """What the consent decision says right now. `reason` is a stable machine-readable code:
    `opted_in`, `no_opt_out`, `opted_out`, `no_opt_in`, `preference_suppressed`,
    `suppressed_<reason>`, `invalid_address`."""

    allowed: bool
    reason: str


class ConsentEventOut(BaseModel):
    id: str
    channel: ConsentChannel
    purpose: ConsentPurpose
    status: ConsentStatus
    #: One of the API sources, or one this service writes itself (`inbound_keyword`,
    #: `unsubscribe_link`, `bounce`, `complaint`, `legacy_import`). `legacy_import` is **unverified
    #: consent** carried over from before the ledger existed.
    source: str
    evidence: str | None
    #: Masked. The address is sealed at rest; `POST .../{id}:unmask` is the audited way to open it.
    address: str
    contact_id: str | None
    recorded_by: str | None
    occurred_at: datetime
    created_at: datetime


class ConsentRecordedOut(BaseModel):
    """The event, and what the decision says now that it is in. An opt-in can be recorded for an
    address a legacy suppression or a customer's own STOP still refuses — `effective` is how the
    caller sees that it has not taken effect."""

    event: ConsentEventOut
    effective: ConsentDecisionOut


class ConsentUnmaskedOut(BaseModel):
    event_id: str
    address: str


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

CampaignStatus = Literal["draft", "scheduled", "sending", "paused", "sent", "cancelled"]
CampaignAudience = Literal["list", "all_sendable"]
RecipientStatus = Literal["queued", "sent", "failed", "suppressed"]


def _reject_header_injection(value: str) -> str:
    """A `\\r` or `\\n` in a subject is not a longer subject — it is the start of a second mail
    header an attacker controls (bcc, another `Subject`, a `Content-Type`). Refused at the
    boundary rather than stripped, so the caller sees why rather than silently getting a
    different subject than the one they asked for; every recipient of a campaign carries the
    same subject line, so letting one through would poison the whole send."""
    if "\r" in value or "\n" in value:
        raise ValueError("must not contain a carriage return or line feed")
    return value


#: What picks an A/B test's winner: the share of each version's delivered recipients who opened,
#: or who clicked.
AbMetric = Literal["open", "click"]


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
    #: Off by default. On rewrites this campaign's links / adds a pixel through signed tracking
    #: URLs on the platform's domain (slice E1).
    track_opens: bool = False
    track_clicks: bool = False
    #: A/B test: version B of the subject and body, the share of the audience each version is
    #: tested on, the metric that picks the winner and how long to wait before picking it.
    ab_test: bool = False
    subject_b: str = Field(default="", max_length=255)
    html_body_b: str = Field(default="", max_length=500_000)
    text_body_b: str = Field(default="", max_length=500_000)
    ab_split_percent: int = Field(default=10, ge=1, le=50)
    ab_metric: AbMetric = "open"
    ab_wait_minutes: int = Field(default=240, ge=15, le=10080)

    @field_validator("subject", "subject_b")
    @classmethod
    def _subject_no_crlf(cls, value: str) -> str:
        return _reject_header_injection(value)


def _reject_naive_datetime(value: datetime) -> datetime:
    """Boundary validation for anything that becomes a scheduler `due_at` (ADR-0015): a naive
    value would be interpreted against whatever timezone the database session happens to be in,
    silently sending at the wrong instant, so it is refused here rather than assumed to be UTC."""
    if value.tzinfo is None:
        raise ValueError("must include a timezone offset")
    return value


class CampaignScheduleIn(BaseModel):
    """`POST /engage/campaigns/{id}/schedule`. Also how an already-`scheduled` campaign is
    rescheduled — same endpoint, same shape, a new instant."""

    model_config = ConfigDict(extra="forbid")

    scheduled_at: datetime

    @field_validator("scheduled_at")
    @classmethod
    def _scheduled_at_aware(cls, value: datetime) -> datetime:
        return _reject_naive_datetime(value)


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
    track_opens: bool | None = None
    track_clicks: bool | None = None
    ab_test: bool | None = None
    subject_b: str | None = Field(default=None, max_length=255)
    html_body_b: str | None = Field(default=None, max_length=500_000)
    text_body_b: str | None = Field(default=None, max_length=500_000)
    ab_split_percent: int | None = Field(default=None, ge=1, le=50)
    ab_metric: AbMetric | None = None
    ab_wait_minutes: int | None = Field(default=None, ge=15, le=10080)

    @field_validator("subject", "subject_b")
    @classmethod
    def _subject_no_crlf(cls, value: str | None) -> str | None:
        return value if value is None else _reject_header_injection(value)


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
    #: Why the send driver paused this campaign (no connected provider, no usable sender).
    #: Empty unless `status == "paused"`.
    pause_reason: str
    stats: CampaignStats
    #: Whether opens / clicks are tracked for this campaign (off by default).
    track_opens: bool = False
    track_clicks: bool = False
    #: A/B test settings; `ab_winner` (`a` | `b`) and `ab_decided_at` are set by the service once
    #: the wait is over, null until then.
    ab_test: bool = False
    subject_b: str = ""
    html_body_b: str = ""
    text_body_b: str = ""
    ab_split_percent: int = 10
    ab_metric: AbMetric = "open"
    ab_wait_minutes: int = 240
    ab_winner: Literal["a", "b"] | None = None
    ab_decided_at: datetime | None = None
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


class CampaignTestSendOut(BaseModel):
    """What `POST /engage/campaigns/{id}/test` did. Not the campaign — a test send changes
    nothing about it — just confirmation of where the rendered copy went."""

    sent: bool
    to: str


# --------------------------------------------------------------------- engage setup checklist

SetupStepName = Literal["connection", "domain", "sender", "contacts", "campaign"]
SetupStepStatus = Literal["done", "todo", "blocked"]


class SetupStepOut(BaseModel):
    """One step of the "get started" checklist, folded from whatever this organization has
    already done — nothing here is stored; `services.build_setup_checklist` computes it fresh
    from the same rows the rest of Engage reads."""

    step: SetupStepName
    status: SetupStepStatus
    #: What is true right now — "Connected via Google Workspace", "2 of 3 domains verified" — or
    #: what is missing.
    detail: str
    #: The frontend route this step's screen lives at, relative to the product's own root.
    link: str


class SetupOut(BaseModel):
    steps: list[SetupStepOut]


# --------------------------------------------------------------------- engage inbox (WhatsApp)

ConversationStatus = Literal["open", "closed"]
MessageDirection = Literal["inbound", "outbound"]


class ConversationOut(BaseModel):
    id: str
    channel: Channel
    #: Masked. The number is sealed at rest; this route never opens it — see `CLAUDE.md`'s PII
    #: rule and the module docstring on `models.Conversation`.
    wa_address: str
    #: A `customer.contacts` id, when one has been resolved (`app/consumers.py`'s
    #: `fold_inbound_contact`). None for a stranger, or before resolution has run.
    contact_id: str | None
    last_customer_message_at: datetime | None
    #: Whether a free-text reply is possible right now — `webhooks.within_service_window`,
    #: evaluated at read time so the inbox never has to reimplement the 24-hour rule client-side.
    within_service_window: bool
    status: ConversationStatus
    assignee_membership_id: str | None
    created_at: datetime
    updated_at: datetime


class ConversationMessageOut(BaseModel):
    """One entry in a conversation's thread, either direction.

    `body` is masked for an inbound message (the customer's own words) and shown in full for an
    outbound one (the organization's own reply) — see `selectors.thread_stmt`'s docstring for
    why only one side is PII-gated here. `engage.inbox.message.unmask` is the audited path to an
    inbound message's real text.
    """

    id: str
    direction: MessageDirection
    message_type: str
    body: str
    occurred_at: datetime
    provider_message_id: str | None
    #: The sender's WhatsApp profile name. Always None for an outbound entry.
    profile_name: str | None


class ConversationReplyIn(BaseModel):
    """Free text, sent inside the conversation's 24-hour service window.

    `idempotency_key` is the caller's own — a retried click or a flaky connection must not send
    the same words twice, and the key is what makes the retry a no-op instead of a second
    message (`services.reply_to_conversation`'s `dedupe_key`).
    """

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4096)
    idempotency_key: str = Field(min_length=1, max_length=120)


class ConversationReplyOut(BaseModel):
    message_id: str
    #: pending | sending | sent | delivered | read | failed | suppressed. `suppressed` is how a
    #: customer's earlier STOP reaches a reply already in flight — see `services.
    #: reply_to_conversation`'s docstring.
    state: MessageState
    conversation_id: str


class ConversationAssignIn(BaseModel):
    """Hand a conversation to a membership, or clear its assignment with `null`."""

    model_config = ConfigDict(extra="forbid")

    assignee_membership_id: uuid.UUID | None = None


class UnmaskedMessageOut(BaseModel):
    """The audited, stepped-up reveal of one inbound message's real text.

    Returned by `engage.inbox.message.unmask` alone — never embedded in the ordinary thread
    read, so a masked body and its plaintext never travel in the same response.
    """

    message_id: str
    body: str | None
