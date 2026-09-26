# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`d5a7c3e91f48` (the consent ledger) and its backfill, run for real against pre-ledger data.

Drives alembic down to the revision right before it, seeds the two legacy stores and the audience
the way production holds them — contacts (active, deleted, suppressed), suppressions of every
reason, preferences of every shape — then upgrades to head and checks each row landed where the
migration's own docstring says it should. Also: the backfill is idempotent (a second run writes
nothing), can be re-run for a late contact without touching a decision made since, existing
campaigns keep selecting exactly the same recipients, WhatsApp marketing consent is **not**
invented, the append-only revoke survives a re-run of the schema grants, there is exactly one
alembic head, and the upgrade/downgrade/upgrade round trip works.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import consent, selectors
from app.config import settings
from app.consent_backfill import LEGACY_CONTACT_EVIDENCE, backfill_legacy_consent
from app.models import SCHEMA
from platform_core import pii
from platform_core.auth import Scope
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
BEFORE = "c9a2e6f1b4d7"  # the revision right before d5a7c3e91f48  # pragma: allowlist secret
HEAD = "d5a7c3e91f48"  # pragma: allowlist secret
TABLES = (
    "consent_events",
    "campaign_recipients",
    "campaigns",
    "contact_list_members",
    "contact_lists",
    "contacts",
    "suppressions",
    "preferences",
    "audit_logs",
    "outbox",
)

ORG_A = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")
ORG_B = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000000b")


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
async def admin():  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.begin() as conn:
        existing = [
            t
            for t in TABLES
            if (await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{t}"})).scalar()
        ]
        await conn.execute(text(f"TRUNCATE {', '.join(f'{SCHEMA}.{t}' for t in existing)} CASCADE"))
    try:
        down = alembic("downgrade", BEFORE)
        assert down.returncode == 0, down.stderr[-2000:]
        yield engine
    finally:
        up = alembic("upgrade", "head")
        await engine.dispose()
        assert up.returncode == 0, up.stderr[-2000:]


# --------------------------------------------------------------------- seeding the legacy stores


def _sealed(address: str, kind: str = "email") -> tuple[bytes, str, str]:
    cfg = settings()
    enc, masked = pii.seal(address, kind=kind, settings=cfg)
    return enc, masked, pii.blind_index(address, kind=kind, settings=cfg)


async def _contact(
    conn, org: uuid.UUID, address: str, *, deleted: bool = False, days_ago: int = 30
) -> uuid.UUID:
    enc, masked, index = _sealed(address)
    contact_id = uuid.uuid4()
    when = datetime.now(UTC) - timedelta(days=days_ago)
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.contacts
                (id, organization_id, email_enc, email_masked, email_index, name, attributes,
                 created_at, updated_at, created_by, updated_by, deleted_at)
            VALUES (:id, :org, :enc, :masked, :idx, '', '{{}}'::jsonb, :ts, :ts, NULL, NULL, :del)
            """
        ),
        {
            "id": contact_id,
            "org": org,
            "enc": enc,
            "masked": masked,
            "idx": index,
            "ts": when,
            "del": datetime.now(UTC) if deleted else None,
        },
    )
    return contact_id


async def _suppression(conn, org: uuid.UUID, address: str, reason: str) -> None:
    enc, masked, index = _sealed(address)
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.suppressions
                (id, organization_id, email_enc, email_masked, email_index, reason,
                 created_at, updated_at, created_by, updated_by, deleted_at)
            VALUES (:id, :org, :enc, :masked, :idx, :reason, :ts, :ts, NULL, NULL, NULL)
            """
        ),
        {
            "id": uuid.uuid4(),
            "org": org,
            "enc": enc,
            "masked": masked,
            "idx": index,
            "reason": reason,
            "ts": datetime.now(UTC) - timedelta(days=10),
        },
    )


async def _preference(
    conn,
    org: uuid.UUID,
    recipient: str,
    channel: str,
    category: str,
    *,
    suppressed: bool = True,
    reason: str | None = None,
    deleted: bool = False,
) -> None:
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.preferences
                (id, organization_id, recipient, channel, category, suppressed, reason,
                 created_at, updated_at, created_by, updated_by, deleted_at)
            VALUES (:id, :org, :recipient, :channel, :category, :suppressed, :reason,
                    :ts, :ts, NULL, NULL, :del)
            """
        ),
        {
            "id": uuid.uuid4(),
            "org": org,
            "recipient": recipient,
            "channel": channel,
            "category": category,
            "suppressed": suppressed,
            "reason": reason,
            "ts": datetime.now(UTC) - timedelta(days=5),
            "del": datetime.now(UTC) if deleted else None,
        },
    )


async def _seed_legacy(conn) -> None:
    # Organization A's audience: two mailable contacts, one unsubscribed, one deleted, one whose
    # suppression is a manual one an operator added.
    await _contact(conn, ORG_A, "alice@example.com")
    await _contact(conn, ORG_A, "dave@example.com")
    await _contact(conn, ORG_A, "bob@example.com")
    await _contact(conn, ORG_A, "carol@example.com", deleted=True)
    await _contact(conn, ORG_A, "erin@example.com")
    await _suppression(conn, ORG_A, "bob@example.com", "unsubscribed")
    await _suppression(conn, ORG_A, "erin@example.com", "manual")
    await _suppression(conn, ORG_A, "frank@example.com", "bounced")  # no contact row at all
    await _suppression(conn, ORG_A, "gina@example.com", "complained")

    await _preference(conn, ORG_A, "+16505550001", "whatsapp", "*", reason="customer_stop")
    await _preference(conn, ORG_A, "+16505550002", "whatsapp", "marketing", reason="operator says")
    await _preference(conn, ORG_A, "x@example.com", "email", "billing")
    await _preference(conn, ORG_A, "+16505550003", "whatsapp", "*", suppressed=False)  # lifted
    await _preference(conn, ORG_A, str(uuid.uuid4()), "in_app", "*")  # no consent regime
    await _preference(conn, ORG_A, "+16505550004", "whatsapp", "*", deleted=True)

    # Organization B: one contact, nothing else.
    await _contact(conn, ORG_B, "alice@example.com")


async def _upgrade_and_read(admin) -> list[dict]:
    async with admin.begin() as conn:
        await _seed_legacy(conn)
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    return await _read_events(admin)


async def _read_events(admin) -> list[dict]:
    async with admin.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    f"SELECT organization_id, channel, purpose, status, source, evidence, "
                    f"subject_index, subject_enc, contact_id, recorded_by, idempotency_key "
                    f"FROM {SCHEMA}.consent_events"
                )
            )
        ).mappings()
        return [dict(r) for r in rows]


def _shape(events: list[dict], org: uuid.UUID = ORG_A) -> set[tuple[str, str, str, str]]:
    return {
        (e["channel"], e["purpose"], e["status"], e["source"])
        for e in events
        if e["organization_id"] == org
    }


# --------------------------------------------------------------------- what becomes what


async def test_every_suppression_becomes_an_opted_out_event_with_its_reason_mapped(admin) -> None:
    events = await _upgrade_and_read(admin)
    shape = _shape(events)
    # Unsubscribed and manual only ever stopped campaigns, so they opt out of marketing alone —
    # rule-driven transactional mail to those addresses carried on and still does.
    assert ("email", "marketing", "opted_out", "unsubscribe_link") in shape
    assert ("email", "transactional", "opted_out", "unsubscribe_link") not in shape
    assert ("email", "marketing", "opted_out", "admin") in shape
    # A bounce and a complaint are facts about the mailbox: both purposes.
    for source in ("bounce", "complaint"):
        assert ("email", "marketing", "opted_out", source) in shape
        assert ("email", "transactional", "opted_out", source) in shape


async def test_every_preference_refusal_becomes_opted_out_events_for_its_categorys_purposes(
    admin,
) -> None:
    events = await _upgrade_and_read(admin)
    by_index = {}
    for e in events:
        by_index.setdefault(e["subject_index"], []).append(e)
    cfg = settings()

    def refusals(recipient: str, kind: str) -> set[tuple[str, str, str]]:
        index = pii.blind_index(recipient, kind=kind, settings=cfg)
        return {(e["purpose"], e["status"], e["source"]) for e in by_index.get(index, [])}

    # `*` covers both purposes; a customer's own STOP is sourced as one.
    assert refusals("+16505550001", "phone") == {
        ("marketing", "opted_out", "inbound_keyword"),
        ("transactional", "opted_out", "inbound_keyword"),
    }
    # `marketing` covers marketing only; an operator's is sourced `admin`.
    assert refusals("+16505550002", "phone") == {("marketing", "opted_out", "admin")}
    # Any other bucket is a rule-authored transactional category.
    assert refusals("x@example.com", "email") == {("transactional", "opted_out", "admin")}
    # A lifted preference, a deleted one and an in-app one are not refusals.
    assert refusals("+16505550003", "phone") == set()
    assert refusals("+16505550004", "phone") == set()
    assert not [e for e in events if e["channel"] == "in_app"]


async def test_an_operators_free_text_reason_is_not_copied_into_the_ledger(admin) -> None:
    events = await _upgrade_and_read(admin)
    assert not [e for e in events if "operator says" in (e["evidence"] or "")]


async def test_active_unsuppressed_contacts_become_unverified_legacy_opt_ins(admin) -> None:
    events = await _upgrade_and_read(admin)
    legacy = [e for e in events if e["source"] == "legacy_import"]
    assert {(e["organization_id"], e["channel"], e["purpose"], e["status"]) for e in legacy} == {
        (ORG_A, "email", "marketing", "opted_in"),
        (ORG_B, "email", "marketing", "opted_in"),
    }
    # alice and dave in A, alice in B — not bob (unsubscribed), erin (manual), carol (deleted).
    assert len(legacy) == 3
    for event in legacy:
        assert event["evidence"] == LEGACY_CONTACT_EVIDENCE
        assert "unverified" in event["evidence"]
        assert event["contact_id"] is not None
        assert event["recorded_by"] is None


async def test_no_whatsapp_marketing_consent_is_invented(admin) -> None:
    """There are no legacy WhatsApp marketing sends to carry over, so nothing grandfathers one."""
    events = await _upgrade_and_read(admin)
    assert not [e for e in events if e["channel"] == "whatsapp" and e["status"] == "opted_in"]
    assert not [e for e in events if e["channel"] == "sms"]


async def test_the_tenants_stay_apart(admin) -> None:
    events = await _upgrade_and_read(admin)
    assert {e["organization_id"] for e in events} == {ORG_A, ORG_B}
    # The same address in two organizations is two subjects' worth of history, one each.
    alice = pii.blind_index("alice@example.com", kind="email", settings=settings())
    assert {e["organization_id"] for e in events if e["subject_index"] == alice} == {ORG_A, ORG_B}
    assert not [
        e for e in events if e["organization_id"] == ORG_B and e["source"] != "legacy_import"
    ]


async def test_the_sealed_address_is_copied_never_stored_in_the_clear(admin) -> None:
    events = await _upgrade_and_read(admin)
    cfg = settings()
    alice = pii.blind_index("alice@example.com", kind="email", settings=cfg)
    row = next(e for e in events if e["subject_index"] == alice and e["organization_id"] == ORG_A)
    assert pii.open_sealed(bytes(row["subject_enc"]), settings=cfg) == "alice@example.com"

    phone = pii.blind_index("+16505550001", kind="phone", settings=cfg)
    sealed_phone = next(e for e in events if e["subject_index"] == phone)
    assert pii.open_sealed(bytes(sealed_phone["subject_enc"]), settings=cfg) == "+16505550001"

    async with admin.begin() as conn:
        dump = (
            await conn.execute(
                text(f"SELECT string_agg(row_to_json(t)::text, ' ') FROM {SCHEMA}.consent_events t")
            )
        ).scalar_one()
    for plaintext in ("alice@", "dave@", "bob@", "frank@", "gina@", "+1650555000"):
        assert plaintext not in dump


# --------------------------------------------------------------------- behaviour after the backfill


async def test_existing_campaigns_keep_selecting_the_same_recipients(admin) -> None:
    """Before the ledger, an audience was every active contact minus the suppressed. It must still
    be — alice and dave — or the first campaign after deploy would reach nobody."""
    await _upgrade_and_read(admin)
    ctx = ctx_for(ORG_A, "engage.campaign.campaign.send", Scope.ORG)
    maker = async_sessionmaker(admin, expire_on_commit=False)
    async with maker() as session, session.begin():
        rows = await selectors.sendable_contacts(session, ctx)
    assert {r.email_masked for r in rows} == {
        pii.mask("alice@example.com", kind="email"),
        pii.mask("dave@example.com", kind="email"),
    }


async def test_nothing_blocked_before_is_allowed_after(admin) -> None:
    await _upgrade_and_read(admin)
    maker = async_sessionmaker(admin, expire_on_commit=False)
    async with maker() as session, session.begin():

        async def allowed(channel: str, purpose: str, address: str, **kw) -> bool:
            return (await consent.decide(session, ORG_A, channel, purpose, address, **kw)).allowed

        # Email: suppressions still refuse marketing; hard ones refuse transactional too.
        assert not await allowed("email", "marketing", "bob@example.com")
        assert not await allowed("email", "marketing", "erin@example.com")
        assert not await allowed("email", "marketing", "frank@example.com")
        assert not await allowed("email", "transactional", "frank@example.com")
        assert not await allowed("email", "transactional", "gina@example.com")
        # ...and what an unsubscribe never stopped still goes: an invoice to bob.
        assert await allowed("email", "transactional", "bob@example.com")
        # WhatsApp: a customer's STOP refuses everything; an operator's marketing preference
        # refuses marketing only.
        assert not await allowed("whatsapp", "transactional", "+16505550001")
        assert not await allowed("whatsapp", "marketing", "+16505550001")
        assert not await allowed("whatsapp", "marketing", "+16505550002")
        assert await allowed("whatsapp", "transactional", "+16505550002")
        # A `billing` preference refuses the billing category and nothing else.
        assert not await allowed("email", "transactional", "x@example.com", category="billing")
        # A number nobody ever recorded anything about needs an explicit opt-in for marketing.
        assert not await allowed("whatsapp", "marketing", "+16505559999")
        assert await allowed("whatsapp", "transactional", "+16505559999")


# --------------------------------------------------------------------- idempotency


async def test_a_second_run_writes_nothing(admin) -> None:
    before = await _upgrade_and_read(admin)
    async with admin.begin() as conn:
        counts = await conn.run_sync(backfill_legacy_consent)
    assert counts.total == 0
    assert (counts.suppressions, counts.preferences, counts.contacts) == (0, 0, 0)
    assert len(await _read_events(admin)) == len(before)


async def test_a_rerun_picks_up_a_late_contact_and_leaves_earlier_decisions_alone(admin) -> None:
    """The one real window: a contact the previous release created between the migration and the
    new code taking over has no ledger event, and would never be selected. A re-run finds it —
    and does not hand a legacy opt-in to an address that has since opted out."""
    before = await _upgrade_and_read(admin)
    async with admin.begin() as conn:
        await _contact(conn, ORG_A, "late@example.com", days_ago=1)
        await _contact(conn, ORG_A, "refused@example.com", days_ago=1)
    # The new code recorded a refusal for `refused` before the re-run.
    refused = consent.subject_for("email", "refused@example.com")
    maker = async_sessionmaker(admin, expire_on_commit=False)
    async with maker() as session, session.begin():
        await consent.append(
            session,
            organization_id=ORG_A,
            subject=refused,
            purposes=("marketing",),
            status="opted_out",
            source="admin",
            evidence="asked to stop",
        )

    async with admin.begin() as conn:
        counts = await conn.run_sync(backfill_legacy_consent)
    assert counts.contacts == 1
    assert counts.total == 1
    after = await _read_events(admin)
    assert len(after) == len(before) + 2  # the late opt-in, and the refusal written above
    async with maker() as session, session.begin():
        assert (
            await consent.decide(session, ORG_A, "email", "marketing", "late@example.com")
        ).allowed
        assert not (
            await consent.decide(session, ORG_A, "email", "marketing", "refused@example.com")
        ).allowed


async def test_every_backfilled_row_carries_a_deterministic_key(admin) -> None:
    events = await _upgrade_and_read(admin)
    keys = [e["idempotency_key"] for e in events]
    assert all(k and k.startswith("legacy:") for k in keys)
    assert len(set(keys)) == len(keys)


# --------------------------------------------------------------------- the migration itself


async def test_the_upgrade_on_an_empty_schema_writes_nothing_and_succeeds(admin) -> None:
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    assert await _read_events(admin) == []


async def test_upgrade_downgrade_upgrade_round_trips_and_rebuilds_the_ledger(admin) -> None:
    first = await _upgrade_and_read(admin)

    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.begin() as conn:
        assert (
            await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.consent_events"})
        ).scalar() is None
        columns = (
            await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'messages'"
                ),
                {"s": SCHEMA},
            )
        ).scalars()
        assert "suppression_reason" not in set(columns)
        # The legacy stores are untouched by either direction.
        assert (
            await conn.execute(text(f"SELECT count(*) FROM {SCHEMA}.suppressions"))
        ).scalar_one() == 4

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    assert len(await _read_events(admin)) == len(first)


async def test_there_is_exactly_one_alembic_head(admin) -> None:
    heads = alembic("heads")
    assert heads.returncode == 0, heads.stderr[-2000:]
    lines = [line for line in heads.stdout.splitlines() if "(head)" in line]
    assert len(lines) == 1, heads.stdout
    assert lines[0].startswith(HEAD)


async def test_the_append_only_revoke_survives_a_rerun_of_the_schema_grants(admin) -> None:
    """`apply_schema_grants` re-grants UPDATE and DELETE on every table each time alembic runs,
    so a revoke that lived only in the migration would be undone by the next `upgrade`."""
    for _ in range(2):
        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr[-2000:]
        role = settings().db_app_role or "opsgrids_notification"
        async with admin.begin() as conn:
            for privilege, expected in (
                ("SELECT", True),
                ("INSERT", True),
                ("UPDATE", False),
                ("DELETE", False),
            ):
                granted = (
                    await conn.execute(
                        text("SELECT has_table_privilege(:role, :tbl, :priv)"),
                        {"role": role, "tbl": f"{SCHEMA}.consent_events", "priv": privilege},
                    )
                ).scalar_one()
                assert granted is expected, privilege
