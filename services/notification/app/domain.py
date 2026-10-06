"""Pure notification logic. No I/O, no session, no network.

Everything here is a function of its arguments, which is what makes the rules that matter
testable without a database: strict rendering, recipient resolution, suppression, and the
retry decision. `services.py` and `dispatcher.py` do the I/O around them.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from typing import Any, Final

#: The channels this service can actually reach. A rule naming anything else is refused at
#: write time rather than discovered when a message fails to send.
#:
#: `in_app` is the platform's own feed (`app/inbox.py`, `GET /notifications/me`) rather than a
#: provider: `to_address` holds the recipient's **membership id**, not an email or phone number,
#: and `transports.InAppTransport` sends nothing over a network — the row landing in `messages`
#: *is* the delivery. Everything else about it (staging, suppression, dedupe, the dispatcher's
#: claim/send/record cycle) is the same generic pipeline `email` and `whatsapp` already use; no
#: new event infrastructure was needed to add it.
CHANNELS: Final[tuple[str, ...]] = ("email", "whatsapp", "in_app")

#: `Message.channel` value for the in-app feed. Named once here so `app/inbox.py` and
#: `transports.py` never repeat the literal.
IN_APP_CHANNEL: Final = "in_app"

#: Message lifecycle. `pending` is written by the consumer and the API; every transition out
#: of it belongs to the dispatcher, so exactly one process talks to a provider.
#:
#: `sending` is not bookkeeping. The dispatcher claims a message in one transaction, talks to the
#: provider with **no transaction open**, and records the outcome in a second — so a provider
#: outage can never hold a database transaction. The cost of that split is a crash window: a
#: process that dies mid-send leaves a `sending` row, which is deliberately visible rather than
#: silently retried, because "did the customer get it?" has no safe default answer.
STATE_PENDING: Final = "pending"
STATE_SENDING: Final = "sending"
STATE_SENT: Final = "sent"
STATE_DELIVERED: Final = "delivered"
STATE_READ: Final = "read"
STATE_FAILED: Final = "failed"
STATE_SUPPRESSED: Final = "suppressed"

#: States under which an in-app message has actually reached the feed. `pending` (not yet
#: dispatched) and `sending` (claimed, not yet recorded) are transient; `failed` and `suppressed`
#: mean the recipient was deliberately or accidentally never shown it. Only these three are
#: something to list.
IN_APP_VISIBLE_STATES: Final[tuple[str, ...]] = (STATE_SENT, STATE_DELIVERED, STATE_READ)

#: How far a message has got, low to high. Receipts arrive out of order — Meta batches them and
#: the network reorders them — so a `delivered` arriving after a `read` must not walk the state
#: backwards and report a message as less delivered than it is. Comparing ranks is what makes
#: the fold monotonic; ignoring order would make the inbox lie.
_PROGRESS: Final[dict[str, int]] = {
    STATE_PENDING: 0,
    STATE_SENDING: 1,
    STATE_SENT: 2,
    STATE_DELIVERED: 3,
    STATE_READ: 4,
}


def advances(current: str, incoming: str) -> bool:
    """Does `incoming` represent progress on a message currently in `current`?

    `failed` is deliberately outside the ladder. A message can fail after being sent — Meta
    reports both — and a failure is worth recording whatever came before it, so it always
    advances. What never advances is a receipt for a message already `failed` or `suppressed`:
    those are terminal, and a late `delivered` on a suppressed message means we have confused
    two messages, not that the suppressed one arrived.
    """
    if current in (STATE_FAILED, STATE_SUPPRESSED):
        return False
    if incoming == STATE_FAILED:
        return True
    return _PROGRESS.get(incoming, -1) > _PROGRESS.get(current, -1)


#: Suppresses every category for a recipient on a channel.
CATEGORY_ALL: Final = "*"

#: Every routing key this service can be configured to react to, grouped by the product that
#: publishes it. The list is explicit rather than a `#` wildcard binding for three reasons: the
#: shared `EventConsumer` dispatches on the exact event type, so a wildcard binding would
#: receive messages no handler could claim; an operator authoring a rule deserves to be told
#: that a typo'd event type will never fire, which needs a set to check against; and binding
#: `#` would include this service's own events, so a rule could stage a message about staging
#: a message.
#:
#: Adding a producer means adding its key here. That is the intended friction: a platform where
#: any string is a valid trigger cannot tell an operator which automations are possible.
SUBSCRIBABLE_EVENTS: Final[tuple[str, ...]] = (
    # catalog
    "catalog.plan.reactivated",
    "catalog.plan.retired",
    "catalog.plan_version.grandfathered",
    "catalog.plan_version.published",
    # commerce
    "commerce.credit.consumed",
    "commerce.credit.granted",
    "commerce.credit.reversed",
    "commerce.invoice.finalized",
    "commerce.invoice.voided",
    "commerce.payment.captured",
    "commerce.payment.failed",
    "commerce.subscription.activated",
    "commerce.subscription.cancelled",
    "commerce.subscription.downgraded",
    "commerce.subscription.past_due",
    "commerce.subscription.payment_retry_due",
    "commerce.subscription.renewed",
    "commerce.subscription.suspended",
    # Staff moved a trial's end later (ADR-0031). Its own key, so a "trial started" rule never
    # fires for an extension; an operator may author a rule on this one deliberately.
    "commerce.subscription.trial_extended",
    "commerce.subscription.trial_started",
    "commerce.subscription.upgraded",
    "commerce.usage.recorded",
    # core
    "core.grants.changed",
    "core.location.created",
    "core.membership.created",
    "core.membership.revoked",
    "core.membership.updated",
    "core.operator.signed_in",
    "core.org.created",
    "core.org.updated",
    "core.org_unit.created",
    "core.org_unit.updated",
    "core.product.activated",
    "core.product.deactivated",
    "core.session.signed_in",
    "core.session.signed_out",
    "core.session.sso_signed_in",
    "core.session.token_reuse_detected",
    "core.user.created",
    "core.user.mfa_enrolled",
    "core.user.password_changed",
    "core.user.password_reset_requested",
    # crm
    "crm.book.company.created",
    "crm.book.contact.created",
    "crm.pipeline.activity.logged",
    "crm.pipeline.deal.created",
    "crm.pipeline.deal.stage_changed",
    # finance
    "finance.billing.customer.created",
    "finance.billing.invoice.issued",
    "finance.billing.payment.received",
    "finance.compliance.eway_bill.cancelled",
    "finance.compliance.eway_bill.failed",
    "finance.compliance.eway_bill.generated",
    "finance.compliance.gst_registration.set",
    "finance.compliance.gst_return.filed",
    "finance.compliance.gst_return.generated",
    "finance.compliance.ims_document.decided",
    "finance.compliance.ims_document.ingested",
    "finance.compliance.invoice.filed",
    "finance.compliance.invoice.filing_cancelled",
    "finance.compliance.invoice.filing_failed",
    "finance.compliance.invoice.filing_requested",
    "finance.compliance.withholding.recorded",
    # hrms
    "hrms.people.employee.onboarded",
    "hrms.people.employee.transitioned",
    "hrms.people.employee.updated",
    "hrms.time.attendance.checked_in",
    "hrms.time.attendance.checked_out",
    "hrms.time.attendance.regularized",
    "hrms.time.geofence.created",
    "hrms.time.geofence.updated",
    "hrms.time.shift.created",
    "hrms.time.shift.updated",
    # itsm
    "itsm.cmdb.asset.created",
    "itsm.service.incident.raised",
    "itsm.service.incident.worklog_added",
    "itsm.service.request.raised",
    # marketing
    "marketing.analytics.audience.created",
    "marketing.analytics.campaign.created",
    # operations
    # Fulfilment plan §4 (F12, operational automation) — alerts an automation may act on. All
    # seven read the same way order-lifecycle events did (`e42c9ae1`): the routing key and the
    # documented payload only, never an import, so a rule or automation on one works whether or
    # not that phase of `operations` is deployed on this branch.
    "operations.dispatch.shipment.delayed",
    "operations.dispatch.shipment.failed",
    "operations.fulfilment.pick.exception_raised",
    "operations.fulfilment.task.delayed",
    "operations.fulfilment.task.escalated",
    "operations.inventory.inconsistency_detected",
    "operations.inventory.item.created",
    "operations.inventory.item.low_stock",
    "operations.inventory.warehouse.created",
    # `operations.orders.order.held` is deliberately absent — an operator pausing a warehouse
    # task is an internal operational state, not a commercial milestone a customer is told
    # about mid-flow. Same call `app.orders.services.release_hold_order` already makes for the
    # matching release (no outbox event at all, "resumes whatever commercial milestone the order
    # was already at rather than reaching a new one") and the same shape `create_order`'s DRAFT
    # uses ("a private working copy nothing outside operations needs to know about yet").
    "operations.orders.order.accepted",
    "operations.orders.order.cancelled",
    "operations.orders.order.placed",
    "operations.procurement.purchase_order.raised",
    "operations.procurement.purchase_order.received",
    "operations.procurement.supplier.created",
    # projects
    "projects.board.task.created",
    "projects.delivery.milestone.created",
    "projects.portfolio.project.created",
    # pulseboard
    "pulseboard.board.task.created",
    "pulseboard.engagements.project.created",
    "pulseboard.standup.entry.posted",
    "pulseboard.time.timesheet.recorded",
    # support
    "support.desk.ticket.assigned",
    "support.desk.ticket.created",
    "support.desk.ticket.replied",
)

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

#: Deliberately permissive — this validates shape, not deliverability. A real address that
#: bounces is the provider's answer to give; a string with no `@` is a rule authoring mistake
#: and should never reach a provider at all.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

#: E.164: a leading `+`, a non-zero country digit, up to fifteen digits total.
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class RenderError(ValueError):
    """A template referenced something the event payload does not contain."""


class RuleError(ValueError):
    """A rule is not coherent — it would never fire, or would fire at nobody."""


@dataclass(frozen=True, slots=True)
class Rendered:
    """The text a message will carry, and the address it goes to."""

    to_address: str
    subject: str | None
    body: str


def resolve_path(payload: dict[str, Any], path: str) -> Any:
    """Follow a dotted path into an event payload. Returns None if any segment is missing.

    Missing is None rather than an exception because a *rule* asking for a field an event does
    not carry is an ordinary, expected condition — the ticket had no phone number — and the
    caller decides whether that means "skip" or "refuse". A raise here would make a rule that
    matches ninety-nine events and misses the hundredth look like a bug in the hundredth.
    """
    current: Any = payload
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def placeholders(text: str) -> set[str]:
    """Every `{{placeholder}}` a template refers to. Used to validate a template up front."""
    return {match.group(1) for match in _PLACEHOLDER.finditer(text)}


def render(text: str, payload: dict[str, Any]) -> str:
    """Substitute `{{placeholders}}` from the payload. **Strict** — unresolved raises.

    The alternative designs both end badly. Leaving the literal `{{name}}` sends a customer a
    message with a broken template in it. Substituting an empty string sends them "Dear ," which
    is worse, because nobody notices it is broken. A raise keeps the failure on our side of the
    wire, where a queue and an operator can see it.

    A value that resolves to None counts as unresolved: a key that exists with a null value is
    exactly as unsendable as a key that does not exist.
    """
    missing: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        path = match.group(1)
        value = resolve_path(payload, path)
        if value is None:
            missing.append(path)
            return ""
        return str(value)

    result = _PLACEHOLDER.sub(substitute, text)
    if missing:
        raise RenderError(f"Unresolved placeholders: {', '.join(sorted(set(missing)))}")
    return result


def normalize_recipient(channel: str, raw: str) -> str:
    """Canonicalise an address for a channel, or raise if it is not one.

    Email is lower-cased and trimmed. WhatsApp is normalised towards E.164: separators a human
    typed are stripped, and a bare number is refused rather than guessed at — a missing country
    code is not something to assume, because assuming wrongly sends a stranger somebody else's
    invoice. `in_app`'s "address" is a membership id, not a postal address of any kind — the
    recipient is a row in `account.memberships`, named by a plain UUID the same way
    `Conversation.assignee_membership_id` already is, so it is validated as one rather than
    guessed at with an address-shaped regex.
    """
    value = raw.strip()
    if not value:
        raise RuleError("Recipient is empty")

    if channel == "email":
        value = value.lower()
        if not _EMAIL.match(value):
            raise RuleError(f"{raw!r} is not an email address")
        return value

    if channel == IN_APP_CHANNEL:
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise RuleError(f"{raw!r} is not a membership id") from exc

    if channel == "whatsapp":
        compact = re.sub(r"[\s\-().]", "", value)
        if not compact.startswith("+"):
            raise RuleError(
                f"{raw!r} is not in E.164 — a WhatsApp recipient needs its country code"
            )
        if not _E164.match(compact):
            raise RuleError(f"{raw!r} is not a valid E.164 number")
        return compact

    raise RuleError(f"{channel!r} is not a channel this service can reach")


def validate_rule(
    *,
    channel: str,
    event_type: str,
    recipient_path: str | None,
    recipient_literal: str | None,
    recipient_contact_path: str | None = None,
    template_code: str | None = None,
    provider_template_code: str | None = None,
) -> None:
    """Refuse a rule that could not work. Raised at write time, not at send time.

    A rule with neither recipient field fires and reaches nobody; a rule with both is ambiguous
    about which wins. Both are authoring mistakes, and an authoring mistake found when the
    invoice reminder does not arrive is found far too late.
    """
    if channel not in CHANNELS:
        raise RuleError(f"{channel!r} is not a channel this service can reach")
    if not event_type.strip():
        raise RuleError("A rule must name the event type it reacts to")
    if event_type not in SUBSCRIBABLE_EVENTS:
        # A rule on an unbound event type is not a small mistake: it saves cleanly, shows up in
        # the operator's list looking exactly like a working automation, and never fires. The
        # failure is an absence, which is the hardest kind to notice.
        raise RuleError(
            f"{event_type!r} is not an event this service subscribes to. "
            "See SUBSCRIBABLE_EVENTS for the routing keys the platform publishes."
        )
    # Three ways to say who to message, and exactly one of them. Two would be ambiguous about
    # which wins; none leaves a rule that fires and reaches nobody.
    sources = [
        ("recipient_path", recipient_path),
        ("recipient_literal", recipient_literal),
        ("recipient_contact_path", recipient_contact_path),
    ]
    given = [name for name, value in sources if value]
    if len(given) > 1:
        raise RuleError("A rule names one recipient source, not several: got " + ", ".join(given))
    if not given:
        raise RuleError(
            "A rule needs a recipient: recipient_path, recipient_literal or recipient_contact_path"
        )
    if recipient_literal:
        # Validated now so a literal typo is a 422 on the rule, not a failed send later.
        normalize_recipient(channel, recipient_literal)

    # Exactly one source of wording. Both would be ambiguous about which the customer reads;
    # neither leaves a rule that fires and renders nothing.
    if template_code and provider_template_code:
        raise RuleError(
            "A rule sends either a template of ours or one the provider holds, not both"
        )
    if not template_code and not provider_template_code:
        raise RuleError("A rule needs a template: template_code or provider_template_code")


def is_suppressed(
    preferences: list[tuple[str, str]],
    *,
    channel: str,
    category: str,
) -> bool:
    """Does any preference row refuse this channel and category?

    `preferences` is the recipient's own `(channel, category)` refusals, already fetched. A row
    for `*` refuses the channel outright; a row for the category refuses just that one. Absence
    means send — see `models.Preference`.
    """
    for pref_channel, pref_category in preferences:
        if pref_channel != channel:
            continue
        if pref_category in (CATEGORY_ALL, category):
            return True
    return False


def dedupe_key(
    *,
    rule_code: str,
    event_id: str | None,
    payload: dict[str, Any],
    dedupe_key_path: str | None,
) -> str:
    """The identity of "this exact send".

    Without a path, the event id is the identity: one rule reacting to one event is one message,
    which is what makes a redelivery harmless even if it gets past `processed_events`. With a
    path, the *business* value is the identity — an invoice number — so a producer that
    legitimately republishes an event about the same invoice still sends once.
    """
    if dedupe_key_path:
        value = resolve_path(payload, dedupe_key_path)
        if value is not None:
            return f"{rule_code}:{value}"
    return f"{rule_code}:{event_id or 'direct'}"


# --------------------------------------------------------------------- provider templates
#
# A provider template is an artifact registered with the channel provider, not text we own. Meta
# holds the body; we supply parameters. This is the only way to reach a WhatsApp user outside the
# 24-hour customer service window, which is to say it is the only way to send a notification the
# customer did not just ask for.
#
# Every field name below was read off Meta's own published example rather than recalled. The
# verified shape is:
#
#     {"messaging_product": "whatsapp", "to": "…", "type": "template",
#      "template": {"name": "…", "language": {"code": "en_US"},
#                   "components": [
#                     {"type": "body",   "parameters": [{"type": "text", "text": "Pablo"}]},
#                     {"type": "header", "parameters": [{"type": "image", "image": {"id": "…"}}]},
#                     {"type": "button", "sub_type": "quick_reply", "index": "0",
#                      "parameters": [{"type": "payload", "payload": "…"}]}]}}
#
# Note `index` is a **string** in that example, not an integer, and it is emitted as one here.

#: Template parameter formats, declared at the template's creation with Meta. Positional
#: templates take values in order; named templates take them by label.
FORMAT_POSITIONAL: Final = "positional"
FORMAT_NAMED: Final = "named"

#: Where a variable lands in the rendered template.
COMPONENT_BODY: Final = "body"
COMPONENT_HEADER: Final = "header"
COMPONENT_BUTTON: Final = "button"

#: Meta's template categories. `authentication` is listed for completeness; sending one is a
#: different compliance question and nothing here selects it.
CATEGORIES: Final[tuple[str, ...]] = ("utility", "marketing", "authentication")

#: Only an approved template can be sent. The others are recorded so an operator can see why a
#: send is refused without leaving the product.
STATUS_APPROVED: Final = "approved"
TEMPLATE_STATUSES: Final[tuple[str, ...]] = (
    "draft",
    "pending",
    STATUS_APPROVED,
    "rejected",
    "paused",
    "disabled",
)


class TemplateError(ValueError):
    """A template cannot be sent as configured, or the payload it needs cannot be built."""


@dataclass(frozen=True, slots=True)
class TemplateVariable:
    """One value a template expects — and the mapping between our name for it and Meta's.

    `key` is our own vocabulary: the dotted path a rule's payload resolves, the same name the
    ERP writes as `{{customer_name}}`. The rest of the service only ever speaks this.

    The provider's side of the mapping is the other two fields, and which one matters depends on
    how the template was registered with Meta:

      positional templates  `position` — the order the parameter appears within its component.
      named templates       `parameter_name` — Meta's own label for the placeholder.

    Keeping both here is the mapping layer. It means the ERP can say `{{invoice_number}}` while
    Meta receives either `BODY` parameter 1 or `parameter_name: "invoice_number"`, and nothing
    outside this row has to know which.

    `button_index` identifies which button, and is only meaningful for `COMPONENT_BUTTON`.
    """

    key: str
    component: str = COMPONENT_BODY
    position: int = 0
    button_index: int = 0
    #: `text` | `payload`. Media parameters are out of scope until document-service exists.
    kind: str = "text"
    #: Meta's label for this placeholder, required for a named-format template and ignored for a
    #: positional one. Lowercase and underscores only — Meta's own constraint.
    parameter_name: str = ""


def _validate_components(variables: list[TemplateVariable]) -> None:
    """Checks that hold for either format: a real component, a sane position."""
    for var in variables:
        if var.component not in (COMPONENT_BODY, COMPONENT_HEADER, COMPONENT_BUTTON):
            raise TemplateError(f"{var.component!r} is not a template component")
        if var.position < 0:
            raise TemplateError(f"{var.key!r} has a negative position")


def validate_template_variables(variables: list[TemplateVariable]) -> None:
    """Refuse a **positional** variable set that could not produce a well-formed payload.

    Checked at authoring time rather than at send time, because a malformed template is an
    operator's mistake and discovering it when the invoice reminder fails to arrive is far too
    late. Meta rejects the whole message, so one bad position loses every parameter with it.

    Only for positional templates. A named template is matched by label and its values may
    arrive in any order, so requiring unique contiguous positions there would force an operator
    to invent numbers that mean nothing — see `validate_named_variables`.
    """
    _validate_components(variables)

    seen: set[tuple[str, int, int]] = set()
    for var in variables:
        slot = (var.component, var.button_index, var.position)
        if slot in seen:
            raise TemplateError(
                f"two variables both claim {var.component} position {var.position}"
                + (f" on button {var.button_index}" if var.component == COMPONENT_BUTTON else "")
            )
        seen.add(slot)

    # Positions within a component must be contiguous from zero. Meta fills them in order, so a
    # gap does not leave a hole — it shifts every later parameter into the wrong placeholder,
    # which sends the customer a real message containing someone else's data.
    groups: dict[tuple[str, int], list[int]] = {}
    for var in variables:
        groups.setdefault((var.component, var.button_index), []).append(var.position)
    for (component, button_index), positions in groups.items():
        expected = list(range(len(positions)))
        if sorted(positions) != expected:
            where = component + (f" button {button_index}" if component == COMPONENT_BUTTON else "")
            raise TemplateError(
                f"{where} positions are {sorted(positions)}; they must be {expected} — "
                "a gap shifts every later parameter into the wrong placeholder"
            )


#: Meta's constraint on a named placeholder: lowercase letters, digits and underscores.
_PARAMETER_NAME = re.compile(r"^[a-z0-9_]+$")


def validate_named_variables(variables: list[TemplateVariable]) -> None:
    """Refuse a named-format template whose mapping to Meta is incomplete or malformed.

    A positional template is wrong loudly — values land in the wrong placeholders. A named
    template is wrong quietly: Meta matches on the label, so a missing or misspelled
    `parameter_name` means that placeholder is simply never filled, and the customer reads a
    message with a hole in it while the send reports success. That is the failure this refuses.

    Deliberately says nothing about `position`. Named values may arrive in any order, so the
    positional checks do not apply and enforcing them would demand meaningless numbers.
    """
    _validate_components(variables)

    for var in variables:
        if var.component == COMPONENT_BUTTON:
            # Buttons are identified by index in both formats; a name is neither required nor
            # published for them.
            continue
        if not var.parameter_name:
            raise TemplateError(
                f"{var.key!r} has no parameter_name, which a named-format template requires — "
                "Meta matches these by label, so an unnamed one is never filled"
            )
        if not _PARAMETER_NAME.match(var.parameter_name):
            raise TemplateError(
                f"{var.parameter_name!r} is not a valid Meta parameter name: lowercase "
                "letters, digits and underscores only"
            )

    named = [v.parameter_name for v in variables if v.component != COMPONENT_BUTTON]
    duplicates = {n for n in named if named.count(n) > 1}
    if duplicates:
        raise TemplateError(
            "two variables share a parameter_name: " + ", ".join(sorted(duplicates))
        )


def build_template_payload(
    *,
    template_name: str,
    language: str,
    variables: list[TemplateVariable],
    context: dict[str, Any],
    parameter_format: str = FORMAT_POSITIONAL,
) -> dict[str, Any]:
    """Build the `template` object Meta expects, or raise.

    **Named format is deliberately refused.** Meta's documentation establishes that a template
    may declare named parameters, but the shape a *named* parameter takes in the send payload is
    not published on any page consulted, and inventing it would mean shipping a guess into a
    customer's phone. A named template therefore fails loudly here with an instruction, rather
    than being silently sent as positional and rejected — or worse, accepted with the values in
    the wrong placeholders.
    """
    if parameter_format not in (FORMAT_POSITIONAL, FORMAT_NAMED):
        raise TemplateError(f"{parameter_format!r} is not a template parameter format")

    # Each format has its own coherence rules and they are not interchangeable: positional
    # cares about order, named cares about labels.
    if parameter_format == FORMAT_NAMED:
        validate_named_variables(variables)
    else:
        validate_template_variables(variables)

    missing = [v.key for v in variables if resolve_path(context, v.key) is None]
    if missing:
        # The same strictness as `render`: a template that reaches Meta with a blank parameter
        # is a message the customer reads with a hole in it.
        raise TemplateError(f"No value for: {', '.join(sorted(set(missing)))}")

    named = parameter_format == FORMAT_NAMED
    components: list[dict[str, Any]] = []

    for component in (COMPONENT_HEADER, COMPONENT_BODY):
        params = sorted(
            (v for v in variables if v.component == component), key=lambda v: v.position
        )
        if params:
            components.append(
                {
                    "type": component,
                    "parameters": [_parameter(v, context, named=named) for v in params],
                }
            )

    buttons = [v for v in variables if v.component == COMPONENT_BUTTON]
    for button_index in sorted({v.button_index for v in buttons}):
        params = sorted(
            (v for v in buttons if v.button_index == button_index), key=lambda v: v.position
        )
        components.append(
            {
                "type": COMPONENT_BUTTON,
                # Two valid sub_types, read off Meta's example: a `quick_reply` button carries
                # an opaque `payload` parameter, a `url` button carries a `text` suffix for the
                # link. `payload` is the *parameter* type, never the sub_type — emitting it here
                # produced a payload Meta rejects outright, which the dispatcher would classify
                # permanent and the message would die with no retry.
                "sub_type": "quick_reply" if params[0].kind == "payload" else "url",
                # A string, matching Meta's published example rather than the integer it looks
                # like it should be.
                "index": str(button_index),
                # Button parameters stay in the positional shape even for a named template.
                # Meta publishes `parameter_name` for header and body; it publishes no named
                # form for a button, and `index` already identifies which button this is
                # unambiguously. Emitting an unverified `parameter_name` here would be
                # inventing a payload; omitting it leaves the shape Meta does document. If Meta
                # refuses it the 4xx is classified permanent and visible, rather than silently
                # filling the wrong button.
                "parameters": [_parameter(v, context) for v in params],
            }
        )

    return {
        "name": template_name,
        "language": {"code": language},
        "components": components,
    }


def _parameter(
    var: TemplateVariable, context: dict[str, Any], *, named: bool = False
) -> dict[str, Any]:
    """One parameter object. `payload` carries an opaque value; everything else is text.

    For a named-format template Meta expects `parameter_name` beside the value, and the `type`
    stays lowercase `"text"` — read off Meta's own send example, which matters because a
    third-party summary of the same feature showed it uppercase. Getting that wrong is the
    `sub_type` bug again.
    """
    value = str(resolve_path(context, var.key))
    if var.kind == "payload":
        return {"type": "payload", "payload": value}
    param: dict[str, Any] = {"type": "text", "text": value}
    if named:
        param["parameter_name"] = var.parameter_name
    return param


def template_is_sendable(status: str) -> bool:
    """Only an approved template may be sent.

    Checked before staging rather than left to Meta, because Meta's refusal arrives as a 4xx
    that the dispatcher classifies permanent — a failed message and an operator wondering why,
    when the answer ("that template is still in review") was knowable in advance.
    """
    return status == STATUS_APPROVED


def should_retry(attempts: int, *, max_attempts: int) -> bool:
    """Whether a failed message is worth claiming again.

    Bounded rather than infinite: a permanently bad address would otherwise be retried forever,
    and a queue that never drains hides the messages that *could* still be sent. A message that
    exhausts its attempts stays `failed` with its last error, visible to an operator.
    """
    return attempts < max_attempts


def next_attempt_no(previous: int) -> int:
    """Attempts are numbered from one, monotonically, per message."""
    return previous + 1


# --------------------------------------------------------------------- engage sending identity
#
# An organisation brings its own provider and sends under its own reputation — one tenant's spam
# complaints must not become everyone's deliverability problem. The wizard therefore asks *how*
# they would like to send, never for a port, and the three functions below are everything that
# choice implies: the transport it means, the DNS an organisation must publish to send as its own
# domain, and whether that DNS is good enough to start.


@dataclass(frozen=True, slots=True)
class TransportSettings:
    """What a connection kind means in SMTP terms.

    The customer chooses "Google Workspace", not a port. Everything below that choice is derived
    here so the wizard never has to ask, and so a provider changing its submission host is one
    edit in one place.
    """

    host: str
    port: int
    use_tls: bool
    daily_cap: int


#: Caps are the provider's published external-recipient limits, not ours. They exist so a campaign
#: can be refused before it burns a mailbox rather than after — a suspended Workspace account is a
#: worse outcome than a rejected campaign.
_KINDS: Final[dict[str, TransportSettings]] = {
    "google": TransportSettings("smtp.gmail.com", 587, True, 2_000),
    "microsoft": TransportSettings("smtp.office365.com", 587, True, 10_000),
}


def transport_for(kind: str, username: str) -> TransportSettings:
    """Derive transport settings from the connection kind the customer chose.

    `username` is taken and not read: every kind's submission endpoint is a property of the
    provider, not of the account holding it. It stays in the signature because the caller derives
    transport and credentials in one step, and the first kind whose host *is* per-account — a
    hosted tenant on its own submission subdomain — would otherwise move every call site.
    """
    if kind in _KINDS:
        return _KINDS[kind]
    if kind == "smtp":
        # The one kind whose endpoint we cannot know. Port and TLS are still ours to insist on,
        # so the wizard asks for a host and the caller fills it into this shape.
        return TransportSettings("", 587, True, 2_000)
    if kind == "platform":
        # Modelled, not reachable: platform-managed sending needs an MTA the platform does not
        # run yet. The cap is what that MTA would be built to, recorded so the gap is visible
        # rather than discovered by a campaign that silently sends nothing.
        return TransportSettings("", 587, True, 50_000)
    raise ValueError(f"Unknown connection kind {kind!r}")


#: The provider's own SPF host, for the two connection kinds whose transport is fixed. `smtp`
#: has no equivalent here — the customer's provider names its own, on the domain row
#: (`spf_include`), because we cannot know a self-hosted relay's SPF host any more than we can
#: know its submission host.
_PROVIDER_SPF_TARGET: Final[dict[str, str]] = {
    "google": "_spf.google.com",
    "microsoft": "spf.protection.outlook.com",
}


def spf_target(connection_kind: str, spf_include: str | None) -> str | None:
    """The SPF include mechanism's target host for `connection_kind`, or None when it is not
    yet knowable — an `smtp` connection whose customer has not named their provider's SPF host."""
    if connection_kind in _PROVIDER_SPF_TARGET:
        return _PROVIDER_SPF_TARGET[connection_kind]
    if connection_kind == "smtp":
        return spf_include or None
    return None


def expected_records(
    domain_name: str,
    *,
    connection_kind: str | None,
    dkim_selector: str,
    spf_include: str | None = None,
) -> list[dict[str, Any]]:
    """The record set an organization must publish to send as `domain_name`, derived from *how
    they currently send* rather than frozen at the moment the domain was added.

    Real sending always goes through the customer's own provider, and each one proves SPF and
    DKIM its own way — there is no OpsGrids mail infrastructure to publish a record for, and no
    return-path or tracking host either: nothing on this platform signs mail with a key it
    mints, processes a bounce envelope, or rewrites a link to count a click. `connection_kind is
    None` (no provider connected yet) returns nothing to publish — there is nothing to verify
    against until the organization says how it sends.

    DMARC is `required=False` deliberately, for every provider: it is the right thing to publish
    and the setup screen recommends it, but a domain whose security team has not settled a policy
    yet still sends — refusing would block a customer on a decision that is not this platform's
    to make for them.
    """
    if connection_kind not in ("google", "microsoft", "smtp"):
        return []

    records: list[dict[str, Any]] = []
    target = spf_target(connection_kind, spf_include)
    if target is None:
        records.append(
            {
                "kind": "spf",
                "type": "TXT",
                "fqdn": domain_name,
                "registrar_name": "@",
                "value": "",
                "suggested_value": None,
                "required": True,
                "instruction": (
                    "Enter your email provider's SPF include below (for example "
                    "amazonses.com), then publish and verify."
                ),
            }
        )
    else:
        default_value = f"v=spf1 include:{target} ~all"
        records.append(
            {
                "kind": "spf",
                "type": "TXT",
                "fqdn": domain_name,
                "registrar_name": "@",
                "value": default_value,
                "suggested_value": default_value,
                "required": True,
                "instruction": (
                    f"Publish one SPF TXT record at your domain's apex that includes {target}. "
                    "A domain may have only one SPF record — if you already have one, add the "
                    "include to it rather than publishing a second."
                ),
            }
        )

    if connection_kind == "google":
        records.append(
            {
                "kind": "dkim",
                "type": "TXT",
                "fqdn": f"{dkim_selector}._domainkey.{domain_name}",
                "registrar_name": f"{dkim_selector}._domainkey",
                "value": "(generated by Google Workspace)",
                "suggested_value": None,
                "required": True,
                "instruction": (
                    "In the Google Admin console, go to Apps -> Google Workspace -> Gmail -> "
                    "Authenticate email, generate a key, publish the TXT value it gives you "
                    "here, then click Start authentication."
                ),
            }
        )
    elif connection_kind == "microsoft":
        for selector in ("selector1", "selector2"):
            records.append(
                {
                    "kind": "dkim",
                    "type": "CNAME",
                    "fqdn": f"{selector}._domainkey.{domain_name}",
                    "registrar_name": f"{selector}._domainkey",
                    "value": "(from Microsoft Defender)",
                    "suggested_value": None,
                    "required": True,
                    "instruction": (
                        "In the Microsoft Defender portal, go to Email authentication -> DKIM, "
                        f"and publish the CNAME target it gives you for {selector}."
                    ),
                }
            )
    elif dkim_selector:
        records.append(
            {
                "kind": "dkim",
                "type": "TXT",
                "fqdn": f"{dkim_selector}._domainkey.{domain_name}",
                "registrar_name": f"{dkim_selector}._domainkey",
                "value": "(from your email provider)",
                "suggested_value": None,
                "required": True,
                "instruction": (
                    "Publish your provider's DKIM record here — a TXT containing v=DKIM1, or "
                    "the CNAME target they give you."
                ),
            }
        )
    else:
        records.append(
            {
                "kind": "dkim",
                "type": "TXT",
                "fqdn": f"_domainkey.{domain_name}",
                "registrar_name": "_domainkey",
                "value": "",
                "suggested_value": None,
                "required": True,
                "instruction": (
                    "Enter your DKIM selector below, then publish your provider's record for it."
                ),
            }
        )

    records.append(
        {
            "kind": "dmarc",
            "type": "TXT",
            "fqdn": f"_dmarc.{domain_name}",
            "registrar_name": "_dmarc",
            "value": f"v=DMARC1; p=none; rua=mailto:dmarc@{domain_name}",
            "suggested_value": None,
            "required": False,
            "instruction": (
                "Recommended: publish a DMARC policy so mailbox providers know what to do with "
                "mail that fails SPF or DKIM. It never blocks sending."
            ),
        }
    )
    return records


def fold_domain_status(records: list[dict[str, Any]]) -> str:
    """`verified` when every required record's `status` is `verified`; otherwise `pending`.

    The `required` list is checked for emptiness before `all()`, not out of caution about an
    impossible input: a domain row exists from the moment a customer types the name, before its
    records have ever been checked, and `all()` over nothing is True — which would mark a domain
    that has proven nothing as ready to send. A domain with no connection yet has no expected
    records at all (`expected_records` returns `[]`), which folds the same way for the same
    reason.
    """
    required = [r for r in records if r.get("required")]
    if required and all(r.get("status") == "verified" for r in required):
        return "verified"
    return "pending"


# --------------------------------------------------------------------- SPF evaluation
#
# SPF is one TXT record at the domain's apex, and it is one of two things a bulk sender's DNS
# absolutely must get right (the other is DKIM, evaluated per provider below). The rules here are
# the actual RFC 7208 shape a customer's registrar panel has to satisfy: exactly one record,
# containing the provider's include as its own whole mechanism token — not a prefix, not a
# suffix, not a substring of a longer one.

#: An `all` mechanism, with its optional qualifier: `-all`, `~all`, `?all`, `+all`, or bare `all`.
_ALL_MECHANISM: Final = re.compile(r"^[-~?+]?all$", re.IGNORECASE)

#: Mechanisms RFC 7208 §4.6.4 counts against the ten-lookup ceiling. `a` and `mx` are counted
#: bare or with a domain-spec suffix (`a:example.com`, `mx/24`); the others always carry one.
_LOOKUP_PREFIXES: Final = ("include:", "exists:", "redirect=")

#: `_spf.opsgrids.com` never had an SPF record of its own — platform-managed sending was never
#: built (see this module's sending-identity section above) — so an `include:` naming it is not
#: a mechanism that will ever resolve, it is a guaranteed SPF permerror for every receiver that
#: evaluates it. Every domain this service generated a record for before the provider-aware
#: rewrite named it, so it is dropped by name rather than by a lookup this pure module has no way
#: to make.
_KNOWN_DEAD_INCLUDES: Final[frozenset[str]] = frozenset({"_spf.opsgrids.com"})


def _include_target(mechanism: str) -> str | None:
    if mechanism.lower().startswith("include:"):
        return mechanism.split(":", 1)[1]
    return None


def _dead_include_in(mechanisms: list[str]) -> str | None:
    """The first known-dead `include:` target among `mechanisms`, or None."""
    for part in mechanisms:
        target = _include_target(part)
        if target is not None and target.lower() in _KNOWN_DEAD_INCLUDES:
            return target
    return None


def _drop_dead_includes(mechanisms: list[str]) -> list[str]:
    return [
        part
        for part in mechanisms
        if not (
            (target := _include_target(part)) is not None and target.lower() in _KNOWN_DEAD_INCLUDES
        )
    ]


@dataclass(frozen=True, slots=True)
class SpfResult:
    """What one SPF check found, and what to do about it. `suggested_value` is **always**
    populated — there is always something to publish or edit, even when nothing exists yet."""

    status: str
    message: str
    suggested_value: str


def _spf_lookup_count(record: str) -> int:
    """How many of RFC 7208's ten permitted DNS lookups this record's mechanisms would spend."""
    count = 0
    for part in record.split()[1:]:
        lowered = part.lower()
        if (
            lowered.startswith(_LOOKUP_PREFIXES)
            or lowered in ("a", "mx")
            or lowered.startswith(("a:", "a/", "mx:", "mx/"))
        ):
            count += 1
    return count


def _insert_before_all(record: str, token: str) -> str:
    """`token` placed immediately before the `all` mechanism, so it takes effect before the
    record's default action — appending after `all` would make it dead text SPF never reads."""
    parts = record.split()
    for i, part in enumerate(parts):
        if _ALL_MECHANISM.match(part):
            return " ".join([*parts[:i], token, *parts[i:]])
    return " ".join([*parts, token])


def _merge_spf(records: list[str], token: str) -> str:
    """One record built from the union of every mechanism across `records`, plus `token` if none
    of them already carry it, with a single trailing `~all` — the shape a customer publishes to
    replace every one of several competing records with the one SPF allows.

    A known-dead `include:` (`_KNOWN_DEAD_INCLUDES`) is dropped rather than carried into the
    merge — publishing it again would keep the permerror the merge exists to fix.
    """
    mechanisms: list[str] = []
    seen: set[str] = set()
    for record in records:
        for part in record.split()[1:]:
            if _ALL_MECHANISM.match(part):
                continue
            target = _include_target(part)
            if target is not None and target.lower() in _KNOWN_DEAD_INCLUDES:
                continue
            if part.lower() not in seen:
                seen.add(part.lower())
                mechanisms.append(part)
    if token.lower() not in seen:
        mechanisms.append(token)
    return " ".join(["v=spf1", *mechanisms, "~all"])


def evaluate_spf(txt_records: list[str], *, required_include: str) -> SpfResult:
    """Fold a domain apex's published TXT answers into one SPF verdict.

    Zero SPF-shaped records: `missing`. More than one: `multiple` — a domain may publish only
    one, so this is refused rather than merged silently; the suggested value is what merging them
    by hand would produce. Exactly one: `verified` iff it contains `include:<required_include>`
    as its own mechanism token (never a substring of a longer one — `include:_spf.google.com.evil
    .com` must not read as containing `include:_spf.google.com`); otherwise `mismatch`, with the
    existing record edited in place rather than a second one to add.
    """
    candidates = [r.strip() for r in txt_records if r.strip().lower().startswith("v=spf1")]
    token = f"include:{required_include}"

    if not candidates:
        return SpfResult(
            status="missing",
            message="No SPF record was found. Publish one at your domain's apex.",
            suggested_value=f"v=spf1 {token} ~all",
        )

    if len(candidates) > 1:
        return SpfResult(
            status="multiple",
            message=(
                f"Your domain has {len(candidates)} SPF records; a domain may have only one. "
                "Merge them into one."
            ),
            suggested_value=_merge_spf(candidates, token),
        )

    record = candidates[0]
    parts = record.split()[1:]
    dead_target = _dead_include_in(parts)
    if dead_target is not None:
        # A known-dead include is a permerror for every receiver regardless of whether the
        # record also contains the include we actually need — so this is checked, and refused,
        # before the ordinary verified/mismatch question is even asked.
        cleaned = _drop_dead_includes([p for p in parts if not _ALL_MECHANISM.match(p)])
        if token.lower() not in {p.lower() for p in cleaned}:
            cleaned.append(token)
        qualifier = next((p for p in parts if _ALL_MECHANISM.match(p)), "~all")
        return SpfResult(
            status="mismatch",
            message=f"Remove include:{dead_target} — it points at a domain with no SPF record.",
            suggested_value=" ".join(["v=spf1", *cleaned, qualifier]),
        )

    mechanisms = {p.lower() for p in parts}
    if token.lower() in mechanisms:
        message = "Verified."
        count = _spf_lookup_count(record)
        if count > 10:
            message += (
                f" This record makes about {count} DNS lookups; RFC 7208 permits ten, so some "
                "receivers may fail it outright."
            )
        return SpfResult(status="verified", message=message, suggested_value=record)

    return SpfResult(
        status="mismatch",
        message="Edit your existing SPF record to this value — don't add a second one",
        suggested_value=_insert_before_all(record, token),
    )


# --------------------------------------------------------------------- DKIM and DMARC evaluation
#
# Neither DKIM record's actual value is ours to know — the key is minted by the provider, not by
# this platform, so what these check is existence and shape, never a specific published value.


def _contains(records: list[str], marker: str) -> bool:
    marker_lower = marker.lower()
    return any(marker_lower in r.lower() for r in records)


def evaluate_dkim_txt(records: list[str]) -> str:
    """Google's shape: a TXT record at the selector that names `v=DKIM1`. `mismatch` (not
    `missing`) when something is published there but it is not that — a customer who pasted the
    wrong value needs to know a value arrived at all."""
    if not records:
        return "missing"
    return "verified" if _contains(records, "v=DKIM1") else "mismatch"


def evaluate_dkim_smtp(txt_records: list[str], cname_records: list[str]) -> str:
    """A customer-run provider's shape: a TXT naming `v=DKIM1`, or a CNAME to wherever the
    provider hosts the record — either satisfies it, since providers publish DKIM both ways."""
    if _contains(txt_records, "v=DKIM1") or cname_records:
        return "verified"
    return "mismatch" if txt_records else "missing"


def evaluate_dmarc(records: list[str]) -> bool:
    """Whether any TXT at `_dmarc.<domain>` is a DMARC record. Recommended only — nothing folds
    this into `fold_domain_status`'s required list, so its absence never blocks a send."""
    return any(r.strip().lower().startswith("v=dmarc1") for r in records)


# --------------------------------------------------------------------- engage unsubscribe links
#
# A link in a marketing email that lets the recipient leave without signing in. The recipient
# has no account and no token of ours, so the link itself is the credential: an HMAC-SHA256
# proves we minted it. Anything that does not verify is refused before a single row is read — a
# forged link must not be able to suppress somebody else's address, nor learn whether that
# address is a contact.
#
# Two token shapes, and the difference between them is the whole reason a second one exists.
#
# **v1** — `<org uuid, 16 bytes>.<address, utf-8>.<mac, 32 bytes>`, base64url without padding —
# carried the address in the clear (well, unencrypted; base64 is not secrecy) inside the link
# itself. That was deliberate at the time: the recipient already knows their own address, so the
# link tells *them* nothing new. It missed who else reads a URL that travels over the public
# internet — every reverse proxy and gateway between here and the recipient's mailbox logs the
# request path by default, so the address ended up in infrastructure access logs this platform
# does not treat as holding PII, in cleartext, forever. `services._V1_UNSUBSCRIBE_SUNSET` is
# when this stops verifying; until then it has to, because mail already delivered still carries
# these links and a recipient's ability to leave must not regress.
#
# **v2** — `v2.<org uuid, 16 bytes>.<recipient uuid, 16 bytes>.<mac, 32 bytes>` — carries no
# address and no blind index, just two ids an access log can do nothing with on their own. The
# recipient id names one row in `campaign_recipients`, and the caller (`services.
# unsubscribe_recipient`) resolves the sealed address from *that row*, never from the token —
# the same sealed copy the row already held, reused rather than re-derived.

_UNSUBSCRIBE_MAX_LEN: Final = 1024


@dataclass(frozen=True, slots=True)
class UnsubscribeTarget:
    """What a verified token names. Exactly one of `email` (v1) or `recipient_id` (v2) is set —
    never both, never neither."""

    organization_id: uuid.UUID
    email: str | None = None
    recipient_id: uuid.UUID | None = None


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _unsubscribe_mac(organization_id: uuid.UUID, email: str, key: bytes) -> bytes:
    # A separator that cannot occur in either part, so ("ab", "c") and ("a", "bc") cannot
    # collide. The uuid is canonical text; the address is already normalised.
    message = f"engage.unsubscribe.v1\n{organization_id}\n{email}".encode()
    return hmac.new(key, message, hashlib.sha256).digest()


def _unsubscribe_mac_v2(organization_id: uuid.UUID, recipient_id: uuid.UUID, key: bytes) -> bytes:
    # A distinct context string from v1's ("v2" rather than "v1"), so a MAC minted for one
    # shape can never verify as the other even if the encoded bytes happened to coincide.
    message = f"engage.unsubscribe.v2\n{organization_id}\n{recipient_id}".encode()
    return hmac.new(key, message, hashlib.sha256).digest()


def make_unsubscribe_token(organization_id: uuid.UUID, email: str, key: bytes) -> str:
    """The v1 unsubscribe token. `email` must be normalised.

    Kept for the links already sent that `verify_unsubscribe_token` still has to accept (see the
    module docstring above) and for the tests that prove that acceptance. Every new link is
    minted with `make_unsubscribe_token_v2` instead — nothing in this service calls this to
    produce a link a customer will actually receive.
    """
    mac = _unsubscribe_mac(organization_id, email, key)
    return f"{_b64e(organization_id.bytes)}.{_b64e(email.encode())}.{_b64e(mac)}"


def make_unsubscribe_token_v2(
    organization_id: uuid.UUID, recipient_id: uuid.UUID, key: bytes
) -> str:
    """The unsubscribe token for one delivery. Opaque: decoding it reveals neither the
    recipient's address nor its blind index, just the two ids above."""
    mac = _unsubscribe_mac_v2(organization_id, recipient_id, key)
    return f"v2.{_b64e(organization_id.bytes)}.{_b64e(recipient_id.bytes)}.{_b64e(mac)}"


def verify_unsubscribe_token(token: str, key: bytes) -> UnsubscribeTarget | None:
    """The target `token` names, if it is one we minted, else None.

    Never raises on bad input — every malformed shape gets the same answer as a wrong MAC, so
    whoever sent it cannot tell "not base64" from "forged". The MAC comparison is constant-time.
    The v1/v2 sunset itself is not enforced here — this function is pure and has no clock — see
    `services.verify_unsubscribe_link`, the one place a route actually resolves a token.
    """
    if not token or len(token) > _UNSUBSCRIBE_MAX_LEN:
        return None
    parts = token.split(".")
    if len(parts) == 4 and parts[0] == "v2":
        return _verify_unsubscribe_token_v2(parts[1:], key)
    if len(parts) == 3:
        return _verify_unsubscribe_token_v1(parts, key)
    return None


def _verify_unsubscribe_token_v1(parts: list[str], key: bytes) -> UnsubscribeTarget | None:
    try:
        org_raw, email_raw, mac = (_b64d(p) for p in parts)
        organization_id = uuid.UUID(bytes=org_raw)
        email = email_raw.decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    expected = _unsubscribe_mac(organization_id, email, key)
    if not hmac.compare_digest(expected, mac):
        return None
    return UnsubscribeTarget(organization_id=organization_id, email=email)


def _verify_unsubscribe_token_v2(parts: list[str], key: bytes) -> UnsubscribeTarget | None:
    try:
        org_raw, recipient_raw, mac = (_b64d(p) for p in parts)
        organization_id = uuid.UUID(bytes=org_raw)
        recipient_id = uuid.UUID(bytes=recipient_raw)
    except (ValueError, binascii.Error):
        return None
    expected = _unsubscribe_mac_v2(organization_id, recipient_id, key)
    if not hmac.compare_digest(expected, mac):
        return None
    return UnsubscribeTarget(organization_id=organization_id, recipient_id=recipient_id)
