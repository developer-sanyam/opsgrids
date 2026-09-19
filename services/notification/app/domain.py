"""Pure notification logic. No I/O, no session, no network.

Everything here is a function of its arguments, which is what makes the rules that matter
testable without a database: strict rendering, recipient resolution, suppression, and the
retry decision. `services.py` and `dispatcher.py` do the I/O around them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

#: The channels this service can actually reach. A rule naming anything else is refused at
#: write time rather than discovered when a message fails to send.
CHANNELS: Final[tuple[str, ...]] = ("email", "whatsapp")

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
    "catalog.plan.retired",
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
    "operations.inventory.item.created",
    "operations.inventory.warehouse.created",
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
    invoice.
    """
    value = raw.strip()
    if not value:
        raise RuleError("Recipient is empty")

    if channel == "email":
        value = value.lower()
        if not _EMAIL.match(value):
            raise RuleError(f"{raw!r} is not an email address")
        return value

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
