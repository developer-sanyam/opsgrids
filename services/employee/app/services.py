"""Writes for the employee service.

Every function here follows account-service's shape: change the row, record the audit entry,
stage the event — **all inside one transaction**. That is why `get_tenant_session` opens the
transaction and does not commit for the route: a crash between the state change and the event
cannot leave one without the other, and a rolled-back write never leaves an audit row claiming
it happened.

Events are staged into the outbox, never published inline. Publishing before the commit is
durable is the bug the outbox exists to prevent (ADR-0000).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.config import settings
from app.domain import EmploymentStatus, IllegalTransition, assert_can_transition
from app.models import AuditLog, Employee, EmployeePii, OutboxEvent
from app.schemas import (
    EmployeeIn,
    EmployeePatch,
    EmployeePiiIn,
    EmployeePiiMasked,
    EmployeePiiUnmasked,
)
from platform_core.audit import record_audit, record_sensitive_read
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import actor_for, publish_event
from platform_core.logging import get_logger
from platform_core.pii import open_sealed, seal

log = get_logger("employee.services")

# Event names. `hrms.*` because these are the product's own, not the platform's.
EVT_EMPLOYEE_ONBOARDED = "hrms.people.employee.onboarded"
EVT_EMPLOYEE_UPDATED = "hrms.people.employee.updated"
EVT_EMPLOYEE_TRANSITIONED = "hrms.people.employee.transitioned"

#: Each sensitive field mapped to its `platform_core.pii` masker kind. `bank_ifsc` is absent: it
#: is non-sensitive, stored whole in plaintext rather than sealed.
_PII_KINDS = {
    "date_of_birth": "dob",
    "personal_email": "email",
    "personal_phone": "phone",
    "pan": "pan",
    "aadhaar": "aadhaar",
    "uan": "uan",
    "esi": "esi",
    "bank_account": "bank_account",
}


def _as_plaintext(value: object) -> str:
    """The string a value is sealed as. A date is its ISO form, so `open_sealed` round-trips it
    back through `date.fromisoformat`; everything else is already a string."""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _apply_pii(row: EmployeePii, incoming: EmployeePiiIn) -> list[str]:
    """Seal each provided sensitive field into `row`, and store its mask. Returns the field
    names touched — for the audit/event, which record names only, never values.

    Only fields *set* on the patch are touched (`exclude_unset`), so a partial update seals the
    one field it names and leaves the rest of the sealed row alone. `bank_ifsc` is copied in the
    clear because it is a public branch code, not a secret.
    """
    cfg = settings()
    changed: list[str] = []
    provided = incoming.model_dump(exclude_unset=True)
    for field, value in provided.items():
        if value is None:
            continue
        if field == "bank_ifsc":
            row.bank_ifsc = value
            changed.append(field)
            continue
        ciphertext, masked = seal(_as_plaintext(value), kind=_PII_KINDS[field], settings=cfg)
        setattr(row, f"{field}_enc", ciphertext)
        setattr(row, f"{field}_masked", masked)
        changed.append(field)
    return changed


def masked_pii(row: EmployeePii) -> EmployeePiiMasked:
    """The masked projection of a sealed PII row — the default, non-decrypting read shape."""
    return EmployeePiiMasked(
        date_of_birth=row.date_of_birth_masked,
        personal_email=row.personal_email_masked,
        personal_phone=row.personal_phone_masked,
        pan=row.pan_masked,
        aadhaar=row.aadhaar_masked,
        uan=row.uan_masked,
        esi=row.esi_masked,
        bank_account=row.bank_account_masked,
        bank_ifsc=row.bank_ifsc,
    )


#: Which fields the update audit before/after snapshots record. Deliberately the plain
#: employment columns — none of them sensitive.
_TRACKED_FIELDS = (
    "full_name",
    "designation",
    "job_title",
    "department_id",
    "location_code",
    "employment_type",
    "work_mode",
    "date_of_joining",
    "date_of_exit",
    "reporting_manager_id",
    "dotted_line_manager_id",
)


def _hierarchy_snapshot(employee: Employee) -> dict[str, Any]:
    """The full hierarchy state of `employee` **after** the change — never a diff.

    Carried on all three employee events (ADR-0006 §2): a consumer that missed or reordered an
    event converges on the next one it sees, and never needs to replay history. `display_name`
    is the wire name approval's copy uses — the column here is `full_name`, which is
    deliberately not among employee's sealed PII fields (`app/models.py`), so copying it moves
    no ciphertext. Reading `hierarchy_version` back only works because the caller has already
    flushed and refreshed the row past the version trigger (`app/models.py`
    `trg_employees_hierarchy_version`) — building this from a Python-side attribute the ORM set
    itself would carry a stale value.
    """
    return {
        "employee_id": str(employee.id),
        "reporting_manager_id": (
            str(employee.reporting_manager_id) if employee.reporting_manager_id else None
        ),
        "dotted_line_manager_id": (
            str(employee.dotted_line_manager_id) if employee.dotted_line_manager_id else None
        ),
        "status": employee.status,
        "location_code": employee.location_code,
        "display_name": employee.full_name,
        "employee_no": employee.employee_no,
        "hierarchy_version": employee.hierarchy_version,
    }


async def _assert_manager_exists(
    session: AsyncSession,
    org_id: uuid.UUID,
    manager_id: uuid.UUID | None,
    *,
    label: str = "reporting manager",
) -> None:
    """A reporting (or dotted-line) manager must be an employee of the same organization.

    Enforced in application code rather than by a foreign key for `reporting_manager_id` (the
    manager link is a plain UUID, and the check has to be tenant-scoped: naming an employee id
    from another organization must read as "not found here", never resolve). The composite
    foreign key covers `dotted_line_manager_id` at the database level too, but this still runs
    for it so the failure is the same friendly 404 rather than an `IntegrityError`.
    """
    if manager_id is None:
        return
    manager = await selectors.get_employee(session, org_id, manager_id)
    if manager is None:
        raise NotFoundProblem(f"That {label} is not an employee of this organization")


async def _assert_no_cycle(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee: Employee,
    proposed_manager_id: uuid.UUID,
    *,
    label: str,
) -> None:
    """Refuse a manager change that would close a reporting cycle, make the chain unroutably
    deep, or write into a cycle that already exists elsewhere in the data (ADR-0006 §2, data
    model §4.3). Applies to both `reporting_manager_id` and `dotted_line_manager_id`: both walk
    the same *reporting* edges upward from the proposed manager, and both are refused the same
    way for whichever of the three reasons applies.

    The three are reported with distinct messages on purpose — they are different defects with
    different fixes, and collapsing them into one "reporting cycle" message (as this used to)
    is actively misleading for the one that is not a cycle at all: a chain that is simply too
    long to route is not looping, and telling an operator it is sends them looking for a loop
    that does not exist.
    """
    walk = await selectors.walk_manager_chain(session, org_id, employee.id, proposed_manager_id)
    if walk.existing_cycle:
        # The data already contained a cycle — possible, since no check has ever existed until
        # now. Writing into it would only make it worse, so it is refused too, but this is worth
        # a line an operator can find: it means some *other* row needs fixing, not this one.
        log.warning(
            "employee.hierarchy.existing_cycle",
            organization_id=str(org_id),
            employee_id=str(employee.id),
            proposed_manager_id=str(proposed_manager_id),
        )
    if walk.reaches_employee:
        raise BusinessRuleProblem(
            f"Making {proposed_manager_id} the {label} of {employee.id} would create a "
            "reporting cycle"
        )
    if walk.too_deep:
        raise BusinessRuleProblem(
            f"Making {proposed_manager_id} the {label} of {employee.id} would push the "
            f"reporting chain past {selectors.MAX_MANAGER_CHAIN_DEPTH} rungs, which is too deep "
            "to route"
        )
    if walk.existing_cycle:
        raise BusinessRuleProblem(
            f"Cannot make {proposed_manager_id} the {label} of {employee.id}: that manager's "
            "existing reporting chain already contains a cycle unrelated to this change"
        )


async def create_employee(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: EmployeeIn,
    *,
    request: Request | None = None,
) -> Employee:
    """Onboard an employee. Starts on probation; the lifecycle is the only way out of it."""
    # ADR-0008 §3: the membership link is frozen on this side. `account.memberships.employee_id`
    # is authoritative and account is its only writer, because deciding which person a platform
    # principal *is* is an authorization decision a product service must not be able to make.
    # Refused loudly and before anything else runs: a rejected request is not a change, so this
    # writes no audit row and stages no event. The message names the endpoint that does work,
    # because the whole point of refusing rather than ignoring is that the caller finds out where
    # to go.
    if payload.membership_id is not None:
        raise BusinessRuleProblem(
            "membership_id is no longer accepted here: the employee link is owned by "
            "account-service. Link this employee with "
            "PATCH /api/v1/members/{membership_id} instead."
        )

    if await selectors.get_by_employee_no(session, ctx.organization_id, payload.employee_no):
        raise ConflictProblem(f"An employee numbered {payload.employee_no!r} already exists")

    await _assert_manager_exists(session, ctx.organization_id, payload.reporting_manager_id)
    await _assert_manager_exists(
        session, ctx.organization_id, payload.dotted_line_manager_id, label="dotted-line manager"
    )
    # No cycle check here: the new row's id does not exist yet and the payload cannot name it
    # (`EmployeeIn` accepts no `id`), so nothing already in the organization can point at it —
    # a fresh employee cannot be part of a cycle at the moment it is created.

    employee = Employee(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        status=EmploymentStatus.PROBATION.value,
        # `pii` is a nested contract, sealed into its own row below — never a column on the
        # employment record. `membership_id` is refused above and is excluded here as well, so
        # this path cannot write the frozen column even if that guard is ever moved or relaxed
        # (ADR-0008 §3) — the value is always `None` by the time we get here, and stating it
        # keeps the mass assignment from quietly becoming a writer again.
        **payload.model_dump(exclude={"pii", "membership_id"}),
    )
    session.add(employee)
    # `hierarchy_version`'s server default is fetched back automatically here (SQLAlchemy 2.0's
    # `eager_defaults="auto"` appends it to the INSERT's RETURNING clause on Postgres) — no
    # explicit refresh needed on the create path, unlike update and transition below, where the
    # version comes from the `BEFORE UPDATE` trigger rather than the column's own default.
    await session.flush()

    sealed_fields: list[str] = []
    if payload.pii is not None:
        pii_row = EmployeePii(
            organization_id=ctx.organization_id,
            employee_id=employee.id,
            created_by=str(ctx.principal.sub),
        )
        sealed_fields = _apply_pii(pii_row, payload.pii)
        session.add(pii_row)
        await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="employee.onboarded",
        entity="employee",
        entity_id=employee.id,
        # Field *names* only — never a sealed value or a plaintext one in an audit payload.
        after={
            "employee_no": employee.employee_no,
            "full_name": employee.full_name,
            "pii_fields": sorted(sealed_fields),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_EMPLOYEE_ONBOARDED,
        aggregate_type="employee",
        aggregate_id=employee.id,
        payload={
            "organization_id": str(ctx.organization_id),
            # Always null since ADR-0008 §3 froze the column — kept on the wire rather than
            # removed because dropping a published key is a breaking contract change for a
            # benefit nothing has asked for, and no consumer reads it. It goes when the later
            # ADR drops the column.
            "membership_id": str(employee.membership_id) if employee.membership_id else None,
            # The event names which sensitive fields were set, never their contents.
            "pii_fields": sorted(sealed_fields),
            # Full hierarchy state, not a diff (ADR-0006 §2) — includes employee_id, employee_no
            # and status, so they are not repeated above.
            **_hierarchy_snapshot(employee),
        },
        actor=actor_for(ctx),
    )
    return employee


async def update_employee(
    session: AsyncSession,
    ctx: ScopeContext,
    employee: Employee,
    payload: EmployeePatch,
    *,
    request: Request | None = None,
) -> Employee:
    """Patch the plain employment fields, and seal any sensitive fields supplied.

    Lifecycle status is not among the plain fields by construction. Sensitive fields arrive under
    `pii`, are sealed into the 1:1 `employee_pii` row (created on first use), and never appear as
    a column here.
    """
    # `pii` is handled separately; the plain-field changes must not include the nested contract.
    changes = payload.model_dump(exclude_unset=True, exclude={"pii"})

    if "reporting_manager_id" in changes:
        new_manager = changes["reporting_manager_id"]
        if new_manager == employee.id:
            raise BusinessRuleProblem("An employee cannot report to themselves")
        await _assert_manager_exists(session, ctx.organization_id, new_manager)
        if new_manager is not None:
            await _assert_no_cycle(
                session, ctx.organization_id, employee, new_manager, label="reporting manager"
            )

    if "dotted_line_manager_id" in changes:
        new_dotted = changes["dotted_line_manager_id"]
        if new_dotted == employee.id:
            raise BusinessRuleProblem("An employee cannot be their own dotted-line manager")
        await _assert_manager_exists(
            session, ctx.organization_id, new_dotted, label="dotted-line manager"
        )
        if new_dotted is not None:
            await _assert_no_cycle(
                session, ctx.organization_id, employee, new_dotted, label="dotted-line manager"
            )

    if "date_of_exit" in changes:
        # `transition_status` auto-stamps today's date on the `exited` transition and its own
        # comment says "a correction is a later update" — this is that later update. Gated on
        # the employee's *current* status rather than being a free-form date: correcting an exit
        # date only makes sense once someone has actually exited, and PATCH never changes status
        # itself (that is the transition endpoint's job alone), so `employee.status` here is
        # unambiguously the record's status before this patch and after it.
        if employee.status != EmploymentStatus.EXITED.value:
            raise BusinessRuleProblem(
                "date_of_exit can only be corrected on an employee who has exited"
            )
        new_exit = changes["date_of_exit"]
        joining = changes.get("date_of_joining", employee.date_of_joining)
        if new_exit is not None and joining is not None and new_exit < joining:
            raise BusinessRuleProblem("date_of_exit cannot be before date_of_joining")

    before = {f: employee.as_dict().get(f) for f in _TRACKED_FIELDS}
    for field, value in changes.items():
        setattr(employee, field, value)
    employee.updated_by = str(ctx.principal.sub)

    sealed_fields: list[str] = []
    if payload.pii is not None:
        pii_row = await selectors.get_employee_pii(session, ctx.organization_id, employee.id)
        if pii_row is None:
            pii_row = EmployeePii(
                organization_id=ctx.organization_id,
                employee_id=employee.id,
                created_by=str(ctx.principal.sub),
            )
            session.add(pii_row)
        else:
            pii_row.updated_by = str(ctx.principal.sub)
        sealed_fields = _apply_pii(pii_row, payload.pii)

    # Flush so the `UPDATE` actually reaches Postgres and the `BEFORE UPDATE` trigger runs, then
    # refresh the whole row: the ORM has no idea a trigger touched `hierarchy_version`, so it
    # would otherwise still read the stale, pre-write value, and a flush also expires any other
    # server-computed column (`updated_at`'s `onupdate`) that a *narrower* refresh would leave
    # expired for `as_dict()` to trip over below.
    await session.flush()
    await session.refresh(employee)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="employee.updated",
        entity="employee",
        entity_id=employee.id,
        before=before,
        # Plain fields snapshot their values; sensitive fields contribute names only.
        after={
            **{f: employee.as_dict().get(f) for f in _TRACKED_FIELDS},
            "pii_fields": sorted(sealed_fields),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_EMPLOYEE_UPDATED,
        aggregate_type="employee",
        aggregate_id=employee.id,
        payload={
            "organization_id": str(ctx.organization_id),
            # Kept beside the full state below: a consumer that only wants to know *what*
            # changed still has it, without decoding a diff from two snapshots.
            "changed": sorted([*changes, *(["pii"] if sealed_fields else [])]),
            "pii_fields": sorted(sealed_fields),
            **_hierarchy_snapshot(employee),
        },
        actor=actor_for(ctx),
    )
    return employee


async def transition_status(
    session: AsyncSession,
    ctx: ScopeContext,
    employee: Employee,
    target: str,
    *,
    reason: str | None = None,
    request: Request | None = None,
) -> Employee:
    """Move an employee to a new lifecycle state, refusing any illegal step.

    The legality decision belongs to the pure state machine in `domain.py`; this function is
    only the transaction around it — validate, apply, audit, stage the event. An illegal
    transition surfaces as a business-rule problem naming both states.
    """
    current = EmploymentStatus(employee.status)
    to = EmploymentStatus(target)
    try:
        assert_can_transition(current, to)
    except IllegalTransition as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    employee.status = to.value
    if to is EmploymentStatus.EXITED and employee.date_of_exit is None:
        # An exit without a date is a record nobody can reconcile against payroll later. The
        # transition endpoint carries no date, so stamp today; a correction is a later update.
        employee.date_of_exit = datetime.now(UTC).date()
    employee.updated_by = str(ctx.principal.sub)

    # `status` is one of the six hierarchy fields the version trigger watches. Flush so the
    # `UPDATE` runs and the trigger fires, then refresh the whole row — see `update_employee`
    # for why this cannot be a Python-side attribute read or a narrower refresh.
    await session.flush()
    await session.refresh(employee)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="employee.transitioned",
        entity="employee",
        entity_id=employee.id,
        before={"status": current.value},
        after={"status": to.value},
        reason=reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_EMPLOYEE_TRANSITIONED,
        aggregate_type="employee",
        aggregate_id=employee.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "from": current.value,
            "to": to.value,
            **_hierarchy_snapshot(employee),
        },
        actor=actor_for(ctx),
    )
    return employee


async def unmask_employee_pii(
    session: AsyncSession,
    ctx: ScopeContext,
    employee: Employee,
    fields: Sequence[str],
    *,
    request: Request | None = None,
) -> EmployeePiiUnmasked:
    """Decrypt the requested sensitive fields — the one place plaintext comes back.

    Reaching here means the caller held `hrms.people.employee.read_sensitive` (no step-up
    is required, ADR-0023 addendum), and the route re-applied their scope to this one employee.
    Unmasking is itself an auditable read, so this writes an `entity.sensitive_read` row **in the
    caller's
    transaction** — the first real caller of `record_sensitive_read`, closing the gap the backlog
    records. The audit names the fields asked for and never their values.

    A field with nothing sealed comes back `None`; the audit still records that it was requested,
    because the auditable act is the attempt to read, not whether a value happened to exist.
    """
    pii_row = await selectors.get_employee_pii(session, ctx.organization_id, employee.id)

    revealed: dict[str, object] = {}
    if pii_row is not None:
        cfg = settings()
        for field in fields:
            ciphertext = getattr(pii_row, f"{field}_enc")
            if ciphertext is None:
                continue
            plaintext = open_sealed(ciphertext, settings=cfg)
            # date_of_birth was sealed as its ISO string; hand it back as a real date.
            if field == "date_of_birth":
                revealed[field] = date.fromisoformat(plaintext)
            else:
                revealed[field] = plaintext

    # The read is audited whether or not a value was present — naming the fields, never contents.
    record_sensitive_read(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        entity="employee",
        entity_id=employee.id,
        fields=sorted(fields),
        request=request,
    )
    return EmployeePiiUnmasked.model_validate(revealed)
