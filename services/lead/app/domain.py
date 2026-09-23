"""Pure rules for the lead service. No I/O, no session — model instances and plain values in,
plain values out, so the two things worth getting exactly right (the lifecycle, the rotation)
are trivially testable in isolation from the database.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

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


# ------------------------------------------------------------------ round-robin assignment


# ------------------------------------------------------------------ conversion ids

#: Fixed, arbitrary namespace for `conversion_ids` — any stable UUID works; what matters is
#: that it never changes, because a change would make every already-converted lead's ids
#: unreproducible from its own id.
_CONVERSION_NAMESPACE = uuid.UUID("6f2e6f3a-6d1e-4b6a-9c8e-2f7a5b1d4c90")


def conversion_ids(lead_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """The `(company_id, contact_id, deal_id)` a lead converts into — deterministic from the
    lead's own id, so calling `:convert` twice computes the *same* three ids both times.

    This is the whole idempotency mechanism (ADR-0017's "same lead converted twice -> one
    company, one deal"): customer-service and deal-service each check for a row already
    holding the id they were sent before creating one, so a retried conversion reuses rows
    instead of duplicating them. `uuid5` rather than a stored counter or a random id — no
    coordination needed, and the ids are reproducible from `lead_id` alone even if the first
    attempt's response was lost before this service saw it.
    """
    return (
        uuid.uuid5(_CONVERSION_NAMESPACE, f"{lead_id}:company"),
        uuid.uuid5(_CONVERSION_NAMESPACE, f"{lead_id}:contact"),
        uuid.uuid5(_CONVERSION_NAMESPACE, f"{lead_id}:deal"),
    )


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
