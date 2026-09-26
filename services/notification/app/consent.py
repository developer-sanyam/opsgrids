"""Consent: one ledger, one decision.

Before this module consent was split across two stores that never looked at each other.
`Preference` records opt-outs for rule-driven sends ("absence means send") and is checked when a
message is staged and again when it is claimed for dispatch; `Suppression` records unsubscribed,
bounced, complained and manual addresses and is checked only when a campaign selects and claims
its recipients. Neither said **who** agreed, **when**, or **through what** — and Meta requires an
opt-in for business-initiated WhatsApp messages, which the first store cannot even express.

`ConsentEvent` (`app/models.py`) is the append-only ledger that fixes the record; `decide` below
is the one function every send path asks. **The policy** — the latest event for `(organization,
channel, purpose, subject)` wins:

* **transactional** — allowed unless the latest event is `opted_out` (and, for email, unless a
  bounced or complained suppression exists: those are facts about the mailbox, not choices).
* **marketing** — allowed *only* if the latest event is `opted_in`. `opted_out` denies and so
  does **no event at all**: silence is not consent.
* A STOP keyword or an unsubscribe link opts the subject out of **both** purposes on that
  channel (`opt_out(..., purposes=BOTH_PURPOSES)`), so one word cannot leave a marketing opt-in
  standing behind it.
* **The legacy stores still count.** `decide` also denies when a `Preference` or a `Suppression`
  says no — deny if *either* says no — so nothing that was blocked before this ledger existed
  becomes allowed by it. A refusal in either store is also written through to the ledger
  (`services.set_preference`, `add_suppression`, STOP, unsubscribe), and the migration backfilled
  every row that already existed, so the ledger alone tells the same story; the legacy check is
  the belt over those braces, not a second opinion that could disagree.

Everything in the first half of this module is pure or a read. The writers below the divider are
the only way a row reaches the ledger from inside this service, and every one of them appends —
there is no update and no delete here, because the database has none to offer the app role.

`legacy_import` events (`consent_backfill.py`) are **unverified consent**: existing contacts
carried over so their campaigns keep going, with no record of how or when they agreed. See
`ConsentEvent`'s docstring and the README.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.config import settings
from app.models import ConsentEvent
from platform_core import pii
from platform_core.logging import get_logger

log = get_logger("notification.consent")

# --------------------------------------------------------------------- vocabulary

#: The channels the ledger tracks. `in_app` is deliberately absent: the platform's own feed has
#: no consent regime (`domain.IN_APP_CHANNEL`), and its "address" is a membership id.
CHANNELS: Final[tuple[str, ...]] = ("whatsapp", "email", "sms")

MARKETING: Final = "marketing"
TRANSACTIONAL: Final = "transactional"
PURPOSES: Final[tuple[str, ...]] = (MARKETING, TRANSACTIONAL)
#: STOP and unsubscribe refuse everything on the channel, so they write one row per purpose.
BOTH_PURPOSES: Final[tuple[str, ...]] = (MARKETING, TRANSACTIONAL)

OPTED_IN: Final = "opted_in"
OPTED_OUT: Final = "opted_out"
STATUSES: Final[tuple[str, ...]] = (OPTED_IN, OPTED_OUT)

#: What a person may claim through the API. Every other source is written only by this service
#: (see `SYSTEM_SOURCES`), so a caller cannot forge a customer's own STOP or a provider's bounce
#: — the reason `SuppressionIn` has no `reason` field either.
API_SOURCES: Final[tuple[str, ...]] = ("checkout", "web_form", "import", "admin", "api")
SOURCE_INBOUND_KEYWORD: Final = "inbound_keyword"
SOURCE_UNSUBSCRIBE_LINK: Final = "unsubscribe_link"
SOURCE_ADMIN: Final = "admin"
SOURCE_IMPORT: Final = "import"
SOURCE_BOUNCE: Final = "bounce"
SOURCE_COMPLAINT: Final = "complaint"
#: Unverified consent: an existing contact carried over when the ledger was introduced.
SOURCE_LEGACY_IMPORT: Final = "legacy_import"
SYSTEM_SOURCES: Final[tuple[str, ...]] = (
    SOURCE_INBOUND_KEYWORD,
    SOURCE_UNSUBSCRIBE_LINK,
    SOURCE_BOUNCE,
    SOURCE_COMPLAINT,
    SOURCE_LEGACY_IMPORT,
)

#: `Suppression.reason` values that are facts about the mailbox rather than a person's choice.
#: Only these refuse a *transactional* send: an address that unsubscribed from a newsletter still
#: has an invoice due, but one whose mailbox does not exist, or that reported us as spam, is not
#: reachable by anything.
HARD_SUPPRESSION_REASONS: Final = frozenset({"bounced", "complained"})

#: The `Rule.category` that makes a rule-driven send marketing. A provider template registered as
#: `marketing` does too (`purpose_for_send`), whatever the rule calls its own bucket.
MARKETING_CATEGORY: Final = "marketing"

# --------------------------------------------------------------------- reason codes

#: Why a decision came out the way it did. Stable strings: they are stored on the message or
#: recipient that was refused (`stored_reason`) and read by machines, so renaming one is a
#: contract change. Allowed reasons exist so a caller can log *why* it went ahead, too.
REASON_OPTED_IN: Final = "opted_in"
REASON_NO_OPT_OUT: Final = "no_opt_out"
REASON_OPTED_OUT: Final = "opted_out"
REASON_NO_OPT_IN: Final = "no_opt_in"
REASON_PREFERENCE: Final = "preference_suppressed"
REASON_INVALID_ADDRESS: Final = "invalid_address"
#: `suppressed_<Suppression.reason>` — `suppressed_unsubscribed`, `suppressed_bounced`, ...
REASON_SUPPRESSED_PREFIX: Final = "suppressed_"

#: Prefix on the value written to `Message.suppression_reason` / `CampaignRecipient.error`, so a
#: consent refusal is recognisable at a glance and by a `LIKE 'consent:%'`.
STORED_PREFIX: Final = "consent:"


@dataclass(frozen=True, slots=True)
class ConsentDecision:
    """The answer, and the machine-readable reason behind it."""

    allowed: bool
    reason: str


def stored_reason(decision: ConsentDecision) -> str:
    """The form a refusal is recorded in on a message or a campaign recipient."""
    return f"{STORED_PREFIX}{decision.reason}"


#: What a message suppressed by the legacy `Preference` check alone is stamped with — the same
#: shape as any other refusal, so `suppression_reason LIKE 'consent:%'` finds every one.
STORED_PREFERENCE: Final = f"{STORED_PREFIX}{REASON_PREFERENCE}"


def evaluate(
    *,
    purpose: str,
    ledger_status: str | None,
    suppression_reason: str | None,
    preference_blocked: bool,
) -> ConsentDecision:
    """**The policy**, as a pure function of what the stores said. See the module docstring.

    Refusals are named most-specific first — a hard bounce says more than "opted out", and a
    person's recorded refusal says more than a legacy preference row that merely agrees with it —
    but the order cannot change `allowed`: any refusal wins over any permission, which is the
    whole of "deny if either store says no".
    """
    if suppression_reason is not None and (
        purpose == MARKETING or suppression_reason in HARD_SUPPRESSION_REASONS
    ):
        return ConsentDecision(False, f"{REASON_SUPPRESSED_PREFIX}{suppression_reason}")
    if ledger_status == OPTED_OUT:
        return ConsentDecision(False, REASON_OPTED_OUT)
    if preference_blocked:
        return ConsentDecision(False, REASON_PREFERENCE)
    if purpose == MARKETING:
        if ledger_status == OPTED_IN:
            return ConsentDecision(True, REASON_OPTED_IN)
        return ConsentDecision(False, REASON_NO_OPT_IN)
    return ConsentDecision(
        True, REASON_OPTED_IN if ledger_status == OPTED_IN else REASON_NO_OPT_OUT
    )


def purposes_for_category(category: str) -> tuple[str, ...]:
    """Which purposes a legacy `Preference.category` refusal covers: `*` is every category, so
    both purposes; `marketing` is marketing; any other bucket (`billing`, `operational`, ...) is a
    rule-authored transactional category."""
    if category == domain.CATEGORY_ALL:
        return BOTH_PURPOSES
    if category == MARKETING_CATEGORY:
        return (MARKETING,)
    return (TRANSACTIONAL,)


# --------------------------------------------------------------------- subjects


@dataclass(frozen=True, slots=True)
class Subject:
    """Who a consent event is about, in the only forms this table may hold: sealed, masked, and
    the keyed blind index. `enc`/`masked` may be copied from another row that already holds the
    address (a contact, a suppression, a campaign recipient) — never opened on the way."""

    channel: str
    index: str
    enc: bytes
    masked: str


def normalize(channel: str, address: str) -> str:
    """The canonical form of an address for a ledger channel. `sms` shares WhatsApp's E.164 rule:
    both are phone numbers and a missing country code is refused rather than guessed at."""
    if channel not in CHANNELS:
        raise domain.RuleError(f"{channel!r} is not a channel the consent ledger tracks")
    return domain.normalize_recipient("email" if channel == "email" else "whatsapp", address)


def _pii_kind(channel: str) -> str:
    return "email" if channel == "email" else "phone"


def index_for(channel: str, address: str) -> str:
    """The blind index `address` is looked up under. Raises `domain.RuleError` for an address that
    is not one. The same index `Contact.email_index`, `Suppression.email_index` and
    `Conversation.wa_address_index` carry, so nothing is decrypted to find a match."""
    normalized = normalize(channel, address)
    return pii.blind_index(normalized, kind=_pii_kind(channel), settings=settings())


def subject_for(channel: str, address: str) -> Subject:
    """Seal and index `address` for a new ledger row. Raises `domain.RuleError` for an address
    that is not one."""
    normalized = normalize(channel, address)
    kind = _pii_kind(channel)
    cfg = settings()
    enc, masked = pii.seal(normalized, kind=kind, settings=cfg)
    return Subject(
        channel=channel,
        index=pii.blind_index(normalized, kind=kind, settings=cfg),
        enc=enc,
        masked=masked,
    )


# --------------------------------------------------------------------- the decision


async def _lookup(
    session: AsyncSession,
    organization_id: uuid.UUID,
    channel: str,
    purpose: str,
    indexes: Sequence[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """What the two index-keyed stores say about these subjects: the latest ledger status per
    index, and — for email, the only channel with a `Suppression` store — the suppression reason.
    Two reads however many subjects there are."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown consent channel {channel!r}")
    if purpose not in PURPOSES:
        raise ValueError(f"unknown consent purpose {purpose!r}")
    unique = list(dict.fromkeys(indexes))
    if not unique:
        return {}, {}
    latest = await selectors.latest_consent_status(
        session, organization_id, channel, purpose, unique
    )
    suppressions = (
        await selectors.suppression_reasons(session, organization_id, unique)
        if channel == "email"
        else {}
    )
    return latest, suppressions


async def decide_indexes(
    session: AsyncSession,
    organization_id: uuid.UUID,
    channel: str,
    purpose: str,
    indexes: Sequence[str],
) -> dict[str, ConsentDecision]:
    """`decide`, for many subjects at once, by blind index — the bulk shape a campaign needs: two
    reads for a whole batch rather than a pair per recipient.

    Honours the ledger and, for email, the `Suppression` store (keyed by the same index). It
    cannot honour a `Preference`, whose recipient is stored in the clear and so cannot be matched
    by index; `decide`, which is handed the address, does. A `Preference` refusal is mirrored into
    the ledger when it is written and was backfilled when the ledger was introduced, so the bulk
    answer does not depend on that check — and the bulk path never consulted `Preference` before,
    so nothing that was blocked there becomes allowed.
    """
    latest, suppressions = await _lookup(session, organization_id, channel, purpose, indexes)
    return {
        index: evaluate(
            purpose=purpose,
            ledger_status=latest.get(index),
            suppression_reason=suppressions.get(index),
            preference_blocked=False,
        )
        for index in dict.fromkeys(indexes)
    }


async def decide(
    session: AsyncSession,
    organization_id: uuid.UUID,
    channel: str,
    purpose: str,
    address: str,
    *,
    category: str | None = None,
) -> ConsentDecision:
    """**May we send `purpose` to `address` on `channel`?** The one question every send path asks.

    `category` is the rule's bucket (`billing`, `marketing`, ...) and only matters to the legacy
    `Preference` check, whose refusals are per category: a preference for `billing` refuses a
    billing send and nothing else. Omitted, only a `*` preference (and, for marketing, a
    `marketing` one) is consulted.

    An address that is not one for the channel is refused rather than raised: a send to nowhere
    is not a permission, and the caller has a decision to record either way.
    """
    try:
        normalized = normalize(channel, address)
    except domain.RuleError:
        if channel not in CHANNELS:
            raise ValueError(f"unknown consent channel {channel!r}") from None
        return ConsentDecision(False, REASON_INVALID_ADDRESS)
    index = pii.blind_index(normalized, kind=_pii_kind(channel), settings=settings())

    latest, suppressions = await _lookup(session, organization_id, channel, purpose, [index])
    preferences = await selectors.suppressions_for(session, organization_id, normalized)
    preference_category = category or (MARKETING_CATEGORY if purpose == MARKETING else "")
    return evaluate(
        purpose=purpose,
        ledger_status=latest.get(index),
        suppression_reason=suppressions.get(index),
        preference_blocked=domain.is_suppressed(
            preferences, channel=channel, category=preference_category
        ),
    )


async def purpose_for_send(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    channel: str,
    category: str,
    provider_template_code: str | None,
) -> str:
    """Marketing when the rule says so, or when the provider template it sends is registered as
    marketing — Meta's own classification, which is what actually carries the opt-in obligation
    and which a rule's free-text `category` bucket can neither add to nor waive."""
    if category == MARKETING_CATEGORY:
        return MARKETING
    if provider_template_code:
        template = await selectors.get_provider_template(
            session, organization_id, provider_template_code, channel
        )
        if template is not None and template.category == MARKETING_CATEGORY:
            return MARKETING
    return TRANSACTIONAL


async def check_send(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    channel: str,
    category: str,
    address: str,
    provider_template_code: str | None = None,
) -> ConsentDecision | None:
    """The decision for one staged or claimed message, or `None` when its channel has no consent
    regime (`in_app`). The call `stage_message` and `claim_for_send` make, so the two can never
    derive a purpose differently."""
    if channel not in CHANNELS:
        return None
    purpose = await purpose_for_send(
        session,
        organization_id,
        channel=channel,
        category=category,
        provider_template_code=provider_template_code,
    )
    decision = await decide(session, organization_id, channel, purpose, address, category=category)
    if not decision.allowed:
        log.info(
            "notification.consent.denied",
            organization_id=str(organization_id),
            channel=channel,
            purpose=purpose,
            reason=decision.reason,
        )
    return decision


# ===================================================================== writers
#
# Everything below appends. There is no function here — and no privilege at the database — that
# updates or deletes a ledger row.


def _row(
    *,
    organization_id: uuid.UUID,
    subject: Subject,
    purpose: str,
    status: str,
    source: str,
    evidence: str | None,
    recorded_by: uuid.UUID | None,
    contact_id: uuid.UUID | None,
    occurred_at: datetime,
    idempotency_key: str | None,
) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "organization_id": organization_id,
        "channel": subject.channel,
        "purpose": purpose,
        "subject_enc": subject.enc,
        "subject_masked": subject.masked,
        "subject_index": subject.index,
        "contact_id": contact_id,
        "status": status,
        "source": source,
        "evidence": evidence,
        "recorded_by": recorded_by,
        "occurred_at": occurred_at,
        # Set here rather than left to the column's server default: rows appended in one
        # transaction would otherwise share `now()` and tie on the ordering key.
        "created_at": datetime.now(UTC),
        "idempotency_key": idempotency_key,
    }


async def _insert(session: AsyncSession, rows: list[dict[str, object]]) -> list[ConsentEvent]:
    """One `INSERT` for any number of rows. A row whose `idempotency_key` is already taken is
    skipped, so what comes back is what was actually written."""
    if not rows:
        return []
    stmt = (
        pg_insert(ConsentEvent)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["organization_id", "idempotency_key"],
            index_where=ConsentEvent.idempotency_key.is_not(None),
        )
        .returning(ConsentEvent)
    )
    return list((await session.execute(stmt)).scalars())


async def append(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    subject: Subject,
    purposes: Sequence[str],
    status: str,
    source: str,
    evidence: str | None = None,
    recorded_by: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> list[ConsentEvent]:
    """Append one event per purpose, in the caller's transaction. Returns the rows actually
    written — fewer than asked when an `idempotency_key` had already been used.

    With several purposes and a key, each row gets `"<key>:<purpose>"`, so the key stays unique
    per row and a retry of the whole call still writes nothing twice.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown consent status {status!r}")
    when = occurred_at or datetime.now(UTC)
    rows = [
        _row(
            organization_id=organization_id,
            subject=subject,
            purpose=purpose,
            status=status,
            source=source,
            evidence=evidence,
            recorded_by=recorded_by,
            contact_id=contact_id,
            occurred_at=when,
            idempotency_key=(
                None
                if idempotency_key is None
                else idempotency_key
                if len(purposes) == 1
                else f"{idempotency_key}:{purpose}"
            ),
        )
        for purpose in purposes
    ]
    return await _insert(session, rows)


async def opt_out(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    subject: Subject,
    purposes: Sequence[str],
    source: str,
    evidence: str | None = None,
    recorded_by: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
) -> list[ConsentEvent]:
    """Record a refusal for every purpose whose latest event is not already one.

    Idempotent by state rather than by key: a repeat STOP, or a second click on an unsubscribe
    link, adds nothing to a history that already ends in `opted_out` — but a click *after* an
    intervening opt-in still counts, which a uniqueness key on the link could not tell apart.
    """
    latest = await selectors.latest_consent_status_by_purpose(
        session, organization_id, subject.channel, subject.index, purposes
    )
    todo = [p for p in purposes if latest.get(p) != OPTED_OUT]
    return await append(
        session,
        organization_id=organization_id,
        subject=subject,
        purposes=todo,
        status=OPTED_OUT,
        source=source,
        evidence=evidence,
        recorded_by=recorded_by,
        contact_id=contact_id,
    )


async def restore(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    subject: Subject,
    purposes: Sequence[str],
    source: str,
    evidence: str | None = None,
    recorded_by: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
) -> list[ConsentEvent]:
    """Reverse an opt-out — **restoring consent that existed, never creating consent that did not.**

    For each purpose whose latest event is `opted_out`: `transactional` is re-opened (its default
    is "allowed", so lifting the refusal is all there is to record), but `marketing` is re-opened
    only if the subject had opted in *before* — a START keyword, or an operator lifting a
    preference or a manual suppression, undoes a refusal; it is not the affirmative, specific
    opt-in Meta requires for a business-initiated marketing message, and must not be allowed to
    stand in for one. A subject with no earlier marketing opt-in stays refused for marketing.
    """
    latest = await selectors.latest_consent_status_by_purpose(
        session, organization_id, subject.channel, subject.index, purposes
    )
    opted_in_before = await selectors.ever_opted_in_purposes(
        session, organization_id, subject.channel, subject.index
    )
    todo = [
        purpose
        for purpose in purposes
        if latest.get(purpose) == OPTED_OUT and (purpose != MARKETING or purpose in opted_in_before)
    ]
    return await append(
        session,
        organization_id=organization_id,
        subject=subject,
        purposes=todo,
        status=OPTED_IN,
        source=source,
        evidence=evidence,
        recorded_by=recorded_by,
        contact_id=contact_id,
    )


@dataclass(frozen=True, slots=True)
class ContactSubject:
    """A newly added contact, for `attest_opt_ins`."""

    subject: Subject
    contact_id: uuid.UUID


async def attest_opt_ins(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    contacts: Sequence[ContactSubject],
    source: str,
    evidence: str,
    recorded_by: uuid.UUID | None,
) -> list[ConsentEvent]:
    """Record the organization's own attestation for contacts an operator has just added: email
    marketing, `opted_in`, sourced to how they were added and attributed to who added them.

    **Only where the ledger is silent.** A subject with any earlier marketing event — a customer's
    own unsubscribe, an operator's opt-out, a legacy suppression — keeps that history untouched:
    re-adding an address to the audience is not how a refusal gets undone. And it is an
    *attestation*, not evidence: adding a contact says the organization believes it may email
    them, and nothing here checks. That keeps "add a contact, then send a campaign" working as it
    always has, while recording who vouched for it.
    """
    if not contacts:
        return []
    known = await selectors.subjects_with_marketing_events(
        session, organization_id, "email", [c.subject.index for c in contacts]
    )
    now = datetime.now(UTC)
    rows: list[dict[str, object]] = []
    for item in contacts:
        if item.subject.index in known:
            continue
        known.add(item.subject.index)  # two rows for one address in one batch write one event
        rows.append(
            _row(
                organization_id=organization_id,
                subject=item.subject,
                purpose=MARKETING,
                status=OPTED_IN,
                source=source,
                evidence=evidence,
                recorded_by=recorded_by,
                contact_id=item.contact_id,
                occurred_at=now,
                idempotency_key=None,
            )
        )
    return await _insert(session, rows)
