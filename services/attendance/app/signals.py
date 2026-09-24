"""The write side of the anti-spoofing stack (R22): capture a punch's signals, judge them against
the organization's rules, seal what is personal, and reveal it again only on purpose.

This is the writes half of `services.py` for one concern, split out because `services.py` is
already the longest module here and nothing in it needs to know how a flag is decided. It sits
under `services` (which calls `record_punch` from the two punch functions) and imports nothing
above `selectors`.

**Observe-only, and off by default.** A rule that fires writes a flag; it never refuses, delays or
alters the punch — a client that lies about a signal is what the rules exist to notice, and a
punch that failed because of one would be a way to lock an employee out of their own attendance.
An organization with no settings row has every rule off, and with no rule on *nothing is stored*:
`record_punch` reads the settings and returns. Turning a rule on is an administrative act on
`hrms.time.shift.write:ORG`, audited with its before and after.

**What is personal, and how it is kept.** The IP address and the device id are sealed with
`platform_core.pii.seal` (AES-256-GCM, bound to this row and field), stored beside a mask, and
returned only by `unmask_signal`, which is guarded by `hrms.time.attendance.read_sensitive` and a
second factor and writes an `entity.sensitive_read` audit row **every time**, including when there
was nothing to reveal — the audited act is the attempt to read. The device id is also kept as a
keyed blind index, for the one question (has someone else used this device?) that has to be asked
of a value nobody may search. A flag's `detail` never holds an address, a device id or another
employee's id.

**Where the client IP comes from.** The same function the audit trail uses for its own `ip`
column (`platform_core.audit._client_ip`): the first `X-Forwarded-For` entry, else the socket peer.
That is deliberately not re-implemented here, so this service cannot come to trust a different
header than the rest of the platform. The trust in that header is the gateway's to establish, not
this module's — until it does, a client that can reach the service with its own forwarded header
can present any address, and `ip_outside_allowed` inherits exactly that weakness. Because the rules
only ever flag, the cost of the gap is a missed flag, never a refused punch.

**No network call happens inside the transaction**; the two extra reads (previous located punch,
other users of a device) are indexed lookups, and only rules that are on run their read.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.config import settings
from app.domain import (
    FlagReason,
    assess_travel,
    classify_user_agent,
    ip_in_any,
    parse_ip,
)
from app.models import (
    AttendanceFlag,
    AttendanceRecord,
    AuditLog,
    PunchSignal,
    SignalSettings,
)
from app.schemas import SignalSettingsIn, SignalUnmasked
from platform_core.audit import _client_ip, record_audit, record_sensitive_read
from platform_core.auth import ScopeContext
from platform_core.logging import get_logger
from platform_core.pii import blind_index, open_sealed, seal

log = get_logger("attendance.signals")

#: The `kind` `platform_core.pii` masks with. Both the IP and the device id take the generic mask
#: (bullets and the last two characters): neither has a conventional "tail" a person would
#: recognise, and the point of the mask is that a reviewer can tell rows apart, not read them.
_MASK_KIND = "generic"
#: Domain-separates the blind index from every other use of the key, per `blind_index`'s `kind`.
_INDEX_KIND = "attendance.device_id"

ACTION_FLAGGED = "attendance.flagged"
ACTION_SETTINGS_UPDATED = "attendance.signal_settings.updated"


def seal_aad(organization_id: uuid.UUID, signal_id: uuid.UUID, field: str) -> bytes:
    """What a sealed value is bound to: this tenant, this signal row and this field.

    GCM authenticates but does not encrypt it, and refuses to open the envelope unless the same
    bytes are presented again — so a ciphertext lifted onto another row, or an `ip` envelope
    presented as a `device_id`, fails to open instead of decrypting to somebody else's value.
    """
    return f"attendance.punch_signal:{organization_id}:{signal_id}:{field}".encode()


def device_index_for(device_id_hash: str) -> str:
    """The keyed blind index the shared-device rule looks devices up by — never the device id
    itself. Public so the rule's own tests seed rows the same way the write path does."""
    return blind_index(device_id_hash, kind=_INDEX_KIND, settings=settings())


def _rules_on(cfg: SignalSettings) -> bool:
    return (
        cfg.detect_mock_location
        or cfg.detect_impossible_travel
        or cfg.detect_shared_device
        or cfg.detect_ip_outside_allowed
    )


def _settings_snapshot(cfg: SignalSettings | None) -> dict[str, Any]:
    """The organization's rules as an audit-friendly dict. `None` is the all-off default."""
    if cfg is None:
        return SignalSettingsIn().model_dump()
    return SignalSettingsIn.model_validate(cfg, from_attributes=True).model_dump()


# --------------------------------------------------------------------- settings


async def put_settings(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: SignalSettingsIn,
    *,
    request: Request | None = None,
) -> SignalSettings:
    """Replace the organization's rules with `payload`, and audit what they were and are.

    An upsert on the unique `(organization_id)`, so two administrators saving for the first time
    at once end with one row and one of the two bodies, never a unique-violation 500. `PUT` is
    idempotent: the same body twice leaves the same state (the second still writes an audit row —
    somebody pressed save).
    """
    existing = await selectors.get_signal_settings(session, ctx.organization_id)
    before = _settings_snapshot(existing)

    values = payload.model_dump()
    stmt = pg_insert(SignalSettings).values(
        id=uuid.uuid4(),
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        updated_by=str(ctx.principal.sub),
        **values,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SignalSettings.organization_id],
        set_={**values, "updated_by": str(ctx.principal.sub), "updated_at": func.now()},
    )
    await session.execute(stmt)
    # Re-read past the identity map: the upsert bypassed it, so a row loaded above is stale.
    row = (
        await session.execute(
            select(SignalSettings)
            .where(
                SignalSettings.organization_id == ctx.organization_id,
                SignalSettings.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=ACTION_SETTINGS_UPDATED,
        entity="signal_settings",
        entity_id=row.id,
        before=before,
        after=_settings_snapshot(row),
        request=request,
    )
    return row


# --------------------------------------------------------------------- capture and judge


async def record_punch(
    session: AsyncSession,
    ctx: ScopeContext,
    record: AttendanceRecord,
    *,
    kind: str,
    punched_at: datetime,
    lat: Decimal | None,
    lng: Decimal | None,
    accuracy_m: Decimal | None,
    mock_location: bool | None,
    device_id_hash: str | None,
    request: Request | None,
) -> list[AttendanceFlag]:
    """Store one punch's signals and flag it against the organization's rules.

    Called from `services.check_in` and `services.check_out` inside the punch's own transaction, so
    the evidence commits with the punch it describes or not at all. Returns the flags raised (the
    punch's caller ignores them: the employee is never told, and the punch is not affected).

    The rules that need history run **before** this punch's own row is inserted — the previous
    located punch and the other users of a device are read while this one does not exist yet, so
    neither can find the punch being judged.
    """
    cfg = await selectors.get_signal_settings(session, ctx.organization_id)
    if cfg is None or not _rules_on(cfg):
        return []

    ip = parse_ip(_client_ip(request)) if request is not None else None
    ua_class = classify_user_agent(request.headers.get("user-agent") if request else None)
    pii_settings = settings()
    device_index = device_index_for(device_id_hash) if device_id_hash else None

    reasons: dict[FlagReason, dict[str, Any]] = {}

    if cfg.detect_mock_location and mock_location is True:
        reasons[FlagReason.MOCK_LOCATION] = {}

    if cfg.detect_impossible_travel and lat is not None and lng is not None:
        previous = await selectors.get_previous_located_signal(
            session, ctx.organization_id, record.employee_id, before=punched_at
        )
        # Both coordinates are non-null by the selector's predicate; the guard is for the type
        # checker and costs nothing.
        if previous is not None and previous.lat is not None and previous.lng is not None:
            verdict = assess_travel(
                from_lat=float(previous.lat),
                from_lng=float(previous.lng),
                from_accuracy_m=float(previous.accuracy_m) if previous.accuracy_m else None,
                from_at=previous.punched_at,
                to_lat=float(lat),
                to_lng=float(lng),
                to_accuracy_m=float(accuracy_m) if accuracy_m else None,
                to_at=punched_at,
                max_speed_kmh=float(cfg.max_travel_speed_kmh),
            )
            if verdict.impossible:
                reasons[FlagReason.IMPOSSIBLE_TRAVEL] = {
                    "distance_m": round(verdict.distance_m),
                    "effective_m": round(verdict.effective_m),
                    "elapsed_s": round(verdict.elapsed_s),
                    "speed_kmh": round(verdict.speed_kmh),
                    "threshold_kmh": cfg.max_travel_speed_kmh,
                    "from_kind": previous.kind,
                }

    if cfg.detect_shared_device and device_index is not None:
        others = await selectors.count_other_device_users(
            session,
            ctx.organization_id,
            device_index=device_index,
            employee_id=record.employee_id,
            since=punched_at - timedelta(hours=cfg.shared_device_window_hours),
        )
        if others:
            # A count, never who: a manager reading this flag must not learn which employees
            # outside their own team used the same device.
            reasons[FlagReason.SHARED_DEVICE] = {
                "other_employees": others,
                "window_hours": cfg.shared_device_window_hours,
            }

    if (
        cfg.detect_ip_outside_allowed
        and record.work_mode == "office"
        and ip is not None
        and cfg.allowed_cidrs
        and not ip_in_any(ip, cfg.allowed_cidrs)
    ):
        reasons[FlagReason.IP_OUTSIDE_ALLOWED] = {"allowed_ranges": len(cfg.allowed_cidrs)}

    signal_id = uuid.uuid4()
    signal = PunchSignal(
        id=signal_id,
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        attendance_id=record.id,
        employee_id=record.employee_id,
        kind=kind,
        punched_at=punched_at,
        lat=lat,
        lng=lng,
        accuracy_m=accuracy_m,
        mock_location=mock_location,
        user_agent_class=ua_class.value,
        device_index=device_index,
    )
    if ip is not None:
        signal.ip_enc, signal.ip_masked = seal(
            ip,
            kind=_MASK_KIND,
            settings=pii_settings,
            aad=seal_aad(ctx.organization_id, signal_id, "ip"),
        )
    if device_id_hash:
        signal.device_id_enc, signal.device_id_masked = seal(
            device_id_hash,
            kind=_MASK_KIND,
            settings=pii_settings,
            aad=seal_aad(ctx.organization_id, signal_id, "device_id"),
        )
    session.add(signal)
    # The flags' foreign key needs the signal row to exist first.
    await session.flush()

    flags = [
        AttendanceFlag(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            attendance_id=record.id,
            signal_id=signal_id,
            employee_id=record.employee_id,
            reason_code=reason.value,
            detail=detail,
        )
        for reason, detail in reasons.items()
    ]
    if flags:
        session.add_all(flags)
        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action=ACTION_FLAGGED,
            entity="attendance",
            entity_id=record.id,
            # Reason codes and the punch kind — nothing personal, no address, no device id.
            after={"kind": kind, "reasons": sorted(r.value for r in reasons)},
            request=request,
        )
        log.info(
            "attendance.punch.flagged",
            organization_id=str(ctx.organization_id),
            attendance_id=str(record.id),
            kind=kind,
            reasons=sorted(r.value for r in reasons),
        )
    return flags


# --------------------------------------------------------------------- unmask


async def unmask_signal(
    session: AsyncSession,
    ctx: ScopeContext,
    signal: PunchSignal,
    fields: Sequence[str],
    *,
    request: Request | None = None,
) -> SignalUnmasked:
    """Open the requested sealed values — the one place plaintext comes back.

    Reaching here means the caller held `hrms.time.attendance.read_sensitive`, stepped up with a
    second factor, and the route re-applied their scope to this one punch. The read is audited in
    the caller's transaction **whether or not a value existed**, naming the fields and never their
    contents, so a revealed value can never exist without a record that it was revealed. A value
    that cannot be opened (a wrong key, a tampered envelope) raises `PiiError`: nothing is
    revealed, the transaction — audit row included — rolls back, and the caller gets the platform's
    generic 500 rather than a message about ciphertext.
    """
    pii_settings = settings()
    revealed: dict[str, str | None] = {"ip": None, "device_id": None}
    sealed = {"ip": signal.ip_enc, "device_id": signal.device_id_enc}
    for field in fields:
        ciphertext = sealed[field]
        if ciphertext is None:
            continue
        revealed[field] = open_sealed(
            ciphertext,
            settings=pii_settings,
            aad=seal_aad(ctx.organization_id, signal.id, field),
        )

    record_sensitive_read(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        entity="punch_signal",
        entity_id=signal.id,
        fields=sorted(fields),
        request=request,
    )
    return SignalUnmasked(**revealed)
