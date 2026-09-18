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
STATE_FAILED: Final = "failed"
STATE_SUPPRESSED: Final = "suppressed"

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
    if recipient_path and recipient_literal:
        raise RuleError("A rule sets recipient_path or recipient_literal, not both")
    if not recipient_path and not recipient_literal:
        raise RuleError("A rule needs a recipient: recipient_path or recipient_literal")
    if recipient_literal:
        # Validated now so a literal typo is a 422 on the rule, not a failed send later.
        normalize_recipient(channel, recipient_literal)


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
