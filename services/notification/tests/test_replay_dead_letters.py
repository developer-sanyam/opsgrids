"""`app/replay_dead_letters.py` — the operator-run recovery tool, not the webhook route itself.

`test_inbound_webhook.py` covers the one happy-path replay (a single permanent error, parked and
then successfully replayed) as part of proving the webhook route's own dead-lettering. This file
covers what only shows up in the replay tool: two redeliveries racing the same permanently-broken
item, a row that keeps failing not stopping the row beside it, a successful replay actually
closing the loop against a later redelivery, a corrupted sealed payload, and the required
`--operator` audit trail.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app import replay_dead_letters, services
from app.models import AuditLog, InboundDeadLetter, InboundMessage, ProviderAccount
from platform_core.config import get_settings
from platform_core.pii import seal

pytestmark = pytest.mark.integration

SECRET = "ci-only-app-secret"
PHONE_NUMBER_ID = "106540352242922"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch):
    """See `test_inbound_webhook.py`'s identical fixture for why both sides of the cache have to
    be cleared — only `test_a_successful_replay_marks_processed_...` drives the route over real
    HTTP, but the settings cache is shared across the whole module."""
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "ci-only-verify-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _headers(body: bytes) -> dict[str, str]:
    return {"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)}


def _inbound_raw(*, wamid: str, sender: str = "16505551234", body: str = "hi") -> dict:
    return {
        "from": sender,
        "id": wamid,
        "timestamp": str(int(datetime.now(UTC).timestamp())),
        "type": "text",
        "text": {"body": body},
    }


def _payload(*, messages: list[dict], phone_number_id: str = PHONE_NUMBER_ID) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550783881",
                                "phone_number_id": phone_number_id,
                            },
                            "messages": messages,
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


async def _seed_dead_letter(
    session,
    *,
    organization_id: uuid.UUID,
    provider_message_id: str,
    raw_enc: bytes,
    kind: str = "inbound",
    occurred_at: datetime | None = None,
) -> InboundDeadLetter:
    row = InboundDeadLetter(
        organization_id=organization_id,
        created_by="system:test",
        kind=kind,
        provider_message_id=provider_message_id,
        error="ValueError",
        occurred_at=occurred_at or datetime.now(UTC),
        raw_enc=raw_enc,
    )
    session.add(row)
    await session.flush()
    return row


def _seal_inbound(**kwargs: object) -> bytes:
    raw = _inbound_raw(**kwargs)  # type: ignore[arg-type]
    sealed, _ = seal(json.dumps(raw), kind="generic", settings=services.settings())
    return sealed


# --------------------------------------------------------------------- (a) park-side dedupe


async def test_two_permanent_failures_on_the_same_item_park_one_row(session, org_id) -> None:
    """`record_dead_letter` upserts on (`organization_id`, `kind`, `provider_message_id`) with
    `ON CONFLICT DO NOTHING` — two redeliveries racing the same permanently-broken item, or a
    replay that fails again for a row a concurrent webhook already re-parked, must not raise a
    second `IntegrityError` here and must not produce two worklist rows for one item."""
    async with session.begin():
        await services.record_dead_letter(
            session,
            organization_id=org_id,
            kind="inbound",
            provider_message_id="wamid.DUPPARK",
            occurred_at=datetime.now(UTC),
            error=ValueError("first attempt"),
            raw=_inbound_raw(wamid="wamid.DUPPARK"),
        )
    async with session.begin():
        await services.record_dead_letter(
            session,
            organization_id=org_id,
            kind="inbound",
            provider_message_id="wamid.DUPPARK",
            occurred_at=datetime.now(UTC),
            error=ValueError("second attempt, same item"),
            raw=_inbound_raw(wamid="wamid.DUPPARK"),
        )

    async with session.begin():
        rows = (await session.execute(select(InboundDeadLetter))).scalars().all()
    assert len(rows) == 1


# --------------------------------------------------------------------- (b) one bad row, rest go on


async def test_a_row_that_keeps_failing_does_not_stop_the_others_and_both_are_audited(
    session, org_id, engines: None
) -> None:
    """Each row replays inside its own `session_scope` and its own `SAVEPOINT`. Before that, a
    shared transaction meant one bad row's raised exception aborted every statement queued after
    it, including the good row's own fold and both rows' audit writes."""
    bad_raw, _ = seal("not valid json at all {{{", kind="generic", settings=services.settings())

    async with session.begin():
        good = await _seed_dead_letter(
            session,
            organization_id=org_id,
            provider_message_id="wamid.GOODREPLAY",
            raw_enc=_seal_inbound(wamid="wamid.GOODREPLAY"),
        )
        bad = await _seed_dead_letter(
            session, organization_id=org_id, provider_message_id="wamid.BADREPLAY", raw_enc=bad_raw
        )

    replayed = await replay_dead_letters.replay(
        organization_id=org_id, operator="ops@opsgrids.example"
    )
    assert replayed == 1

    async with session.begin():
        remaining = (await session.execute(select(InboundDeadLetter))).scalars().all()
        messages = (await session.execute(select(InboundMessage))).scalars().all()
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "inbound_dead_letter.replayed")
                )
            )
            .scalars()
            .all()
        )

    assert {r.id for r in remaining} == {bad.id}
    assert {m.provider_message_id for m in messages} == {"wamid.GOODREPLAY"}
    assert {a.entity_id for a in audits} == {good.id, bad.id}
    good_audit = next(a for a in audits if a.entity_id == good.id)
    bad_audit = next(a for a in audits if a.entity_id == bad.id)
    assert good_audit.after["succeeded"] is True
    assert bad_audit.after["succeeded"] is False


# --------------------------------------------------------------------- (c) replay closes the loop


async def test_a_successful_replay_marks_processed_so_redelivery_is_a_duplicate_not_a_reparking(
    client, session, org_id
) -> None:
    """Once a replay folds a row and records `ProcessedEvent`, Meta redelivering the same original
    webhook body must be recognised as the duplicate it is — not folded a second time, and
    certainly not parked again."""
    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id="waba-1",
                phone_number_id=PHONE_NUMBER_ID,
                display_number="+15550783881",
                status="connected",
            )
        )

    raw = _inbound_raw(wamid="wamid.REPLAYTHENREDELIVER")
    sealed, _ = seal(json.dumps(raw), kind="generic", settings=services.settings())
    async with session.begin():
        await _seed_dead_letter(
            session,
            organization_id=org_id,
            provider_message_id="wamid.REPLAYTHENREDELIVER",
            raw_enc=sealed,
        )

    replayed = await replay_dead_letters.replay(
        organization_id=org_id, operator="ops@opsgrids.example"
    )
    assert replayed == 1

    async with session.begin():
        messages = (await session.execute(select(InboundMessage))).scalars().all()
    assert len(messages) == 1

    # The same body Meta originally sent, redelivered after the replay already closed it out.
    body = json.dumps(_payload(messages=[raw])).encode()
    response = await client.post("/webhooks/whatsapp", content=body, headers=_headers(body))
    assert response.status_code == 200

    async with session.begin():
        messages = (await session.execute(select(InboundMessage))).scalars().all()
        dead_letters = (
            await session.execute(select(func.count()).select_from(InboundDeadLetter))
        ).scalar_one()
    # Not re-folded — still exactly the one row the replay itself created.
    assert len(messages) == 1
    # And not re-parked either.
    assert dead_letters == 0


# --------------------------------------------------------------------- (d) a corrupted payload


async def test_a_pii_error_on_one_row_is_audited_and_does_not_block_the_others(
    session, org_id, engines: None
) -> None:
    """A `raw_enc` that no longer opens — a corrupted ciphertext, a rotated-out key — has to be
    this one row's own audited failure, not an exception that unwinds out of `replay()` and takes
    every row queued after it down with it."""
    async with session.begin():
        corrupted = await _seed_dead_letter(
            session,
            organization_id=org_id,
            provider_message_id="wamid.CORRUPTED",
            raw_enc=b"too-short-to-be-a-real-envelope",
        )
        good = await _seed_dead_letter(
            session,
            organization_id=org_id,
            provider_message_id="wamid.GOODAFTERPII",
            raw_enc=_seal_inbound(wamid="wamid.GOODAFTERPII"),
        )

    replayed = await replay_dead_letters.replay(
        organization_id=org_id, operator="ops@opsgrids.example"
    )
    assert replayed == 1

    async with session.begin():
        remaining = (await session.execute(select(InboundDeadLetter))).scalars().all()
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "inbound_dead_letter.replayed")
                )
            )
            .scalars()
            .all()
        )

    assert {r.id for r in remaining} == {corrupted.id}
    corrupted_audit = next(a for a in audits if a.entity_id == corrupted.id)
    good_audit = next(a for a in audits if a.entity_id == good.id)
    assert corrupted_audit.after["succeeded"] is False
    assert corrupted_audit.after["error_type"] == "PiiError"
    assert good_audit.after["succeeded"] is True


# --------------------------------------------------------------------- (e) the required operator


async def test_replay_requires_an_operator(org_id) -> None:
    with pytest.raises(ValueError):
        await replay_dead_letters.replay(organization_id=org_id, operator="")


async def test_replay_records_the_operator_on_the_audit_row(session, org_id, engines: None) -> None:
    async with session.begin():
        row = await _seed_dead_letter(
            session,
            organization_id=org_id,
            provider_message_id="wamid.OPERATORAUDIT",
            raw_enc=_seal_inbound(wamid="wamid.OPERATORAUDIT"),
        )

    replayed = await replay_dead_letters.replay(
        organization_id=org_id, operator="staff-4471", dead_letter_id=row.id
    )
    assert replayed == 1

    async with session.begin():
        audit = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "inbound_dead_letter.replayed")
                )
            )
            .scalars()
            .one()
        )
    assert audit.acting_as is not None
    assert "staff-4471" in audit.acting_as


async def test_the_cli_refuses_to_run_without_an_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--operator` is `required=True` on `main`'s own parser — omitting it is `argparse`'s usage
    error (exit code 2, before the engine or the database is ever touched), never a run that
    quietly falls back to an unattributed actor."""
    monkeypatch.setattr("sys.argv", ["replay_dead_letters", "--org", str(uuid.uuid4())])
    with pytest.raises(SystemExit) as exc_info:
        await replay_dead_letters.main()
    assert exc_info.value.code == 2
