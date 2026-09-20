"""Request and response contracts for the employee service.

This module is the published API surface: a change here is a contract change, and the frontend
generates its client from it.

Sensitive PII (date of birth, personal contact, PAN/Aadhaar/UAN/ESI, bank details) is accepted
as **plaintext on the way in** and returned only as a **mask on the way out** — never the
plaintext, never the ciphertext. The one exception is `bank_ifsc`, a public bank branch code
that is not a secret and is shown whole. The unmasked plaintext comes back from exactly one
place, `EmployeePiiUnmasked`, served by the stepped-up, audited unmask endpoint.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

#: The sensitive fields a caller may ask the unmask endpoint to reveal. `bank_ifsc` is absent on
#: purpose: it is non-sensitive and already returned whole in the masked view, so there is
#: nothing to unmask.
SensitiveField = Literal[
    "date_of_birth",
    "personal_email",
    "personal_phone",
    "pan",
    "aadhaar",
    "uan",
    "esi",
    "bank_account",
]

EmployeeNo = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]
EmploymentType = Literal["full_time", "part_time", "contract", "intern"]
WorkMode = Literal["office", "field", "remote", "hybrid"]
Status = Literal["probation", "active", "on_notice", "exited"]
#: Only the non-terminal targets a transition may name — `probation` is an entry state a
#: transition never moves *to*, so it is excluded from the request contract.
TransitionTarget = Literal["active", "on_notice", "exited"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


class EmployeePiiIn(BaseModel):
    """Sensitive PII supplied as **plaintext**. Every field optional: on create it is what is
    known; on patch, present means "seal this new value", absent means "leave it alone".

    The service seals each provided value before it touches the database — the plaintext lives
    only for the length of that call and is never stored, logged, audited or put in an event.
    """

    date_of_birth: date | None = None
    personal_email: str | None = Field(default=None, max_length=254)
    personal_phone: str | None = Field(default=None, max_length=20)
    pan: str | None = Field(default=None, max_length=10)
    aadhaar: str | None = Field(default=None, max_length=12)
    uan: str | None = Field(default=None, max_length=12)
    esi: str | None = Field(default=None, max_length=17)
    bank_account: str | None = Field(default=None, max_length=18)
    #: A public branch code, stored and returned whole — not sensitive.
    bank_ifsc: str | None = Field(default=None, max_length=11)


class EmployeeIn(BaseModel):
    """Create an employment record.

    `status` is not accepted: a new record always starts on probation, and the lifecycle is
    the only way it moves from there.
    """

    employee_no: EmployeeNo
    full_name: str = Field(min_length=1, max_length=200)
    designation: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    department_id: uuid.UUID | None = None
    location_code: str | None = Field(default=None, max_length=40)
    employment_type: EmploymentType = "full_time"
    work_mode: WorkMode = "office"
    date_of_joining: date | None = None
    reporting_manager_id: uuid.UUID | None = None
    #: A second, informational reporting line — stored but not routed on (ADR-0006 §2). Refused
    #: at write time if it would close a reporting cycle or push the chain past the depth cap,
    #: exactly like `reporting_manager_id` (`app/services.py::_assert_no_cycle`).
    dotted_line_manager_id: uuid.UUID | None = None
    #: **Declared so it can be refused** (ADR-0008 §3). `account.memberships.employee_id` is the
    #: authoritative link and account is its only writer; this service no longer accepts the
    #: reverse half at create. Supplying a non-null value raises a `BusinessRuleProblem` naming
    #: `PATCH /api/v1/members/{membership_id}` — see `app/services.py::create_employee`.
    #:
    #: The field stays here rather than being deleted because this model sets no `model_config`,
    #: so Pydantic's default `extra="ignore"` applies (verified, not assumed): delete it and a
    #: supplied `membership_id` is dropped during validation with no error at all, and an
    #: integration that has been sending it keeps "succeeding" while doing nothing. A field that
    #: refuses itself with a pointer is the loud option.
    membership_id: uuid.UUID | None = None
    #: Optional sensitive PII, sealed on write. Absent means the person has no PII row yet.
    pii: EmployeePiiIn | None = None


class EmployeePatch(BaseModel):
    """Partial update. Absent means "leave alone"; the lifecycle status is not editable here —
    it moves only through the transition endpoint and its state machine."""

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    designation: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    department_id: uuid.UUID | None = None
    location_code: str | None = Field(default=None, max_length=40)
    employment_type: EmploymentType | None = None
    work_mode: WorkMode | None = None
    date_of_joining: date | None = None
    reporting_manager_id: uuid.UUID | None = None
    dotted_line_manager_id: uuid.UUID | None = None
    #: Present to seal new sensitive values; only the fields set on it are touched.
    pii: EmployeePiiIn | None = None


class TransitionIn(BaseModel):
    to: TransitionTarget
    reason: str | None = Field(default=None, max_length=500)


class EmployeePiiMasked(BaseModel):
    """The safe projection of an employee's PII: masks only, plus the whole IFSC.

    This is the default read shape. Every value here is either a non-sensitive hint
    (`•••• •••• 3210`) or a field classified non-sensitive (`bank_ifsc`). It is built from the
    stored `_masked` columns, so producing it never decrypts anything.
    """

    model_config = ConfigDict(from_attributes=True)

    date_of_birth: str | None = None
    personal_email: str | None = None
    personal_phone: str | None = None
    pan: str | None = None
    aadhaar: str | None = None
    uan: str | None = None
    esi: str | None = None
    bank_account: str | None = None
    bank_ifsc: str | None = None


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: **Advisory and possibly stale** (ADR-0008 §3). Nothing writes this column any more, and
    #: nothing in the platform reads it: `account.memberships.employee_id` is the authoritative
    #: link and `GET /api/v1/members` is the truth. Kept on the read contract so the reconcile
    #: (ADR-0008 §8) and existing clients still see the estate's record of intent.
    membership_id: uuid.UUID | None
    employee_no: str
    full_name: str
    designation: str | None
    job_title: str | None
    department_id: uuid.UUID | None
    location_code: str | None
    employment_type: str
    work_mode: str
    date_of_joining: date | None
    reporting_manager_id: uuid.UUID | None
    dotted_line_manager_id: uuid.UUID | None
    status: str
    date_of_exit: date | None
    created_at: datetime
    #: Bumped by the database trigger whenever a hierarchy field changes (ADR-0006 §2). Read-only
    #: here — nothing accepts it on write, and the trigger ignores a client-supplied value even
    #: on a raw `UPDATE`.
    hierarchy_version: int
    #: Masked PII, when the caller loaded it. Never plaintext, never ciphertext. Null where the
    #: employee has no PII row, and on the list projection which does not join it.
    pii: EmployeePiiMasked | None = None


class PiiUnmaskIn(BaseModel):
    """Which sensitive fields to reveal. Naming them explicitly keeps an unmask a deliberate,
    minimal act — and the audit row records exactly this list."""

    fields: list[SensitiveField] = Field(min_length=1)


class EmployeePiiUnmasked(BaseModel):
    """The **only** response that carries plaintext PII, served by the stepped-up, audited
    unmask endpoint. Every field is optional: only the ones asked for (and present) come back."""

    date_of_birth: date | None = None
    personal_email: str | None = None
    personal_phone: str | None = None
    pan: str | None = None
    aadhaar: str | None = None
    uan: str | None = None
    esi: str | None = None
    bank_account: str | None = None


class HierarchyNodeOut(BaseModel):
    """The full hierarchy state of one employee — the same shape the events carry (ADR-0006 §2),
    served by the internal backfill endpoint so approval's copy can start from any single row
    without decoding history. `employee_id` and `display_name` are deliberate renames of `id`
    and `full_name`: they are the names approval's own copy uses on the wire."""

    employee_id: uuid.UUID
    reporting_manager_id: uuid.UUID | None
    dotted_line_manager_id: uuid.UUID | None
    status: str
    location_code: str | None
    display_name: str
    employee_no: str
    hierarchy_version: int


class HierarchyPage(BaseModel):
    """A keyset page of `HierarchyNodeOut`, ordered on `id`. `next_cursor` is `null` once there
    is nothing left to page through — the caller keeps requesting with `cursor=next_cursor`
    until it sees `null`, rather than inferring the end from the page size (ADR-0006 §2.1)."""

    items: list[HierarchyNodeOut]
    next_cursor: uuid.UUID | None


class EmployeeLinkOut(BaseModel):
    """One employee's recorded membership link — **two UUIDs and nothing else** (ADR-0008 §8).

    No name, no employee number, no status, no PII. Deliberately strictly less than
    `HierarchyNodeOut`, so the bulk-PII exposure the security pass flagged on the hierarchy
    endpoint does not exist on this one at all: the worst a holder of the shared internal-token
    secret can learn here is that two opaque identifiers are associated.

    `membership_id` is **nullable, and a null is not a filler value** — it is the payload. An
    employee that appears here with a null link tells the reconcile "this id is a real employee
    of this organization that simply has no link recorded", which is what separates a mistyped
    `employee_id` in account from an ordinary not-yet-linked one. See
    `selectors.employee_links_stmt` for why the unlinked rows travel.
    """

    employee_id: uuid.UUID
    membership_id: uuid.UUID | None


class EmployeeLinkPage(BaseModel):
    """A keyset page of `EmployeeLinkOut`, ordered on the employee's `id` — the same contract and
    the same termination rule as `HierarchyPage`: keep requesting with `cursor=next_cursor` until
    it comes back `null`, rather than inferring the end from the page size."""

    items: list[EmployeeLinkOut]
    next_cursor: uuid.UUID | None
