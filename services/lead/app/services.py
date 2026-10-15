"""Writes for lead.

Every function follows the platform shape: change the row, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens the transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one
without the other, and a rolled-back write never leaves an audit row claiming it happened.
Events are staged into the outbox, never published inline (ADR-0000).

`convert_lead` is the one exception to "one transaction" here, deliberately: it is claim, call,
record — three steps, three transactions, with the two calls to customer's and deal's
`/internal/v1` running with **no transaction open** between them. `claim_conversion`,
`finalize_conversion` and `fail_conversion` each open and commit their own; see `convert_lead`'s
own docstring for why holding a row lock (or worse, a whole request's transaction) across two
network calls was the bug this shape replaces.

`set_assignment_rule`, `create_lead` and `update_lead`'s explicit-`owner_id` validation
(`_ensure_owners_active`) are a smaller, accepted version of the same trade-off: they call
customer-service's `owners:validate` from inside the request's one transaction, holding it
open for the round trip. Unlike `:convert`, none of these sits on a hot path a rep repeats
constantly, and none of them opens a sealed value first — a live database transaction blocked
on a single fast internal call from a low-frequency write is a smaller cost than the extra
transaction-splitting `:convert` needed to keep decrypted PII off an open connection.

Round-robin assignment (`_resolve_round_robin_snapshot`/`_assign_owner`) does **not** take
that trade — a lead capture with no explicit owner is exactly the hot path the paragraph above
distinguishes itself from, and the row it would otherwise hold locked across the same network
call is the *whole organization's* rotation, not this one write's own row. Its owner-directory
call runs against a plain, unlocked read, before `create_lead` ever locks the assignment rule;
see `_resolve_round_robin_snapshot`'s own docstring.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, NamedTuple

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import clients, domain, selectors
from app.config import settings
from app.models import AssignmentRule, AuditLog, Lead, OutboxEvent
from app.schemas import LeadPatch, RevealField
from platform_core.audit import record_audit, record_sensitive_read
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.cache import get_redis
from platform_core.db import assert_tenant, constraint_name, set_tenant
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    RateLimitedProblem,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.events import actor_for, publish_event
from platform_core.logging import get_logger
from platform_core.models import utcnow
from platform_core.pii import PiiError, blind_index, open_sealed, seal

log = get_logger("lead.services")

# Event names. `crm.*` because these are the product's own, not the platform's.
EVT_LEAD_CREATED = "crm.lead.created"
EVT_LEAD_UPDATED = "crm.lead.updated"
EVT_LEAD_CONVERTED = "crm.lead.converted"
#: Staged by `_stage_conversion_aborted`, and only there: by `finalize_conversion` when it
#: refuses to write "converted" (`status_changed`, `owner_changed`: the records customer and deal
#: already created for the attempt are still there) and by `fail_conversion` when it bumps the
#: generation because customer reported a record an earlier attempt created was deleted
#: (`record_deleted`). The payload names the attempt's `generation` and `claim_id`: a consumer
#: re-derives the attempt's ids from `(lead_id, generation)` with `domain.conversion_ids` (R2.4).
EVT_LEAD_CONVERSION_ABORTED = "crm.lead.conversion_aborted"

#: How far out a converted lead's deal defaults to closing. A lead carries no close date of
#: its own (ecosystem-gaps.md §2 — that is exactly why it is not modelled as a deal), so
#: `:convert` has to pick something; thirty days is deal-service's own board's typical early
#: pipeline horizon, and a rep changes it on the deal the moment they have a real one.
_DEFAULT_CLOSE_HORIZON_DAYS = 30


async def _bind_tenant(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Bind a transaction to the caller's organization — the same tenancy chokepoint
    `get_tenant_session` applies for every other route's one transaction, done by hand here
    because `:convert`'s claim/call/record shape needs three separate transactions on one
    session rather than the single one `get_tenant_session` opens for the whole request.
    Must run as the first statement inside each transaction."""
    await set_tenant(session, organization_id)
    if not settings().is_production:
        await assert_tenant(session, organization_id)


async def _ensure_owners_active(organization_id: uuid.UUID, owner_ids: list[uuid.UUID]) -> None:
    """lead-service's own gate for an `owner_id` it is about to write — ask customer-service's
    `owners:validate` (restricted to lead-service alone) which of `owner_ids` are **not**
    active reps, and refuse if any are. Called before an explicit `owner_id` on a lead
    create/patch, and before accepting a round-robin owner list — an inactive or unknown rep
    never enters lead-service's own writes in the first place, matching the same refusal
    customer's own `_assert_owner_assignable` already gives every write there.
    """
    if not owner_ids:
        return
    cfg = settings()
    try:
        invalid = await clients.invalid_owner_ids(cfg, organization_id, owner_ids)
    except clients.OwnersValidateUnavailable as exc:
        raise UpstreamUnavailableProblem(
            "Could not confirm the owner directory. Try again shortly."
        ) from exc
    if invalid:
        raise ValidationProblem(
            "One or more owners are not active reps in this organization's CRM book"
        )


# ------------------------------------------------------------------ assignment rule


async def _get_or_create_rule(session: AsyncSession, ctx: ScopeContext) -> AssignmentRule:
    """The organization's rule, row-locked, creating an empty (unconfigured) one on first
    touch so a lead can always be created — an org with no rule configured yet simply assigns
    nobody.

    `INSERT ... ON CONFLICT DO NOTHING` rather than "check then insert": two concurrent first
    writes for the same organization racing a plain check-then-insert would both see no rule
    and both try to create one, and the loser would hit `uq_assignment_rules_org` as an
    unhandled `IntegrityError` instead of quietly finding the row the winner just created.
    """
    stmt = (
        pg_insert(AssignmentRule)
        .values(organization_id=ctx.organization_id, created_by=str(ctx.principal.sub))
        .on_conflict_do_nothing(index_elements=["organization_id"])
    )
    await session.execute(stmt)
    rule = await selectors.get_assignment_rule_for_update(session, ctx.organization_id)
    if rule is None:
        # Unreachable except by a bug in the statement above: the insert either created the
        # row or a concurrent one already had, and either way a row now exists to lock.
        raise RuntimeError(f"assignment rule for {ctx.organization_id} could not be created")
    return rule


async def set_assignment_rule(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    mode: str,
    owner_ids: list[uuid.UUID],
) -> AssignmentRule:
    """Configure the organization's round-robin. ORG-scoped only: this is workspace
    configuration, not any one rep's record, so an OWN-scoped `crm.lead.lead.write` grant is
    refused outright rather than silently allowed to reconfigure everyone's rotation. Every
    id in `owner_ids` must be a known, active rep (`_ensure_owners_active`) — the rule is
    refused rather than saved with an inactive owner already inside it."""
    if ctx.scope is not Scope.ORG:
        raise ForbiddenProblem("Configuring lead assignment requires ORG scope")
    await _ensure_owners_active(ctx.organization_id, owner_ids)

    rule = await _get_or_create_rule(session, ctx)
    before = {"mode": rule.mode, "owner_ids": [str(o) for o in rule.owner_ids]}
    rule.mode = mode
    rule.owner_ids = list(owner_ids)
    # Reset on reconfigure: a cursor from the old list has no meaningful position in a new
    # one, and starting over at the front is simpler to reason about than trying to carry a
    # fractional position across an edit.
    rule.cursor = 0
    rule.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="assignment_rule.updated",
        entity="assignment_rule",
        entity_id=rule.id,
        before=before,
        after={"mode": mode, "owner_ids": [str(o) for o in owner_ids]},
    )
    return rule


#: How long `_active_owner_ids_for_assignment` trusts its last *confirmed* answer for a given
#: (organization, exact owner set) before asking customer-service again. A lead capture is a
#: hot path in a way an explicit `owner_id` write or a rule reconfiguration is not, so this
#: exists to spare customer-service a round trip per lead rather than per minute; see that
#: function's own docstring for the trade this makes.
_ASSIGNMENT_ACTIVE_CACHE_TTL_SECONDS = 60

#: How long a *failed* attempt to reach the directory is remembered, separately from a
#: confirmed answer's own TTL above. Short, on purpose: long enough that a burst of lead
#: captures during a genuine outage does not each wait out their own timeout against a
#: customer-service that is still down, short enough that service coming back is noticed soon
#: after.
_ASSIGNMENT_UNAVAILABLE_CACHE_TTL_SECONDS = 15

#: Process-local, not shared across workers or restarts — a stale entry only ever means a
#: newly-inactive owner keeps a slot in the rotation for up to a minute longer than it should,
#: never the other direction (an active owner wrongly skipped), so a cold cache or a
#: multi-process deployment degrades to "revalidate more often", not to an incorrect answer.
#: The oldest a *confirmed* answer may be and still stand in for a directory that cannot be
#: asked right now. Ten minutes: long enough to ride out an ordinary customer-service restart
#: or deploy, short enough that "we last heard this rep was active" is not an answer about a
#: rep who was deactivated an hour ago — past it, nobody is assigned until the directory
#: answers again.
_ASSIGNMENT_STALE_LIMIT_SECONDS = 600


class _ConfirmedActive(NamedTuple):
    """One confirmed answer from the directory: which owner set it was for, which of them were
    active, until when it is fresh (`expires_at`, the ordinary TTL), and when it was actually
    heard (`confirmed_at`, what `_last_confirmed_active`'s staleness limit measures)."""

    key: frozenset[uuid.UUID]
    active: set[uuid.UUID]
    expires_at: float
    confirmed_at: float


_assignment_active_cache: dict[uuid.UUID, _ConfirmedActive] = {}

#: Which (organization, owner set) the directory most recently refused to answer, and until
#: when that failure is still remembered — `_active_owner_ids_for_assignment`'s own circuit
#: breaker, so a hanging customer-service costs one timeout per org per
#: `_ASSIGNMENT_UNAVAILABLE_CACHE_TTL_SECONDS`, not one per lead captured in that window.
_assignment_unavailable_cache: dict[uuid.UUID, tuple[frozenset[uuid.UUID], float]] = {}


def _last_confirmed_active(organization_id: uuid.UUID, key: frozenset[uuid.UUID]) -> set[uuid.UUID]:
    """The most recent *confirmed* answer for this exact owner set, past its ordinary TTL but
    no older than `_ASSIGNMENT_STALE_LIMIT_SECONDS` — used only when the directory cannot be
    asked again right now. Never a value for a *different* owner set: a reconfigure
    invalidates the cache the same way it always has, because a different set never matches
    here either. An answer older than the limit is ignored (empty set: nobody assigned, and a
    log line saying why) rather than trusted about reps who may have left since."""
    cached = _assignment_active_cache.get(organization_id)
    if cached is None or cached.key != key:
        return set()
    if time.monotonic() - cached.confirmed_at > _ASSIGNMENT_STALE_LIMIT_SECONDS:
        log.warning(
            "lead.assignment.confirmed_answer_too_old", organization_id=str(organization_id)
        )
        return set()
    return cached.active


async def _active_owner_ids_for_assignment(
    organization_id: uuid.UUID, owner_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of `owner_ids` are still active reps, specifically for round-robin assignment.

    `set_assignment_rule` already refuses to *save* a rule naming an inactive or unknown
    owner, but a rep configured as active then deactivated later stays in the saved list
    forever — nothing revalidates it again until this. Cached up to
    `_ASSIGNMENT_ACTIVE_CACHE_TTL_SECONDS` per exact `(organization, owner set)` pair, so a
    lead captured every few seconds costs one customer-service round trip a minute, not one
    per lead; the cache key naturally invalidates itself the moment `owner_ids` changes (a
    reconfigure, an owner added or removed), because a different set never matches a cached
    one.

    Fails **closed** on `OwnersValidateUnavailable` (reversed from this function's first
    version, which failed open): an owner this function cannot actually confirm is active is
    never handed out. Falls back to the last *confirmed* answer for this exact set if one
    exists and is no older than `_ASSIGNMENT_STALE_LIMIT_SECONDS`, and to an empty set —
    nobody assigned, `_assign_owner`'s own all-inactive path — if there is none or it is
    older than that. A failure is itself remembered for
    `_ASSIGNMENT_UNAVAILABLE_CACHE_TTL_SECONDS`, so a customer-service outage costs this
    function one attempt per organization per that window, not one per lead captured in it —
    the same reasoning `_ASSIGNMENT_ACTIVE_CACHE_TTL_SECONDS` already gives a healthy answer,
    just shorter, because a recovering directory should be noticed sooner than that.

    Must never run with the assignment rule's row locked — see `create_lead`'s own call site.
    """
    key = frozenset(owner_ids)
    now = time.monotonic()

    cached = _assignment_active_cache.get(organization_id)
    if cached is not None and cached.key == key and now < cached.expires_at:
        return cached.active

    unavailable = _assignment_unavailable_cache.get(organization_id)
    if unavailable is not None and unavailable[0] == key and now < unavailable[1]:
        log.warning(
            "lead.assignment.owner_directory_unreachable",
            organization_id=str(organization_id),
            cached=True,
        )
        return _last_confirmed_active(organization_id, key)

    cfg = settings()
    try:
        invalid = set(await clients.invalid_owner_ids(cfg, organization_id, list(owner_ids)))
    except clients.OwnersValidateUnavailable:
        log.warning(
            "lead.assignment.owner_directory_unreachable",
            organization_id=str(organization_id),
            cached=False,
        )
        _assignment_unavailable_cache[organization_id] = (
            key,
            now + _ASSIGNMENT_UNAVAILABLE_CACHE_TTL_SECONDS,
        )
        return _last_confirmed_active(organization_id, key)

    active = set(owner_ids) - invalid
    _assignment_active_cache[organization_id] = _ConfirmedActive(
        key, active, now + _ASSIGNMENT_ACTIVE_CACHE_TTL_SECONDS, now
    )
    return active


async def _resolve_round_robin_snapshot(
    session: AsyncSession, ctx: ScopeContext
) -> tuple[list[uuid.UUID], set[uuid.UUID]]:
    """The owner-set snapshot `_assign_owner` needs, read and resolved **before any row is
    locked**.

    This is the fix for the finding that mattered most here: the old shape read the rule
    *locked*, then asked customer-service which of its owners were still active while still
    holding that lock — so a hanging customer-service serialized every concurrent lead
    creation for the organization on this one row, each holding a database connection out of
    the pool for however long the network call took. A plain, unlocked read costs the same one
    round trip (or, within the cache window, none) without blocking anyone else's read of the
    same row while it happens.

    Returns `(owner_ids, active)` as read *right now* — `_assign_owner` re-reads the rule
    locked afterwards and compares; if a concurrent reconfigure changed `owner_ids` in the
    gap, the snapshot resolved here is simply not for the row it ends up locking, and
    `_assign_owner` falls back to its own cache-or-empty rule rather than resolving again
    while holding the lock.
    """
    rule = await selectors.get_assignment_rule(session, ctx.organization_id)
    if rule is None or rule.mode != "round_robin" or not rule.owner_ids:
        return [], set()
    owner_ids = list(rule.owner_ids)
    active = await _active_owner_ids_for_assignment(ctx.organization_id, owner_ids)
    return owner_ids, active


async def _assign_owner(
    session: AsyncSession,
    ctx: ScopeContext,
    rule: AssignmentRule,
    *,
    snapshot_owner_ids: list[uuid.UUID],
    snapshot_active: set[uuid.UUID],
) -> uuid.UUID | None:
    """Round-robin's write half: `domain.next_owner` picks, this advances the cursor in the
    same transaction as the lead it assigns. `rule` must already be row-locked
    (`_get_or_create_rule`/`get_assignment_rule_for_update`) — the lock plus this one atomic
    read-modify-write is what stops two concurrent lead creates from both picking
    `owner_ids[0]` and both advancing the cursor to `1`, the same rep twice with the rotation
    never having moved. Returns `None` under `manual` mode, an empty rotation, or one whose
    every owner has since gone inactive (logged, once, the moment it happens rather than
    silently) — a lead with no owner is a valid, unassigned lead, not an error.

    `snapshot_owner_ids`/`snapshot_active` come from `_resolve_round_robin_snapshot`, resolved
    *before* `rule` was locked. Never calls out to customer-service itself — that is exactly
    what would hold this row's lock across a network round trip again. If the now-locked
    `rule.owner_ids` still matches the snapshot, its already-resolved active set is used
    directly; if a concurrent reconfigure changed it in the gap between the snapshot and the
    lock, this falls back to a cached answer for the *new* set if one happens to exist, or
    treats the lead as unassigned for this one capture rather than resolving inline.
    """
    if rule.mode != "round_robin":
        return None
    if not rule.owner_ids:
        return None
    if list(rule.owner_ids) == snapshot_owner_ids:
        active = snapshot_active
    else:
        key = frozenset(rule.owner_ids)
        cached = _assignment_active_cache.get(ctx.organization_id)
        if cached is not None and cached.key == key and time.monotonic() < cached.expires_at:
            active = cached.active
        else:
            log.warning(
                "lead.assignment.owner_set_changed_under_lock",
                organization_id=str(ctx.organization_id),
            )
            active = set()
    rotation = [owner_id for owner_id in rule.owner_ids if owner_id in active]
    if not rotation:
        log.warning("lead.assignment.all_owners_inactive", organization_id=str(ctx.organization_id))
        return None
    owner_id, new_cursor = domain.next_owner(rotation, rule.cursor)
    if owner_id is not None:
        rule.cursor = new_cursor
        await session.flush()
    return owner_id


#: `_confirmed_creator_owner`'s own circuit breaker: until when the directory is presumed down
#: for an organization after the fallback's own check failed. Separate from the rotation's
#: `_assignment_unavailable_cache`, whose entry is keyed to the rotation's owner set.
_creator_unavailable_until: dict[uuid.UUID, float] = {}


def _creator_owner_candidate(
    ctx: ScopeContext, fallback_owner_id: uuid.UUID | None
) -> uuid.UUID | None:
    """The rep a lead falls back to when the rotation hands it to nobody: the one the caller
    named as their own, else their employee link, else -- at ORG scope only -- their membership
    id (the roster row `PUT /customers/owners/me` makes for a login with no employee record).

    The same rule as deal-service's `default_owner`: a narrower scope never falls back to the
    membership id, because OWN reads compare the employee link and the lead would vanish from
    the person who just captured it.

    A named fallback is scope-checked like an explicit owner, so an OWN-scoped rep cannot hand a
    lead to a colleague this way either -- but one the caller may not use is *ignored*, never a
    403. It is a hint the SPA picks with `findMyRep`, which also matches a roster row by
    membership id or email; an OWN rep whose row was matched that way (an admin added them by
    email, a CRM-only tenant with no employee link) must still be able to capture a lead, as
    they could before the fallback existed. It is never used, so no colleague is assigned."""
    if fallback_owner_id is not None:
        try:
            ctx.assert_can_touch(owner_id=fallback_owner_id)
        except ForbiddenProblem:
            log.info(
                "lead.assignment.fallback_out_of_scope", organization_id=str(ctx.organization_id)
            )
        else:
            return fallback_owner_id
    # `own_ref`, never the raw employee link (tests/infra/test_no_raw_subject_ref.py): the same id
    # today, and the membership id once lead declares the membership subject (R2.7).
    if ctx.own_ref is not None:
        return ctx.own_ref
    if ctx.scope is Scope.ORG:
        return ctx.principal.membership_id
    return None


async def _confirmed_creator_owner(
    organization_id: uuid.UUID, candidate: uuid.UUID | None
) -> uuid.UUID | None:
    """`candidate`, if customer's directory confirms it is an active rep; otherwise `None`.

    Never raises for the directory: a lead capture must not fail because the fallback could not
    be confirmed. An unconfirmed creator leaves the lead unassigned, which the lead page then
    lets someone fix. Runs before the rotation row is locked (see `create_lead`)."""
    if candidate is None:
        return None
    now = time.monotonic()
    # Honour both circuit breakers: a directory the rotation (or this) just found unreachable is
    # not asked again per capture during an outage.
    rotation_down = _assignment_unavailable_cache.get(organization_id)
    if (
        rotation_down is not None and now < rotation_down[1]
    ) or now < _creator_unavailable_until.get(organization_id, 0.0):
        return None
    try:
        invalid = await clients.invalid_owner_ids(settings(), organization_id, [candidate])
    except clients.OwnersValidateUnavailable:
        log.warning(
            "lead.assignment.creator_fallback_unconfirmed", organization_id=str(organization_id)
        )
        _creator_unavailable_until[organization_id] = (
            now + _ASSIGNMENT_UNAVAILABLE_CACHE_TTL_SECONDS
        )
        return None
    if candidate in invalid:
        log.info("lead.assignment.creator_not_a_rep", organization_id=str(organization_id))
        return None
    return candidate


# ------------------------------------------------------------------ leads


async def create_lead(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    full_name: str,
    company: str | None,
    email: str | None,
    phone: str | None,
    source: str,
    owner_id: uuid.UUID | None,
    notes: str,
    fallback_owner_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> Lead:
    """Capture a lead. `email` / `phone` are sealed and masked before anything is stored —
    the raw values are never written to a column that survives this function. `phone` is
    expected already canonicalised to E.164 (`app.schemas.LeadIn`'s own validator does this
    at the boundary, before this function ever sees it). A repeat submission of the same
    email in this organization refuses (409): the dedup lookup reads the blind index, never a
    decrypted value, so checking costs no sensitive read.

    `owner_id` omitted assigns from the organization's round-robin (`AssignmentRule`); a form
    submission has no rep to name, so this is the path most captures take. Named explicitly,
    it must be a rep the caller may act as (`ctx.assert_can_touch` — an OWN-scoped caller may
    only create leads owned by themselves) and a known, active rep (`_ensure_owners_active`).

    When the rotation hands the lead to nobody -- no rule configured (every new organization),
    or every rep in it has left -- the creator owns it (`_creator_owner_candidate`), provided
    customer's directory confirms they are an active rep. `manual` mode still assigns nobody:
    that is the setting that says "do not assign automatically". A lead with no owner cannot be
    converted, so leaving a single-user organization's leads unassigned left them stuck.
    """
    if owner_id is not None:
        ctx.assert_can_touch(owner_id=owner_id)
        await _ensure_owners_active(ctx.organization_id, [owner_id])

    cfg = settings()

    email_masked: str | None = None
    email_enc: bytes | None = None
    email_index: str | None = None
    if email:
        email_index = blind_index(email, kind="email", settings=cfg)
        if await selectors.get_lead_by_email_index(session, ctx.organization_id, email_index):
            raise ConflictProblem("A lead with that email already exists")
        email_enc, email_masked = seal(email, kind="email", settings=cfg)

    phone_masked: str | None = None
    phone_enc: bytes | None = None
    if phone:
        phone_enc, phone_masked = seal(phone, kind="phone", settings=cfg)

    resolved_owner_id = owner_id
    if resolved_owner_id is None:
        # Resolved *before* the rule's row is locked (M1, fix round 3) — asking
        # customer-service which owners are still active is a network call, and the row this
        # locks is the whole organization's rotation; holding that lock across a hanging
        # customer-service would serialize every other concurrent lead capture behind it.
        snapshot_owner_ids, snapshot_active = await _resolve_round_robin_snapshot(session, ctx)
        # The creator fallback is confirmed here too, for the same reason: it is a network
        # call, and it must not run with the rotation row locked. Only asked when the snapshot
        # already says the rotation will pick nobody.
        candidate = _creator_owner_candidate(ctx, fallback_owner_id)
        creator_owner = (
            await _confirmed_creator_owner(ctx.organization_id, candidate)
            if not snapshot_active
            else None
        )
        rule = await _get_or_create_rule(session, ctx)
        resolved_owner_id = await _assign_owner(
            session,
            ctx,
            rule,
            snapshot_owner_ids=snapshot_owner_ids,
            snapshot_active=snapshot_active,
        )
        if resolved_owner_id is None and rule.mode != "manual":
            resolved_owner_id = creator_owner

    lead = Lead(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        full_name=full_name,
        company=company,
        email_masked=email_masked,
        email_enc=email_enc,
        email_index=email_index,
        phone_masked=phone_masked,
        phone_enc=phone_enc,
        source=source,
        status="new",
        owner_id=resolved_owner_id,
        notes=notes,
    )
    try:
        # A SAVEPOINT, not a bare `session.add` + `flush()`: once a statement inside a
        # transaction raises, Postgres marks the *whole* transaction aborted until a ROLLBACK
        # (or a ROLLBACK TO SAVEPOINT), so a later statement on it — including the recovery
        # read below — would fail with "current transaction is aborted" without this.
        # `leave.services.create_ledger_entry` takes the identical shape for its own
        # concurrent-duplicate race.
        async with session.begin_nested():
            session.add(lead)
            await session.flush()
    except IntegrityError as exc:
        if constraint_name(exc) != "uq_leads_org_email_index":
            raise
        # The race the pre-check above cannot see: two concurrent captures of the same email
        # in this organization. The pre-check reduces this to a rare event; it does not
        # eliminate it, and only the database's own unique index is the actual guarantee.
        raise ConflictProblem("A lead with that email already exists") from exc

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="lead.created",
        entity="lead",
        entity_id=lead.id,
        after={"full_name": full_name, "source": source, "status": "new"},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAD_CREATED,
        aggregate_type="lead",
        aggregate_id=lead.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "lead_id": str(lead.id),
            "source": source,
            "owner_id": str(resolved_owner_id) if resolved_owner_id else None,
        },
        actor=actor_for(ctx),
    )
    return lead


async def update_lead(
    session: AsyncSession,
    ctx: ScopeContext,
    lead_id: uuid.UUID,
    payload: LeadPatch,
    *,
    request: Request | None = None,
) -> Lead:
    """Patch a lead's plain fields and status. `status` is checked against
    `domain.can_transition`, which has no move landing on `converted` — only
    `POST /leads/{id}:convert` can set that, because it is the one path that also creates the
    company and deal the status implies.

    `owner_id` set to a rep is re-scope-checked on the *new* value (an OWN-scoped caller may
    only reassign to themselves) and must be a known, active rep (`_ensure_owners_active`).
    `owner_id` set to `None` — unassigning — requires ORG scope: the route's own object-level
    check already confirmed the caller may touch this lead's *current* owner, but removing an
    owner is a step further than reassigning to yourself, and OWN scope was never meant to
    cover it.

    Fetches the row **row-locked** (`get_lead_for_update`), not the plain read the route used
    to pass in — a PATCH racing `:convert`'s claim/call/record shape used to read the lead
    unlocked, so a disqualify sent while a conversion was mid-flight (no transaction open
    between the claim and the network calls, by design) would see "qualified", allow the
    move, and commit — and `finalize_conversion`, coming back afterward, would still hold the
    same `conversion_claim_id` and overwrite the disqualification with "converted" anyway.
    Locking here means whichever of this PATCH or `finalize_conversion` gets to the row first
    now genuinely blocks the other until it commits, instead of both reading a "before" that
    is stale by the time either one writes.
    """
    changes = payload.model_dump(exclude_unset=True)

    # Everything about the *new* owner that does not depend on the lead's own row — the scope
    # check and the customer-service round trip — runs **before** the row is locked. Asking
    # customer-service whether a rep is active is a network call, and doing it after
    # `get_lead_for_update` held this lead's row lock across however long it took: a hanging
    # customer-service froze every other PATCH, and `:convert`'s own claim and finalize, on the
    # same lead. (The same reasoning `_resolve_round_robin_snapshot` applies to the rotation.)
    if "owner_id" in changes:
        if changes["owner_id"] is None:
            if ctx.scope is not Scope.ORG:
                raise ForbiddenProblem("Unassigning a lead requires ORG scope")
        else:
            ctx.assert_can_touch(owner_id=changes["owner_id"])
            await _ensure_owners_active(ctx.organization_id, [changes["owner_id"]])

    lead = await selectors.get_lead_for_update(session, ctx.organization_id, lead_id)
    if lead is None:
        raise NotFoundProblem("That lead is not part of this organization")
    ctx.assert_can_touch(owner_id=lead.owner_id)

    touches_conversion_state = "status" in changes or "owner_id" in changes
    if touches_conversion_state:
        # Terminal, belt-and-suspenders: `can_transition` already refuses every *status* move
        # once `lead.status == "converted"`, but a converted lead's ids are the thing that
        # actually matters — checked directly here so an owner reassignment is refused too,
        # and so a row somehow carrying converted ids without a literal "converted" status
        # (a hand-edited row, a bad migration) still cannot be patched as if it were live.
        if lead.status == "converted" or lead.converted_company_id is not None:
            raise ConflictProblem(
                "A converted lead cannot be reassigned or have its status changed"
            )
        # A conversion attempt is between its claim and its record — no transaction is open
        # right now to have blocked this PATCH from reaching the row, so the lease is the only
        # thing standing in for that lock.
        if domain.claim_is_live(lead.conversion_claimed_at, now=utcnow()):
            raise ConflictProblem("This lead's conversion is in progress. Try again in a moment.")

    if "status" in changes:
        new_status = changes["status"]
        if not domain.can_transition(lead.status, new_status):
            raise ConflictProblem(domain.refusal_for_transition(lead.status, new_status))

    before = {field: getattr(lead, field) for field in changes}
    for field, value in changes.items():
        setattr(lead, field, value)
    lead.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="lead.updated",
        entity="lead",
        entity_id=lead.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAD_UPDATED,
        aggregate_type="lead",
        aggregate_id=lead.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "lead_id": str(lead.id),
            "changed": sorted(changes),
        },
        actor=actor_for(ctx),
    )
    return lead


# ------------------------------------------------------------------ revealing a sealed detail

#: What the 404 says when the requested detail was never captured. Names only the field the
#: caller asked about — it says nothing about the *other* one.
_NOTHING_ON_FILE: dict[str, str] = {
    "phone": "No phone number is on file for this lead",
    "email": "No email address is on file for this lead",
}

#: The daily budget's window. Not a setting: "per day" is what the budget is called.
_REVEAL_DAILY_WINDOW_SECONDS = 86_400

#: The longest the reveal budget's Redis round trip may take. There is exactly one round trip
#: (`_REVEAL_BUDGET_SCRIPT`), so this is the whole cost of the check and the most a request can
#: hold a pooled database connection for while it waits: the budget is spent inside the request's
#: transaction (after the lead has been looked up and before it is opened). A Redis that neither
#: answers nor refuses — a dropped route, a stopped container behind a proxy — is the same 503 an
#: outage is: nothing is revealed.
_REVEAL_BUDGET_TIMEOUT_SECONDS = 2.0

#: Spend one reveal from **both** windows, or from **neither**, in one atomic step on the server.
#: `KEYS` are the burst and the daily counter; `ARGV` are their limits and their TTLs.
#:
#: * **One round trip and one atomic step**, so a timeout or a dropped connection cannot land
#:   between an `INCR` and its `EXPIRE` and leave a counter that never expires (which, for a
#:   daily counter, would refuse its user for ever). The server runs a script to completion once
#:   it has it whatever the client does next, and does not run one it never received.
#: * **The expiry is set whenever there is none** (`TTL < 0`), not only when the count is 1, so a
#:   counter that somehow lost its expiry is healed by the next request.
#: * **A refused request spends nothing.** A caller who is over the burst limit does not also burn
#:   the daily budget with the requests that are refused, so a stolen token cannot exhaust the
#:   real person's day by hammering after the burst is spent. It answers `1` (burst spent) or `2`
#:   (daily spent) with the current counts, and `0` when it spent.
#:
#: Duplicated in lead- and customer-service, which share the counters (the same keys), and pinned
#: equal by `tests/infra/test_reveal_budget_parity.py`. Keep them identical.
_REVEAL_BUDGET_SCRIPT = """
local burst = tonumber(redis.call('GET', KEYS[1]) or '0')
local daily = tonumber(redis.call('GET', KEYS[2]) or '0')
if burst >= tonumber(ARGV[1]) then return {1, burst, daily} end
if daily >= tonumber(ARGV[2]) then return {2, burst, daily} end
burst = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then redis.call('EXPIRE', KEYS[1], ARGV[3]) end
daily = redis.call('INCR', KEYS[2])
if redis.call('TTL', KEYS[2]) < 0 then redis.call('EXPIRE', KEYS[2], ARGV[4]) end
return {0, burst, daily}
"""


def log_reveal_refused(
    principal: Principal,
    *,
    reason: str,
    lead_id: uuid.UUID | None = None,
    field: str | None = None,
    request: Request | None = None,
) -> None:
    """One structured WARNING per refused reveal: who, where, which lead, and why.

    A log line, deliberately not an audit row. A refusal changes nothing, so it is not an
    `entity.sensitive_read` (that action means a value was opened), and an audit table a prober
    can grow one 403 at a time is a cost rather than a control. What a refusal *does* need is to
    be visible — somebody walking lead ids, or a caller without the permission trying every field,
    is exactly the pattern worth grepping for.

    Never a value: the ids, the field's *name* and a reason code, nothing read from the lead.
    `request_id` correlates the line with the request's other logs; it is whatever the client
    sent as `X-Request-ID` when it sent one, so it locates a line and proves nothing about who
    made the call — `actor_id` is the identity.
    """
    log.warning(
        "lead.reveal.refused",
        reason=reason,
        organization_id=str(principal.organization_id),
        actor_id=str(principal.sub),
        lead_id=str(lead_id) if lead_id is not None else None,
        field=field,
        request_id=getattr(request.state, "request_id", None) if request is not None else None,
    )


async def enforce_reveal_budget(ctx: ScopeContext, *, request: Request | None = None) -> None:
    """Spend one reveal from this user's budget in this organization, or raise 429.

    Two fixed windows, both keyed on the organization *and* the user, so one person's budget is
    never another's and the same person in two organizations has two (a membership is
    per-organization): `reveal_burst_limit` per `reveal_burst_window_seconds`, and
    `reveal_daily_limit` per UTC day. The keys are shared with customer-service's reveal on purpose
    — the thing being bounded is how much PII one person can read, and two routes with two budgets
    would double it. **Keep the key prefix identical to customer-service's.**

    **Spent when a lead is about to be opened, not when a request arrives.** It runs after the
    permission and the entitlement have passed and after the lead has been shown to
    exist in this organization, to be in the caller's scope and to have a value on file — and
    before it is opened (`reveal_lead_field`). A request that was going to be refused spends
    nothing, so a stolen token cannot exhaust the real person's budget with invalid ids or leads
    they may not touch, and cannot deny them the reveals they are entitled to. What it costs:
    probing with ids that turn out to be foreign or absent is no longer counted here, only by the
    gateway's own limits — and it reads nothing, since absent and foreign are the same 404.

    A window is fixed, not sliding: a caller can spend the end of one and the start of the next.
    Both windows are spent — or both refused, with nothing spent — in **one** round trip
    (`_REVEAL_BUDGET_SCRIPT`), bounded by **one** timeout, so the whole check costs the request at
    most `_REVEAL_BUDGET_TIMEOUT_SECONDS` of a held database connection.

    **Fails closed.** Redis unreachable, erroring, not opened in this process, or not answering in
    `_REVEAL_BUDGET_TIMEOUT_SECONDS` is a 503: a Redis outage stops reveals rather than lifting
    their limit. Nothing else in this service uses Redis.
    """
    cfg = settings()
    who = f"{ctx.organization_id}:{ctx.principal.sub}"
    now = int(time.time())
    burst_window = cfg.reveal_burst_window_seconds
    daily_window = _REVEAL_DAILY_WINDOW_SECONDS
    # The keys `platform_core.cache.enforce_rate_limit` would have used for the same windows.
    burst_key = f"rl:crm.reveal.burst:{who}:{now // burst_window}"
    daily_key = f"rl:crm.reveal.daily:{who}:{now // daily_window}"
    try:
        async with asyncio.timeout(_REVEAL_BUDGET_TIMEOUT_SECONDS):
            verdict, _burst, _daily = await get_redis().eval(
                _REVEAL_BUDGET_SCRIPT,
                2,
                burst_key,
                daily_key,
                cfg.reveal_burst_limit,
                cfg.reveal_daily_limit,
                # Two windows of slack, so a clock skew cannot orphan the key.
                burst_window * 2,
                daily_window * 2,
            )
    except Exception as exc:
        log.error("lead.reveal.budget_unavailable", error=str(exc)[:120])
        raise UpstreamUnavailableProblem(
            "Cannot verify request limits right now. Please retry shortly."
        ) from exc

    if verdict != 0:
        window, name = (burst_window, "burst") if verdict == 1 else (daily_window, "daily")
        log_reveal_refused(ctx.principal, reason=f"rate_limited_{name}", request=request)
        problem = RateLimitedProblem("Too many requests. Please slow down.")
        problem.headers = {"Retry-After": str(window)}
        raise problem


async def reveal_lead_field(
    session: AsyncSession,
    ctx: ScopeContext,
    lead_id: uuid.UUID,
    field: RevealField,
    *,
    request: Request | None = None,
) -> str:
    """Reveal a lead's real phone number or email to a human — the audited path (no step-up).

    Reaching here means the caller held `crm.lead.lead.read_sensitive` (no step-up is required,
    ADR-0023 addendum). Everything else is decided here, in one
    function, so no caller can reach the decrypt having skipped a check:

    1. The lead is fetched inside the caller's organization. An id that is not there — or is
       another organization's — is the same 404, so this route confirms nothing about lead ids
       it should not.
    2. The caller's scope is re-applied to this one lead (`assert_can_touch`, a 403): filtering
       a list and forgetting the detail is the named vulnerability. Checked *before* whether a
       value exists, so an out-of-scope caller cannot learn whether a lead has a phone.
    3. Nothing sealed for the requested field is a 404 (`_NOTHING_ON_FILE`), never a 200 with a
       null. No audit row is kept for it — nothing was disclosed, and the request's
       transaction rolls back on the error.
    4. The reveal budget is spent (`enforce_reveal_budget`, 429 when spent, 503 when it cannot
       be checked): only a request that has passed 1-3 counts, and nothing has been decrypted yet.
    5. The sealed value is opened with the same key context `create_lead` sealed it under.
    6. An `entity.sensitive_read` audit row naming the **field only** — and the token's session
       and `amr` — is staged in the caller's transaction, which commits before the response is
       sent (`get_tenant_session`'s function-scoped exit): a revealed value never leaves without a
       record that it was revealed. The value itself is never written anywhere, here or in a
       log line.

    Every refusal in 1-4 also logs one WARNING (`log_reveal_refused`) — not an audit row.

    A converted lead still reveals — it keeps its own sealed copy, independent of the contact
    conversion created on customer-service's side.
    """
    lead = await selectors.get_lead(session, ctx.organization_id, lead_id)
    if lead is None:
        log_reveal_refused(
            ctx.principal, reason="lead_not_found", lead_id=lead_id, field=field, request=request
        )
        raise NotFoundProblem("That lead is not part of this organization")
    try:
        ctx.assert_can_touch(owner_id=lead.owner_id)
    except ForbiddenProblem:
        log_reveal_refused(
            ctx.principal, reason="out_of_scope", lead_id=lead.id, field=field, request=request
        )
        raise

    sealed = lead.phone_enc if field == "phone" else lead.email_enc
    if sealed is None:
        log_reveal_refused(
            ctx.principal, reason="nothing_on_file", lead_id=lead.id, field=field, request=request
        )
        raise NotFoundProblem(_NOTHING_ON_FILE[field])

    # Everything that could refuse this request without a value being opened has now run, so this
    # is where the budget is spent: after the cheap checks, before the decrypt.
    await enforce_reveal_budget(ctx, request=request)

    try:
        plaintext = open_sealed(sealed, settings=settings())
    except PiiError:
        # An unopenable envelope is a server fault (a key rotated out from under a row, a
        # damaged column), not something to describe to a client — it surfaces as a plain 500.
        # The log names the row and the field so someone can find it, and never the value.
        log.error(
            "lead.reveal.unopenable",
            organization_id=str(ctx.organization_id),
            lead_id=str(lead.id),
            field=field,
        )
        raise

    # The same action and shape `record_sensitive_read` writes (`{"fields": [...]}`), plus the
    # session that did it. `record_sensitive_read` cannot carry it, so this is `record_audit`
    # directly. The row's own `request_id` comes from `X-Request-ID` when the client sent one,
    # so it is a correlation aid and not evidence of anything; `session_id` — the token's `sid`
    # claim, forwarded by the gateway — is what ties a reveal to the sign-in that authorised it,
    # and `amr` — the token's authentication methods — says *how* the session signed in
    # (`["pwd"]`, or `["pwd", "otp"]` when it gave a code). No step-up is needed to reveal.
    # The field's *name* only, never its value.
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="entity.sensitive_read",
        entity="lead",
        entity_id=lead.id,
        after={
            "fields": [field],
            "session_id": ctx.principal.session_id,
            "amr": list(ctx.principal.amr),
        },
        request=request,
    )
    log.info(
        "lead.field_revealed",
        organization_id=str(ctx.organization_id),
        lead_id=str(lead.id),
        field=field,
    )
    return plaintext


# ------------------------------------------------------------------ conversion: claim, call, record


@dataclass(frozen=True, slots=True)
class ConversionClaim:
    """A snapshot of what `convert_lead` needs once `claim_conversion`'s transaction has
    committed. Deliberately a plain dataclass, never the ORM `Lead` row: the whole point of
    splitting this into its own transaction is that the network calls which follow run with
    no transaction, and therefore no session state, open — touching an ORM object after its
    transaction closed is exactly the "attribute access needs IO the sync bridge cannot do
    outside a transaction" trap `services.convert_lead`'s tests guard against."""

    lead_id: uuid.UUID
    claim_id: uuid.UUID | None
    already_converted: bool
    company_id: uuid.UUID
    contact_id: uuid.UUID | None
    deal_id: uuid.UUID
    #: `Lead.conversion_generation` at the moment of the claim — the generation the three ids
    #: above were derived under, and the one `domain.deal_name` folds into the deal's name.
    generation: int
    #: `None` only on the `already_converted` branch's stale-data edge case (the owner was
    #: unassigned by a `PATCH` after this lead already converted) — never on a fresh claim,
    #: which refuses before it gets here (`claim_conversion`'s own "assign an owner" check).
    #: `convert_lead` narrows this itself before it is ever passed to a client that needs it.
    owner_id: uuid.UUID | None
    full_name: str
    company: str | None
    source: str
    raw_email: str | None
    raw_phone: str | None


async def claim_conversion(
    session: AsyncSession,
    ctx: ScopeContext,
    lead_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> ConversionClaim:
    """Tx1 of `convert_lead`'s claim / call / record shape.

    Locks the lead row and either: short-circuits an already-converted lead (no claim, no
    network call, the same ids returned again); refuses (409) a live claim another attempt
    holds (`domain.claim_is_live`, a `domain.CONVERSION_LEASE_SECONDS`-second lease); or takes
    the claim — a fresh `claim_id`, freshly stamped `conversion_claimed_at` — whether the lead
    was never claimed or a previous claim's lease has expired (a takeover is not a special
    case here: it is simply "not live", handled the same way as "never claimed").

    Opening the lead's own sealed email/phone and writing the `entity.sensitive_read` audit
    row for that happens **in this transaction**, before it commits — the fix for the finding
    that mattered most: previously, that audit row lived in the same transaction as the two
    network calls that followed it, so a failure in either call rolled the audit row back
    after the plaintext had already left this process on the wire. Now the record of "this
    attempt opened this lead's PII" is durable before a single byte of it is sent anywhere.
    """
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)

        lead = await selectors.get_lead_for_update(session, ctx.organization_id, lead_id)
        if lead is None:
            raise NotFoundProblem("That lead is not part of this organization")
        ctx.assert_can_touch(owner_id=lead.owner_id)

        if lead.status == "converted":
            if lead.converted_company_id is None or lead.converted_deal_id is None:
                # The model's own invariant (`app/models.py`: these are set together, never
                # separately) broken by something outside this function's control — a
                # hand-edited row, a bad migration. Not something a retry fixes.
                raise RuntimeError(f"lead {lead.id} is converted but is missing its conversion ids")
            return ConversionClaim(
                lead_id=lead.id,
                claim_id=None,
                already_converted=True,
                company_id=lead.converted_company_id,
                contact_id=lead.converted_contact_id,
                deal_id=lead.converted_deal_id,
                generation=lead.conversion_generation,
                owner_id=lead.owner_id,
                full_name=lead.full_name,
                company=lead.company,
                source=lead.source,
                raw_email=None,
                raw_phone=None,
            )

        if not domain.can_convert(lead.status):
            raise ConflictProblem(
                f"A lead in {lead.status!r} cannot be converted — qualify it first"
            )
        if lead.owner_id is None:
            raise ValidationProblem("Assign an owner to this lead before converting it")
        owner_id: uuid.UUID = lead.owner_id

        now = utcnow()
        if domain.claim_is_live(lead.conversion_claimed_at, now=now):
            raise ConflictProblem("This lead is already being converted. Try again in a moment.")

        cfg = settings()
        raw_email: str | None = None
        raw_phone: str | None = None
        sensitive_fields: list[str] = []
        if lead.email_enc is not None:
            raw_email = open_sealed(lead.email_enc, settings=cfg)
            sensitive_fields.append("email")
        if lead.phone_enc is not None:
            raw_phone = open_sealed(lead.phone_enc, settings=cfg)
            sensitive_fields.append("phone")
        if sensitive_fields:
            # Opening a lead's own sealed contact details, to hand them to customer-service so
            # it can seal its own copy, is itself a sensitive read — audited the same way
            # `unmask_contact_phone` audits a human's stepped-up unmask.
            record_sensitive_read(
                session,
                AuditLog,
                organization_id=ctx.organization_id,
                actor_id=ctx.principal.sub,
                entity="lead",
                entity_id=lead.id,
                fields=sensitive_fields,
                request=request,
            )

        claim_id = uuid.uuid4()
        lead.conversion_claim_id = claim_id
        lead.conversion_claimed_at = now
        lead.updated_by = str(ctx.principal.sub)
        await session.flush()

        company_id, contact_id, deal_id = domain.conversion_ids(lead.id, lead.conversion_generation)

        return ConversionClaim(
            lead_id=lead.id,
            claim_id=claim_id,
            already_converted=False,
            company_id=company_id,
            contact_id=contact_id,
            deal_id=deal_id,
            generation=lead.conversion_generation,
            owner_id=owner_id,
            full_name=lead.full_name,
            company=lead.company,
            source=lead.source,
            raw_email=raw_email,
            raw_phone=raw_phone,
        )


def _stage_conversion_aborted(
    session: AsyncSession,
    ctx: ScopeContext,
    lead: Lead,
    *,
    reason: str,
    claim_id: uuid.UUID,
    generation: int,
    company_id: uuid.UUID,
    contact_id: uuid.UUID | None,
    deal_id: uuid.UUID,
) -> None:
    """Stage `crm.lead.conversion_aborted` in the caller's transaction. This is the **only**
    place the event is built (`tests/test_aborted_event_builder.py` keeps it that way): its
    committed schema is closed (`additionalProperties: false`), so one builder is one place to
    keep the payload true to it.

    `generation` is the value **before** the bump this abort caused, so
    `domain.conversion_ids(lead_id, generation)` re-derives the three ids the aborted attempt
    used; `claim_id` names the attempt that ended. `company_id`, `contact_id` and `deal_id` are
    the ids the abort *names*: for a finalize abort the ones customer and deal returned, which
    may be records customer matched by name or email, and for `record_deleted` the ones derived
    at `generation` (nothing was returned). Either way they prove nothing about who created the
    records. Ids only: no name, no email, no phone.
    """
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAD_CONVERSION_ABORTED,
        aggregate_type="lead",
        aggregate_id=lead.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "lead_id": str(lead.id),
            "reason": reason,
            "generation": generation,
            "claim_id": str(claim_id),
            "company_id": str(company_id),
            "contact_id": str(contact_id) if contact_id else None,
            "deal_id": str(deal_id),
        },
        actor=actor_for(ctx),
    )


async def finalize_conversion(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    lead_id: uuid.UUID,
    claim_id: uuid.UUID,
    owner_id: uuid.UUID,
    company_id: uuid.UUID,
    contact_id: uuid.UUID | None,
    deal_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    """Tx2 of `convert_lead`'s claim / call / record shape: record a successful conversion.

    Re-locks the lead and proceeds only if it still holds *this* `claim_id` — if the lease
    expired while the network calls were in flight and a second attempt has since taken over,
    this raises rather than finalizing a conversion the row no longer agrees this attempt
    owns (the second attempt's own `finalize_conversion` is the one that gets to write the
    outcome; this one's caller should treat the 409 as "someone else finished this").

    Also requires the row's own `status` still be `"qualified"` and its `owner_id` still be the
    one `claim_conversion` snapshotted (`owner_id` here) before writing "converted" — the
    records customer and deal created were opened *for that owner*, so a reassignment in the
    gap means they no longer describe this lead. The claim/lease pair stops a *second
    `:convert`* from racing this one, and `update_lead`'s own claim check stops an ordinary
    PATCH from doing either while this attempt's lease is live, but neither is airtight
    against every future caller of this row (an admin tool, a bulk script) and this is the
    last place before "converted" is written that can still say no. Either mismatch aborts the
    claim instead of overwriting whatever it moved to, and records why — including the ids of
    the records the calls already created, which nothing here can take back — and stages
    `crm.lead.conversion_aborted` so something that can (a consumer, later) knows they are
    now orphans. Every abort bumps `Lead.conversion_generation` by one, so the retry derives
    ids no aborted attempt used (`domain.conversion_ids`) and creates fresh records instead of
    reusing the abandoned ones: for a reassignment they belong to the old owner, and for a
    status change a late cleanup of them must not be able to reach a retry that succeeded.
    """
    # `abort_reason`/`missing_claim` decide what to raise *after* the `async with` below exits —
    # never from inside it. Raising from inside a `session.begin()` block rolls the whole
    # transaction back, which for the abort path would silently undo the very things this
    # function exists to make durable: the cleared claim, the audit row explaining why, and
    # the event.
    missing_claim = False
    abort_reason: str | None = None
    detail = ""

    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)

        lead = await selectors.get_lead_for_update(session, ctx.organization_id, lead_id)
        if lead is None or lead.conversion_claim_id != claim_id:
            missing_claim = True
        else:
            before_status = lead.status
            if before_status != "qualified":
                abort_reason = "status_changed"
                detail = f"lead moved to {before_status!r} before the conversion finished"
            elif lead.owner_id != owner_id:
                abort_reason = "owner_changed"
                detail = "lead was reassigned before the conversion finished"

            if abort_reason is not None:
                # Every abort moves the lead to a new generation (D-4), so the retry derives
                # ids no aborted attempt used. For a reassignment that keeps the retry from
                # handing the new owner a company and deal opened for the old one; for a status
                # change it keeps a later cleanup of the abandoned records (R2.4) away from a
                # retry that has since succeeded. The claim-id match above proves the row's
                # generation is the one this attempt's ids were derived under: only the holder
                # of the live claim can bump it, and a bump clears the claim.
                aborted_generation = lead.conversion_generation
                lead.conversion_claim_id = None
                lead.conversion_claimed_at = None
                lead.updated_by = str(ctx.principal.sub)
                lead.conversion_generation += 1
                orphans = {
                    "company_id": str(company_id),
                    "contact_id": str(contact_id) if contact_id else None,
                    "deal_id": str(deal_id),
                }
                abort_after: dict[str, Any] = {
                    "reason": detail,
                    **orphans,
                    "conversion_generation": lead.conversion_generation,
                    "claim_id": str(claim_id),
                    "aborted_generation": aborted_generation,
                }
                await session.flush()
                record_audit(
                    session,
                    AuditLog,
                    organization_id=ctx.organization_id,
                    actor_id=ctx.principal.sub,
                    action="lead.conversion_aborted",
                    entity="lead",
                    entity_id=lead.id,
                    before={
                        "status": before_status,
                        "owner_id": str(lead.owner_id) if lead.owner_id else None,
                    },
                    after=abort_after,
                    request=request,
                )
                _stage_conversion_aborted(
                    session,
                    ctx,
                    lead,
                    reason=abort_reason,
                    claim_id=claim_id,
                    generation=aborted_generation,
                    company_id=company_id,
                    contact_id=contact_id,
                    deal_id=deal_id,
                )
            else:
                lead.status = "converted"
                lead.converted_company_id = company_id
                lead.converted_contact_id = contact_id
                lead.converted_deal_id = deal_id
                lead.converted_at = utcnow()
                lead.conversion_claim_id = None
                lead.conversion_claimed_at = None
                lead.updated_by = str(ctx.principal.sub)
                await session.flush()

                record_audit(
                    session,
                    AuditLog,
                    organization_id=ctx.organization_id,
                    actor_id=ctx.principal.sub,
                    action="lead.converted",
                    entity="lead",
                    entity_id=lead.id,
                    before={"status": before_status},
                    after={
                        "status": "converted",
                        "company_id": str(company_id),
                        "contact_id": str(contact_id) if contact_id else None,
                        "deal_id": str(deal_id),
                    },
                    request=request,
                )
                publish_event(
                    session,
                    OutboxEvent,
                    organization_id=ctx.organization_id,
                    event_type=EVT_LEAD_CONVERTED,
                    aggregate_type="lead",
                    aggregate_id=lead.id,
                    payload={
                        "organization_id": str(ctx.organization_id),
                        "lead_id": str(lead.id),
                        "company_id": str(company_id),
                        "contact_id": str(contact_id) if contact_id else None,
                        "deal_id": str(deal_id),
                    },
                    actor=actor_for(ctx),
                )

    if missing_claim:
        raise ConflictProblem(
            "This lead's conversion claim was taken over by another attempt. "
            "Call :convert again to get the current result."
        )
    if abort_reason == "status_changed":
        raise ConflictProblem(
            "This lead's status changed before the conversion could complete. "
            "Check its current status and try again."
        )
    if abort_reason == "owner_changed":
        raise ConflictProblem(
            "This lead was reassigned before the conversion could complete. "
            "Check its current owner and try again."
        )


async def fail_conversion(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    lead_id: uuid.UUID,
    claim_id: uuid.UUID,
    reason: str,
    bump_generation: bool = False,
    request: Request | None = None,
) -> None:
    """The other half of Tx2: clear a claim that will not be finalized, so the *next* attempt
    does not have to wait out a lease on a conversion that has already, definitively, failed —
    a permanent rejection from either target service, or a transient failure lead-service has
    already decided to surface to its own caller rather than silently retry. `reason` is one
    of `app/clients.py`'s own fixed, generic exception messages — never an upstream response
    body — recorded so the audit trail says *that* a conversion attempt failed and roughly
    why, without carrying anything either target service said back to us.

    `bump_generation` is for the one failure that would otherwise be permanent: customer
    reporting that the company or contact an *earlier* attempt created for this lead was
    deleted. The ids `domain.conversion_ids` derives are deterministic, so a deleted record at
    one of them refuses every future attempt on the same ids forever — bumping
    `Lead.conversion_generation` makes the next attempt derive a fresh set instead, and
    announces it as `crm.lead.conversion_aborted` with reason `record_deleted`, so whatever
    the abandoned generation left behind (typically the deal) has an event to be found by.
    A failure that does not bump stages no event.

    A no-op if the claim has already been taken over or cleared by someone else — there is
    nothing left for this attempt to fail on the row's behalf.
    """
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)

        lead = await selectors.get_lead_for_update(session, ctx.organization_id, lead_id)
        if lead is None or lead.conversion_claim_id != claim_id:
            return

        aborted_generation = lead.conversion_generation
        lead.conversion_claim_id = None
        lead.conversion_claimed_at = None
        lead.updated_by = str(ctx.principal.sub)
        after: dict[str, Any] = {"reason": reason}
        if bump_generation:
            lead.conversion_generation += 1
            after["conversion_generation"] = lead.conversion_generation
            after["claim_id"] = str(claim_id)
            after["aborted_generation"] = aborted_generation
        await session.flush()

        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action="lead.conversion_failed",
            entity="lead",
            entity_id=lead.id,
            after=after,
            request=request,
        )
        if bump_generation:
            # The three ids are a function of the generation this attempt ran under, so they
            # are re-derived here rather than read from anywhere: nothing was returned
            # (customer's answer *was* the failure). The contact id is the derived one only when
            # the lead holds an email, read from the sealed column's presence and never opened.
            company_id, contact_id, deal_id = domain.conversion_ids(lead.id, aborted_generation)
            _stage_conversion_aborted(
                session,
                ctx,
                lead,
                reason="record_deleted",
                claim_id=claim_id,
                generation=aborted_generation,
                company_id=company_id,
                contact_id=contact_id if lead.email_enc is not None else None,
                deal_id=deal_id,
            )


async def convert_lead(
    session: AsyncSession,
    ctx: ScopeContext,
    lead_id: uuid.UUID,
    *,
    deal_value_minor: int = 0,
    deal_stage: str | None = None,
    request: Request | None = None,
) -> tuple[uuid.UUID, uuid.UUID | None, uuid.UUID]:
    """Convert a qualified lead into a company (+ contact) and a deal — claim, call, record.

    `deal_value_minor` (integer minor units, INR) and `deal_stage` (a pipeline stage key; `None`
    is the entry stage) are the rep's optional say over the deal that opens. A replay of an
    already-converted lead ignores them: the deal already exists and is never rewritten here.

    Three steps, three transactions (`claim_conversion`, then customer's and deal's
    `/internal/v1` with **no transaction open**, then `finalize_conversion` or
    `fail_conversion`). Concurrent `:convert` calls on the *same* lead are serialized by the
    claim/lease pair, not by holding a row lock across the network calls — the earlier
    version of this function did exactly that (one transaction, the whole way through), and
    holding a lock — or worse, an entire request's transaction — across two upstream calls
    turned a slow customer-service or deal-service into a database problem for every other
    reader of this lead's row, and rolled back the sensitive-read audit row whenever either
    call failed after the PII had already gone out on the wire.

    Idempotent: a lead already `converted` short-circuits inside `claim_conversion`, before
    any claim is taken and before any network call — ADR-0017's guarantee that converting
    twice is one company, one contact (or none), one deal.
    """
    claim = await claim_conversion(session, ctx, lead_id, request=request)
    if claim.already_converted:
        return claim.company_id, claim.contact_id, claim.deal_id
    if claim.claim_id is None or claim.owner_id is None or claim.contact_id is None:
        # `claim_conversion` always sets all three on the non-`already_converted` branch — a
        # fault to investigate, not a validation error to hand back, if this is ever hit.
        # (`contact_id` here is the *deterministic* id `domain.conversion_ids` derived, always
        # present on a fresh claim regardless of whether the lead has an email — customer
        # decides whether to actually use it and may still answer with no contact at all.)
        raise RuntimeError(f"lead {claim.lead_id} was claimed but is missing claim state")
    claim_id = claim.claim_id
    owner_id = claim.owner_id
    deterministic_contact_id = claim.contact_id

    cfg = settings()
    company_name = claim.company or claim.full_name
    close_date = date.today() + timedelta(days=_DEFAULT_CLOSE_HORIZON_DAYS)

    try:
        company_id, contact_id = await clients.ensure_company_and_contact(
            cfg,
            ctx.organization_id,
            company_id=claim.company_id,
            contact_id=deterministic_contact_id,
            company_name=company_name,
            owner_id=owner_id,
            contact_full_name=claim.full_name,
            contact_email=claim.raw_email,
            contact_raw_phone=claim.raw_phone,
            actor_id=ctx.principal.sub,
        )
        deal_id = await clients.ensure_deal(
            cfg,
            ctx.organization_id,
            deal_id=claim.deal_id,
            company_id=company_id,
            contact_id=contact_id,
            owner_id=owner_id,
            name=domain.deal_name(company_name, claim.full_name),
            value_minor=deal_value_minor,
            stage=deal_stage,
            close_date=close_date.isoformat(),
            source=claim.source,
            actor_id=ctx.principal.sub,
        )
    except clients.OwnerUnknown as exc:
        await fail_conversion(
            session, ctx, lead_id=claim.lead_id, claim_id=claim_id, reason=str(exc)
        )
        raise ValidationProblem(
            "Assign an owner known to the CRM book before converting this lead"
        ) from exc
    except (clients.CompanyDeleted, clients.ContactDeleted) as exc:
        # Checked by exception type (customer's own `company-deleted` / `contact-deleted`
        # problem types, `clients._problem_type`), never by re-deriving it from a bare 404 or
        # 409 alone — a 404 here means "unknown owner" (`OwnerUnknown`, above), not a deleted
        # record. Bumps the generation so the *next* attempt derives fresh ids rather than
        # hitting the same deleted record at the same deterministic id forever.
        await fail_conversion(
            session,
            ctx,
            lead_id=claim.lead_id,
            claim_id=claim_id,
            reason=str(exc),
            bump_generation=True,
        )
        raise ValidationProblem(
            "The CRM record created by an earlier attempt was deleted — convert again to "
            "create fresh records."
        ) from exc
    except clients.DealNameConflict as exc:
        # Before the generic `ConversionRejected` below (it is a subclass): "check the lead's
        # details" would send the rep to fields that are not the problem.
        await fail_conversion(
            session, ctx, lead_id=claim.lead_id, claim_id=claim_id, reason=str(exc)
        )
        raise ValidationProblem(
            "A deal with this name already exists in the CRM book. Rename or remove that deal, "
            "then convert this lead again."
        ) from exc
    except clients.ConversionRejected as exc:
        await fail_conversion(
            session, ctx, lead_id=claim.lead_id, claim_id=claim_id, reason=str(exc)
        )
        raise ValidationProblem(
            "The conversion could not be completed. Check the lead's details and try again."
        ) from exc
    except clients.ConversionUnavailable as exc:
        await fail_conversion(
            session, ctx, lead_id=claim.lead_id, claim_id=claim_id, reason=str(exc)
        )
        raise UpstreamUnavailableProblem(
            "The CRM is temporarily unable to complete this conversion. Try again shortly."
        ) from exc

    await finalize_conversion(
        session,
        ctx,
        lead_id=claim.lead_id,
        claim_id=claim_id,
        owner_id=owner_id,
        company_id=company_id,
        contact_id=contact_id,
        deal_id=deal_id,
        request=request,
    )
    return company_id, contact_id, deal_id
