"""Pure rules for the lead service. No I/O, no session — model instances and plain values in,
plain values out, so the two things worth getting exactly right (the lifecycle, the rotation)
are trivially testable in isolation from the database.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

# ------------------------------------------------------------------ the lifecycle

#: Every status, in the order a lead normally visits them. `converted` is reached only through
#: `:convert`, never through a plain status patch — see `can_transition`.
STATUS_ORDER: tuple[str, ...] = ("new", "working", "qualified", "disqualified", "converted")

#: Terminal: nothing moves out of these through an ordinary status change. `disqualified` can
#: be reopened (a rep changes their mind) — `qualified` is where that happens from — so it is
#: deliberately not terminal here despite reading like an ending.
TERMINAL_STATUSES: frozenset[str] = frozenset({"converted"})

#: The legal forward/backward moves for `PATCH .../status`. `converted` has no entry: it is
#: reached exclusively by `services.convert_lead`, which sets it directly, so a plain status
#: patch can never claim a lead was converted without the company and deal that implies.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"working", "disqualified"}),
    "working": frozenset({"qualified", "disqualified", "new"}),
    "qualified": frozenset({"disqualified", "working"}),
    "disqualified": frozenset({"new", "working"}),
    "converted": frozenset(),
}


def can_transition(current: str, new: str) -> bool:
    """Whether a plain status change may move `current` -> `new`.

    `converted` is reachable from nowhere through this function — see `_TRANSITIONS` — and a
    lead already `converted` accepts no further status change at all, matching `TERMINAL_STATUSES`.
    """
    if current not in STATUS_ORDER or new not in STATUS_ORDER:
        return False
    if current in TERMINAL_STATUSES:
        return False
    if new == current:
        return False
    return new in _TRANSITIONS.get(current, frozenset())


def can_convert(status: str) -> bool:
    """Only a qualified lead may convert. Not `new` or `working` — conversion creates a real
    company and deal, and doing that from an unqualified lead is exactly the noise a pipeline
    stage exists to keep out (ecosystem-gaps.md §2). Not `disqualified` — reopen it first. A
    lead already `converted` is handled by the caller as the idempotent-replay case, not here."""
    return status == "qualified"


# ------------------------------------------------------------------ phone canonicalization

#: E.164: a leading `+`, a non-zero country digit, up to fifteen digits in total. Duplicated
#: from `customer`'s own `_canonical_e164` rather than shared — the same few deliberately
#: duplicated lines `customer`'s own docstring already explains for its copy from
#: notification-service. Validating **here, at capture**, is what keeps a malformed number out
#: of the sealed column in the first place, rather than surfacing only later, at `:convert`,
#: when customer-service tries to canonicalise its own copy and the failure is a service-to-
#: service 4xx instead of an ordinary 422 on the form that took the bad input.
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def canonical_e164(raw: str) -> str | None:
    """Normalise a supplied number, or return `None` when it cannot be normalised.

    Never raises, and never echoes the input anywhere — this is a pure function and leaves the
    decision of *how* to fail (a 422, its wording, whether to log) to the caller, which is the
    layer that knows whether the input is something a client typed moments ago (safe to quote
    back) or a value that arrived over the network from another tenant's data (never is).
    """
    compact = re.sub(r"[\s\-().]", "", raw.strip())
    if not compact.startswith("+"):
        return None
    if not _E164.match(compact):
        return None
    return compact


# ------------------------------------------------------------------ conversion claim lease

#: How long a conversion claim holds before a second attempt may take it over rather than
#: being refused outright. Long enough for both upstream calls to finish at their configured
#: timeouts; short enough that a genuinely crashed attempt does not block a retry for long.
#: `services.claim_conversion` is the only writer of the claim fields this reads.
CONVERSION_LEASE_SECONDS = 60


def claim_is_live(claimed_at: datetime | None, *, now: datetime) -> bool:
    """Whether an existing conversion claim is still within its lease and must be respected
    (a 409, not a takeover). `None` — never claimed, or already cleared — is never live."""
    if claimed_at is None:
        return False
    return (now - _aware(claimed_at)) < timedelta(seconds=CONVERSION_LEASE_SECONDS)


def _aware(dt: datetime) -> datetime:
    """Treat a naive timestamp as UTC so a comparison never raises — the same guard deal-
    service's own `domain.py` uses, for the same reason."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# ------------------------------------------------------------------ round-robin assignment


# ------------------------------------------------------------------ conversion ids

#: Fixed, arbitrary namespace for `conversion_ids` — any stable UUID works; what matters is
#: that it never changes, because a change would make every already-converted lead's ids
#: unreproducible from its own id.
_CONVERSION_NAMESPACE = uuid.UUID("6f2e6f3a-6d1e-4b6a-9c8e-2f7a5b1d4c90")


def conversion_ids(
    lead_id: uuid.UUID, generation: int = 0
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """The `(company_id, contact_id, deal_id)` a lead converts into — deterministic from the
    lead's own id, so calling `:convert` twice computes the *same* three ids both times.

    This is the whole idempotency mechanism (ADR-0017's "same lead converted twice -> one
    company, one deal"): customer-service and deal-service each check for a row already
    holding the id they were sent before creating one, so a retried conversion reuses rows
    instead of duplicating them. `uuid5` rather than a stored counter or a random id — no
    coordination needed, and the ids are reproducible from `lead_id` alone even if the first
    attempt's response was lost before this service saw it.

    `generation` is the one stored counter, and it exists for exactly one case: a record an
    earlier attempt created was deleted afterwards, so its deterministic id can never be
    reused (`Lead.conversion_generation`). Generation `0` derives exactly the ids this
    function always has — nothing already handed out changes — and each later generation is a
    fresh, equally reproducible set.
    """
    suffix = "" if generation == 0 else f":{generation}"
    return (
        uuid.uuid5(_CONVERSION_NAMESPACE, f"{lead_id}:company{suffix}"),
        uuid.uuid5(_CONVERSION_NAMESPACE, f"{lead_id}:contact{suffix}"),
        uuid.uuid5(_CONVERSION_NAMESPACE, f"{lead_id}:deal{suffix}"),
    )


# ------------------------------------------------------------------ deal naming


#: deal-service's own `Deal.name` column is `String(200)` — a long company name and a long
#: lead name together can exceed that, and an unbounded `f"{company} — {full_name}"` would
#: reach deal-service's `/internal/v1` only to bounce off its own 422, turning a perfectly
#: convertible lead into an upstream rejection over nothing but a name.
DEAL_NAME_MAX_LENGTH = 200
_TRUNCATION_MARK = "…"


def deal_name(company: str, full_name: str, lead_id: uuid.UUID) -> str:
    """The name `:convert` gives the deal it creates for `lead_id` — bounded to fit
    deal-service's column, and suffixed with a short, fixed-width slice of the lead's own id
    so two different leads naming the same company and the same person still produce two
    distinguishable deal names instead of one that silently reads as a single duplicate."""
    suffix = f" · {str(lead_id)[:8]}"
    budget = DEAL_NAME_MAX_LENGTH - len(suffix)
    base = f"{company} — {full_name}"
    if len(base) > budget:
        base = base[: budget - len(_TRUNCATION_MARK)].rstrip() + _TRUNCATION_MARK
    return f"{base}{suffix}"


def next_owner(owner_ids: Sequence[uuid.UUID], cursor: int) -> tuple[uuid.UUID | None, int]:
    """The owner a round-robin rule hands the next lead, and the cursor to store afterwards.

    Returns `(None, cursor)` unchanged when the rule has no owners — an empty rotation
    assigns nobody rather than raising, so a lead can still be created before an organization
    has configured its rep list. Otherwise wraps with modulo, so a cursor that has drifted
    past the list's current length (an owner was removed) still lands on a valid slot instead
    of raising.
    """
    if not owner_ids:
        return None, cursor
    index = cursor % len(owner_ids)
    return owner_ids[index], index + 1
