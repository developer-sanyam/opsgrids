"""Engage sending identity — the connection, its domains and the senders mail goes out as.

These run against real PostgreSQL as the **app** role, like the rest of this service's
integration suite, because two of the three things being asserted here are database behaviour
rather than Python behaviour: the row-level-security policies on the new tables, and the
defaults that make a freshly created sender unusable.

The unusable-by-default assertion is the load-bearing one. `verified_at` is written by exactly
one thing — a test send that actually reached the provider — and a sender that arrived usable
because a column defaulted the wrong way would let a campaign go out through a connection
nobody has ever proven works.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

from app import models, services, whatsapp_provider
from app.config import settings
from app.main import app
from app.transports import RecordingTransport, TransportRefused, TransportUnavailable
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    NotFoundProblem,
    RateLimitedProblem,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.pii import open_sealed
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _assume_whatsapp_ownership_proven(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this module except `test_whatsapp_provider.py`'s own is about the
    connection *row* — its shape, its secrecy, its uniqueness — not about proving a token against
    Meta's Graph API, which is exercised on its own with `httpx.MockTransport` in that file.
    Patched here, once, rather than in each test that reaches `connect_whatsapp`/
    `replace_whatsapp`."""

    async def _pass(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(whatsapp_provider, "verify_ownership", _pass)


class FakeResolver:
    """Answers from what the test wrote down. A name that is absent publishes nothing.

    Same shape as `tests/test_dns_check.py`'s, and deliberately a second copy: importing a
    fixture out of another test module couples two files that are otherwise independent, and
    this one is four lines.
    """

    def __init__(self, answers: dict[tuple[str, str], list[str]]) -> None:
        self._answers = answers

    async def resolve(self, host: str, rtype: str) -> list[str]:
        return self._answers.get((host, rtype), [])


async def test_sender_is_unverified_until_a_test_send_succeeds(session, org_id) -> None:
    domain = models.EmailDomain(organization_id=org_id, domain="company.com", status="pending")
    session.add(domain)
    await session.flush()

    sender = models.Sender(
        organization_id=org_id,
        domain_id=domain.id,
        name="Sales",
        address="sales@company.com",
        reply_to="sales@company.com",
    )
    session.add(sender)
    await session.flush()

    assert sender.verified_at is None
    assert sender.usable is False


async def test_a_new_domain_starts_pending_with_no_records(session, org_id) -> None:
    """`records` is filled when the record set is generated, not at insert. A domain that
    arrived with an empty set and a `verified` status would be sendable from before anyone
    published anything."""
    domain = models.EmailDomain(organization_id=org_id, domain="company.com")
    session.add(domain)
    await session.flush()

    assert domain.status == "pending"
    assert domain.records == []
    assert domain.last_checked_at is None


async def test_one_connection_per_organization(session, org_id) -> None:
    """A second connection would make "which one sent this" ambiguous before anything records
    the answer, so the database refuses it rather than the service remembering to."""
    from sqlalchemy.exc import IntegrityError

    session.add(
        models.EmailConnection(organization_id=org_id, kind="google", username="a@b.example.com")
    )
    await session.flush()

    session.add(
        models.EmailConnection(organization_id=org_id, kind="smtp", username="c@d.example.com")
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_another_tenants_sending_identity_is_invisible(
    session, admin_session, org_id
) -> None:
    """The policies, not the query filters. The suite runs as the app role, so this would be a
    false pass under a bypassing connection — which is why the conftest refuses to use one.

    All three rows matter, for different reasons. The connection holds a sealed credential that
    can send mail as another business. The domain is the set of names they are allowed to send
    as. The sender is what a campaign picks from, so a leaked one is a campaign that goes out
    signed by somebody else's Sales team.
    """
    other = uuid.uuid4()
    async with admin_session.begin():
        their_domain = models.EmailDomain(
            organization_id=other, domain="theirs.example.com", status="verified"
        )
        admin_session.add(their_domain)
        await admin_session.flush()
        admin_session.add(
            models.EmailConnection(
                organization_id=other,
                kind="google",
                host="smtp.gmail.com",
                username="ops@theirs.example.com",
                secret_enc=b"sealed-envelope",
            )
        )
        admin_session.add(
            models.Sender(
                organization_id=other,
                domain_id=their_domain.id,
                name="Their Sales",
                address="sales@theirs.example.com",
            )
        )

    assert (await session.execute(select(models.EmailConnection))).scalars().all() == []
    assert (await session.execute(select(models.EmailDomain))).scalars().all() == []
    assert (await session.execute(select(models.Sender))).scalars().all() == []


async def test_writing_a_sender_into_another_tenant_is_refused(session, org_id) -> None:
    """`WITH CHECK`: USING gates existing rows, but the new row of an INSERT needs its own
    check. Without it this insert would succeed — invisible to the tenant that made it, fully
    visible to the one it was planted on."""
    session.add(
        models.Sender(
            organization_id=uuid.uuid4(),
            domain_id=uuid.uuid4(),
            name="Planted",
            address="planted@example.com",
        )
    )
    with pytest.raises(Exception, match="row-level security"):
        await session.flush()
    await session.rollback()


# --------------------------------------------------------------------- the writers
#
# Everything below goes through `app/services.py` rather than the ORM, because what is being
# asserted is the writer's work: a sealed credential, a DKIM key that exists, a sender that
# cannot be created on a domain nobody has proven. A test that inserted rows directly would
# pass while the only code a customer can reach did none of it.


def _ctx(org_id: uuid.UUID, permission: str) -> ScopeContext:
    return ctx_for(org_id, permission, Scope.ORG)


@pytest_asyncio.fixture
async def verified_domain(session, org_id) -> models.EmailDomain:
    """A domain whose DNS has already been proven, so the sender rules can be tested alone.

    Written directly rather than through `create_domain` and `verify_domain`: what these tests
    are about is what a sender may be built on, and minting an RSA key and faking a resolver to
    get there would make them fail for reasons that belong to other tests.
    """
    row = models.EmailDomain(organization_id=org_id, domain="company.com", status="verified")
    async with session.begin():
        session.add(row)
    return row


async def test_creating_a_connection_seals_the_secret(session, org_id) -> None:
    async with session.begin():
        conn = await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="info@company.com",
            secret="app-password",  # pragma: allowlist secret
        )

    assert conn.secret_enc is not None
    assert b"app-password" not in conn.secret_enc


async def test_a_connection_derives_its_transport_from_its_kind(session, org_id) -> None:
    """The customer picks "Google Workspace" and never sees a port."""
    async with session.begin():
        conn = await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="info@company.com",
            secret="x",  # pragma: allowlist secret
        )

    assert (conn.host, conn.port, conn.use_tls) == ("smtp.gmail.com", 587, True)
    assert conn.daily_cap == 2000


async def test_a_second_connection_is_refused(session, org_id) -> None:
    """The unique constraint is the net; this is the tightrope. A caller deserves a sentence
    about the connection that already exists, not an IntegrityError from the driver."""
    ctx = _ctx(org_id, "engage.setup.connection.write")
    async with session.begin():
        await services.create_connection(
            session,
            ctx,
            kind="google",
            username="a@b.com",
            secret="x",  # pragma: allowlist secret
        )

    with pytest.raises(BusinessRuleProblem):
        async with session.begin():
            await services.create_connection(
                session,
                ctx,
                kind="smtp",
                username="c@d.com",
                secret="y",  # pragma: allowlist secret
                host="mail.d.com",
            )


async def test_connecting_audits_and_stages_an_event_without_the_secret(session, org_id) -> None:
    """Both rows exist, and neither carries the credential — not the plaintext, not the
    envelope. An outbox payload is read by every consumer on the bus."""
    async with session.begin():
        await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="info@company.com",
            secret="app-password",  # pragma: allowlist secret
        )

    audit = (await session.execute(select(models.AuditLog))).scalars().all()
    events = (
        (
            await session.execute(
                select(models.OutboxEvent).where(
                    models.OutboxEvent.event_type == services.EVT_CONNECTION_CREATED
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1
    assert len(events) == 1
    assert "app-password" not in json.dumps([audit[0].after, events[0].payload])


# --------------------------------------------------------------------- replacing the connection
#
# `replace_connection` is a transactional overwrite, not delete-then-create: the unique
# constraint is on `organization_id` alone, so there is never a window with zero rows and never
# a second row alongside the first.


async def test_replacing_changes_the_connections_fields_and_reseals_the_secret(
    session, org_id
) -> None:
    ctx = _ctx(org_id, "engage.setup.connection.write")
    async with session.begin():
        original = await services.create_connection(
            session,
            ctx,
            kind="google",
            username="info@company.com",
            secret="old-app-password",  # pragma: allowlist secret
        )
    original_id = original.id

    async with session.begin():
        replaced = await services.replace_connection(
            session,
            ctx,
            kind="smtp",
            username="ops@newprovider.com",
            secret="new-app-password",  # pragma: allowlist secret
            host="mail.newprovider.com",
        )

    # The same row, changed in place — not a second connection alongside the first.
    assert replaced.id == original_id
    rows = (await session.execute(select(models.EmailConnection))).scalars().all()
    assert len(rows) == 1

    assert (replaced.kind, replaced.username, replaced.host, replaced.port) == (
        "smtp",
        "ops@newprovider.com",
        "mail.newprovider.com",
        587,
    )
    assert replaced.secret_enc is not None
    assert open_sealed(replaced.secret_enc, settings=settings()) == "new-app-password"


async def test_replacing_erases_the_old_plaintext_everywhere(session, org_id) -> None:
    """The old credential must be destroyed, not orphaned: overwritten in the same
    transaction, with the prior plaintext left in none of the places this function writes —
    not the row, not the audit row, not the outbox payload."""
    ctx = _ctx(org_id, "engage.setup.connection.write")
    async with session.begin():
        await services.create_connection(
            session,
            ctx,
            kind="google",
            username="info@company.com",
            secret="old-app-password",  # pragma: allowlist secret
        )

    async with session.begin():
        replaced = await services.replace_connection(
            session,
            ctx,
            kind="google",
            username="info@company.com",
            secret="new-app-password",  # pragma: allowlist secret
        )

    assert replaced.secret_enc is not None
    assert b"old-app-password" not in replaced.secret_enc
    assert open_sealed(replaced.secret_enc, settings=settings()) == "new-app-password"

    audit = (await session.execute(select(models.AuditLog))).scalars().all()
    events = (await session.execute(select(models.OutboxEvent))).scalars().all()
    everywhere = json.dumps([a.before for a in audit] + [a.after for a in audit]) + json.dumps(
        [e.payload for e in events]
    )
    assert "old-app-password" not in everywhere
    assert "new-app-password" not in everywhere


async def test_replacing_audits_and_stages_an_event_without_the_secret(session, org_id) -> None:
    ctx = _ctx(org_id, "engage.setup.connection.write")
    async with session.begin():
        await services.create_connection(
            session,
            ctx,
            kind="google",
            username="info@company.com",
            secret="old-app-password",  # pragma: allowlist secret
        )

    async with session.begin():
        await services.replace_connection(
            session,
            ctx,
            kind="smtp",
            username="ops@newprovider.com",
            secret="new-app-password",  # pragma: allowlist secret
            host="mail.newprovider.com",
        )

    audit = (
        (
            await session.execute(
                select(models.AuditLog).where(
                    models.AuditLog.action == "engage.connection.replaced"
                )
            )
        )
        .scalars()
        .all()
    )
    events = (
        (
            await session.execute(
                select(models.OutboxEvent).where(
                    models.OutboxEvent.event_type == services.EVT_CONNECTION_REPLACED
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1
    assert len(events) == 1
    assert audit[0].before == {
        "kind": "google",
        "username": "info@company.com",
        "host": "smtp.gmail.com",
        "port": 587,
    }
    assert audit[0].after == {
        "kind": "smtp",
        "username": "ops@newprovider.com",
        "host": "mail.newprovider.com",
        "port": 587,
    }
    everywhere = json.dumps([audit[0].before, audit[0].after, events[0].payload])
    assert "old-app-password" not in everywhere
    assert "new-app-password" not in everywhere


async def test_replacing_when_no_connection_exists_is_not_found(session, org_id) -> None:
    """PUT replaces a resource that already exists; a PUT with nothing to replace is a client
    error rather than a silent create. `create_connection` (POST) already owns creation — a
    second route that created one too would leave the two routes ambiguous about which one to
    call, for no gain over telling the caller there is nothing here yet."""
    with pytest.raises(NotFoundProblem):
        async with session.begin():
            await services.replace_connection(
                session,
                _ctx(org_id, "engage.setup.connection.write"),
                kind="google",
                username="info@company.com",
                secret="x",  # pragma: allowlist secret
            )


async def test_a_domain_with_no_connection_yet_has_nothing_to_publish(session, org_id) -> None:
    """OpsGrids mints no keypair and publishes no mail infrastructure of its own — a domain has
    nothing to check until the organization says how it sends."""
    async with session.begin():
        row = await services.create_domain(
            session, _ctx(org_id, "engage.setup.domain.write"), domain="company.com"
        )

    assert row.records == []
    assert row.dkim_private_key_enc is None
    assert row.dkim_selector == "google"
    assert row.spf_include is None


async def test_a_domain_verifies_once_dns_publishes_every_required_record(session, org_id) -> None:
    ctx = _ctx(org_id, "engage.setup.domain.write")
    async with session.begin():
        await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="info@company.com",
            secret="app-password",  # pragma: allowlist secret
        )
        row = await services.create_domain(session, ctx, domain="company.com")

    published = {
        ("company.com", "TXT"): ["v=spf1 include:_spf.google.com ~all"],
        ("google._domainkey.company.com", "TXT"): ["v=DKIM1; k=rsa; p=AAA"],
    }
    async with session.begin():
        checked = await services.verify_domain(
            session, ctx, domain_id=row.id, resolver=FakeResolver(published)
        )

    assert checked.status == "verified"
    assert checked.last_checked_at is not None
    assert await _events(session, services.EVT_DOMAIN_VERIFIED) == 1


async def test_a_domain_publishing_nothing_stays_pending_and_announces_nothing(
    session, org_id
) -> None:
    """Everything downstream reads `domain.verified` as permission to send as that name, so it
    is staged on the transition and on nothing else."""
    ctx = _ctx(org_id, "engage.setup.domain.write")
    async with session.begin():
        await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="info@company.com",
            secret="app-password",  # pragma: allowlist secret
        )
        row = await services.create_domain(session, ctx, domain="company.com")

    async with session.begin():
        checked = await services.verify_domain(
            session, ctx, domain_id=row.id, resolver=FakeResolver({})
        )

    assert checked.status == "pending"
    assert checked.last_checked_at is not None
    assert await _events(session, services.EVT_DOMAIN_VERIFIED) == 0


async def test_a_domain_with_no_connection_is_never_verified_by_a_check(session, org_id) -> None:
    """`verify` with no provider connected performs no lookup and folds to `pending` — the
    "connect your email provider first" state, never a false `verified`."""
    ctx = _ctx(org_id, "engage.setup.domain.write")
    async with session.begin():
        row = await services.create_domain(session, ctx, domain="company.com")

    async with session.begin():
        checked = await services.verify_domain(
            session, ctx, domain_id=row.id, resolver=FakeResolver({})
        )

    assert checked.status == "pending"
    assert checked.records == []


async def test_a_sender_on_an_unverified_domain_is_refused(session, org_id) -> None:
    ctx = _ctx(org_id, "engage.setup.domain.write")
    async with session.begin():
        row = await services.create_domain(session, ctx, domain="company.com")

    with pytest.raises(BusinessRuleProblem):
        async with session.begin():
            await services.create_sender(
                session,
                _ctx(org_id, "engage.setup.sender.write"),
                domain_id=row.id,
                name="Sales",
                address="sales@company.com",
            )


async def test_a_sender_address_must_belong_to_its_domain(session, org_id, verified_domain) -> None:
    """Otherwise a verified domain is a licence to send as any name at all, which is the one
    thing domain verification exists to prevent."""
    with pytest.raises(BusinessRuleProblem):
        async with session.begin():
            await services.create_sender(
                session,
                _ctx(org_id, "engage.setup.sender.write"),
                domain_id=verified_domain.id,
                name="Sales",
                address="sales@someone-elses-domain.com",
            )


async def test_a_sender_is_created_unusable_on_a_verified_domain(
    session, org_id, verified_domain
) -> None:
    async with session.begin():
        sender = await services.create_sender(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            domain_id=verified_domain.id,
            name="Sales",
            address="Sales@Company.com",
        )

    assert sender.address == "sales@company.com"
    assert sender.usable is False
    assert await _events(session, services.EVT_SENDER_CREATED) == 1


# --------------------------------------------------------------------- deleting a domain


async def test_deleting_a_domain_removes_it_and_its_senders(
    session, org_id, verified_domain
) -> None:
    async with session.begin():
        sender = await services.create_sender(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            domain_id=verified_domain.id,
            name="Sales",
            address="sales@company.com",
        )
    domain_id, sender_id = verified_domain.id, sender.id

    async with session.begin():
        await services.delete_domain(
            session, _ctx(org_id, "engage.setup.domain.write"), domain_id=domain_id
        )

    assert await selectors_get_domain(session, org_id, domain_id) is None
    assert await selectors_get_sender(session, org_id, sender_id) is None


async def selectors_get_domain(session, org_id, domain_id):  # thin wrapper, kept local
    from app import selectors

    return await selectors.get_domain(session, org_id, domain_id)


async def selectors_get_sender(session, org_id, sender_id):
    from app import selectors

    return await selectors.get_sender(session, org_id, sender_id)


async def test_deleting_an_unknown_domain_is_not_found(session, org_id) -> None:
    with pytest.raises(NotFoundProblem):
        async with session.begin():
            await services.delete_domain(
                session, _ctx(org_id, "engage.setup.domain.write"), domain_id=ABSENT
            )


async def test_deleting_a_domain_used_by_a_sent_campaign_is_refused(
    session, org_id, verified_domain
) -> None:
    """A sender's campaign history is not this domain's to take away — the same reasoning
    `delete_list` already applies to a list a campaign addressed."""
    async with session.begin():
        sender = await services.create_sender(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            domain_id=verified_domain.id,
            name="News",
            address="news@company.com",
        )
        session.add(
            models.Campaign(
                organization_id=org_id,
                sender_id=sender.id,
                name="Autumn",
                subject="News",
                html_body="",
                text_body="Hello",
                audience="all_sendable",
                status="sent",
            )
        )
    # Read before the failure: the rollback expires every loaded attribute, and reading one
    # afterwards is a lazy refresh from a thread with no event loop to do it on.
    sender_id = sender.id

    with pytest.raises(ConflictProblem, match=r"news@company\.com"):
        async with session.begin():
            await services.delete_domain(
                session,
                _ctx(org_id, "engage.setup.domain.write"),
                domain_id=verified_domain.id,
            )

    # Refused, not partially applied: the sender is still there.
    assert await selectors_get_sender(session, org_id, sender_id) is not None


async def test_deleting_a_domain_used_only_by_a_draft_or_cancelled_campaign_succeeds(
    session, org_id, verified_domain
) -> None:
    async with session.begin():
        sender = await services.create_sender(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            domain_id=verified_domain.id,
            name="News",
            address="news@company.com",
        )
        session.add(
            models.Campaign(
                organization_id=org_id,
                sender_id=sender.id,
                name="Draft",
                subject="News",
                html_body="",
                text_body="Hello",
                audience="all_sendable",
                status="draft",
            )
        )

    async with session.begin():
        await services.delete_domain(
            session, _ctx(org_id, "engage.setup.domain.write"), domain_id=verified_domain.id
        )

    assert await selectors_get_domain(session, org_id, verified_domain.id) is None


async def test_a_delete_route_refuses_a_caller_holding_only_the_read(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{D_READ}:ORG"])):
        response = await client.delete(f"{DOMAINS}/{ABSENT}")
    assert response.status_code == 403


async def test_deleting_another_organizations_domain_is_not_found(
    client, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        their_domain = models.EmailDomain(
            organization_id=other, domain="theirs.example.com", status="verified"
        )
        admin_session.add(their_domain)
        await admin_session.flush()
        domain_id = their_domain.id

    with as_principal(engage_caller(org_id, [f"{D_WRITE}:ORG"])):
        response = await client.delete(f"{DOMAINS}/{domain_id}")
    assert response.status_code == 404


#: A domain row exactly as the pre-fix service wrote it: `host`/`verified` keys, `return_path`/
#: `tracking` kinds, no `fqdn`/`status` anywhere. Production holds a row shaped like this today.
_LEGACY_RECORDS = [
    {
        "kind": "spf",
        "type": "TXT",
        "host": "opsgrids.com",
        "value": "v=spf1 include:_spf.opsgrids.com ~all",
        "required": True,
        "verified": False,
    },
    {
        "kind": "return_path",
        "type": "CNAME",
        "host": "bounce.opsgrids.com",
        "value": "bounce.opsgrids.com",
        "required": True,
        "verified": False,
    },
]


async def test_a_legacy_shaped_domain_row_does_not_break_the_list(client, session, org_id) -> None:
    """`DnsRecordOut(**record)` raises on the pre-fix shape — this is the guard that keeps one
    such row from turning `GET /engage/domains` into a 500 for the whole organization."""
    async with session.begin():
        session.add(
            models.EmailDomain(
                organization_id=org_id,
                domain="opsgrids.com",
                status="verified",
                records=_LEGACY_RECORDS,
            )
        )
    with as_principal(engage_caller(org_id, [f"{D_READ}:ORG"])):
        response = await client.get(DOMAINS)
    assert response.status_code == 200, response.text


async def test_a_legacy_shaped_domain_row_can_still_be_deleted(client, session, org_id) -> None:
    async with session.begin():
        row = models.EmailDomain(
            organization_id=org_id,
            domain="opsgrids.com",
            status="verified",
            records=_LEGACY_RECORDS,
        )
        session.add(row)
        await session.flush()
        domain_id = row.id
    with as_principal(engage_caller(org_id, [f"{D_WRITE}:ORG"])):
        response = await client.delete(f"{DOMAINS}/{domain_id}")
    assert response.status_code == 204


# --------------------------------------------------------------------- editing a domain


async def test_editing_the_dkim_selector_resets_the_domain_to_pending(
    session, org_id, verified_domain
) -> None:
    async with session.begin():
        row = await services.update_domain_settings(
            session,
            _ctx(org_id, "engage.setup.domain.write"),
            domain_id=verified_domain.id,
            dkim_selector="rotated",
            spf_include=None,
        )

    assert row.dkim_selector == "rotated"
    assert row.status == "pending"


async def test_an_smtp_spf_include_must_be_a_plain_hostname(
    session, org_id, verified_domain
) -> None:
    with pytest.raises(ValidationProblem):
        async with session.begin():
            await services.update_domain_settings(
                session,
                _ctx(org_id, "engage.setup.domain.write"),
                domain_id=verified_domain.id,
                dkim_selector=None,
                spf_include="not a hostname",
            )


@pytest.mark.parametrize("selector", ["a..b", ".x", "x\\", "-bad", "bad-"])
async def test_a_malformed_dkim_selector_is_refused_before_it_reaches_the_resolver(
    session, org_id, verified_domain, selector
) -> None:
    """A selector shaped like this reaches `dns.name.from_text` inside the resolver at verify
    time, where dnspython's own `EmptyLabel`/`BadEscape` would otherwise surface as a 500."""
    with pytest.raises(ValidationProblem):
        async with session.begin():
            await services.update_domain_settings(
                session,
                _ctx(org_id, "engage.setup.domain.write"),
                domain_id=verified_domain.id,
                dkim_selector=selector,
                spf_include=None,
            )


async def test_editing_with_no_actual_change_leaves_status_untouched(
    session, org_id, verified_domain
) -> None:
    async with session.begin():
        row = await services.update_domain_settings(
            session,
            _ctx(org_id, "engage.setup.domain.write"),
            domain_id=verified_domain.id,
            dkim_selector="google",
            spf_include=None,
        )
    assert row.status == "verified"


# --------------------------------------------------------------------- the test-send gate
#
# The transport is injected in every one of these. A test that opened a socket would be testing
# whoever owns that mailbox, would fail on a train, and would send real mail to a real person
# every time the suite ran.


@pytest_asyncio.fixture
async def usable_sender(session, org_id, verified_domain) -> models.Sender:
    """A connected provider and a sender on a verified domain — everything the gate needs
    except a message that actually arrived."""
    async with session.begin():
        await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="info@company.com",
            secret="app-password",  # pragma: allowlist secret
        )
        sender = await services.create_sender(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            domain_id=verified_domain.id,
            name="Sales",
            address="sales@company.com",
        )
    return sender


async def _reread(session, sender_id: uuid.UUID) -> models.Sender:
    async with session.begin():
        stmt = select(models.Sender).where(models.Sender.id == sender_id)
        return (await session.execute(stmt)).scalar_one()


async def test_a_test_send_that_reaches_the_provider_verifies_the_sender(
    session, org_id, usable_sender
) -> None:
    wire = RecordingTransport()
    async with session.begin():
        sender = await services.send_test(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            sender_id=usable_sender.id,
            to_address="ops@company.com",
            transport=wire,
        )

    assert sender.usable is True
    assert [out.to_address for out in wire.sent] == ["ops@company.com"]
    assert await _events(session, services.EVT_SENDER_VERIFIED) == 1


async def test_a_refused_test_send_leaves_the_sender_unusable(
    session, org_id, usable_sender
) -> None:
    """The gate only opens on a message that actually left. A sender marked usable by a send the
    provider rejected is a campaign that fails one address at a time, in front of customers. The
    refusal itself must not carry the provider's own words back to the caller — see M1 below."""
    # Read before the failure: the rollback expires every loaded attribute, and reading one
    # afterwards is a lazy refresh from a thread that has no event loop to do it on.
    sender_id = usable_sender.id
    wire = RecordingTransport(
        fail_with=TransportRefused("535 5.7.8 Username and Password not accepted")
    )
    with pytest.raises(BusinessRuleProblem) as refusal:
        async with session.begin():
            try:
                await services.send_test(
                    session,
                    _ctx(org_id, "engage.setup.sender.write"),
                    sender_id=sender_id,
                    to_address="ops@company.com",
                    transport=wire,
                )
            finally:
                # Checked *before* the block rolls back. The rollback would undo a premature
                # `verified_at` and hide a writer that stamps the column first and sends second
                # — which is a working gate only until a caller catches the problem and commits
                # anyway. Only a message that actually left may write it.
                staged = await session.get(models.Sender, sender_id)
                assert staged is not None and staged.verified_at is None

    # M1: the provider's own words used to be carried out intact. An arbitrary `to_address` and
    # a caller who chose the host turns that into an oracle for a port scan, so the response now
    # carries one fixed sentence and nothing from the transport — the banner text is logged, not
    # returned.
    assert "535" not in str(refusal.value)
    assert "Username and Password" not in str(refusal.value)
    assert str(refusal.value) == (
        "The provider refused the test message. Check the connection's credentials and "
        "the sender's address."
    )
    assert (await _reread(session, sender_id)).usable is False
    assert await _events(session, services.EVT_SENDER_VERIFIED) == 0


async def test_a_provider_that_could_not_be_reached_is_not_the_customers_mistake(
    session, org_id, usable_sender
) -> None:
    """A 422 tells the customer to fix something. A timeout is not something they can fix, and
    saying so would send them round their own settings for an hour."""
    sender_id = usable_sender.id
    wire = RecordingTransport(fail_with=TransportUnavailable("[Errno 60] Operation timed out"))
    with pytest.raises(UpstreamUnavailableProblem) as outage:
        async with session.begin():
            await services.send_test(
                session,
                _ctx(org_id, "engage.setup.sender.write"),
                sender_id=sender_id,
                to_address="ops@company.com",
                transport=wire,
            )

    # M1: the errno used to be carried out intact, which is exactly the connect-vs-timeout
    # oracle a port scan wants from an internal address. Fixed sentence, nothing from the
    # socket.
    assert "Errno" not in str(outage.value)
    assert "timed out" not in str(outage.value)
    assert str(outage.value) == "The provider could not be reached. Try again in a few minutes."
    assert (await _reread(session, sender_id)).usable is False


async def test_a_test_send_without_a_connection_is_refused(
    session, org_id, verified_domain
) -> None:
    """Refused before the transport is built, because the transport would have no credential —
    and a socket error is a worse answer than the sentence that names what is missing."""
    async with session.begin():
        sender = await services.create_sender(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            domain_id=verified_domain.id,
            name="Sales",
            address="sales@company.com",
        )

    with pytest.raises(BusinessRuleProblem):
        async with session.begin():
            await services.send_test(
                session,
                _ctx(org_id, "engage.setup.sender.write"),
                sender_id=sender.id,
                to_address="ops@company.com",
                transport=RecordingTransport(),
            )


async def test_a_test_send_for_an_unknown_sender_is_not_found(session, org_id) -> None:
    with pytest.raises(NotFoundProblem):
        async with session.begin():
            await services.send_test(
                session,
                _ctx(org_id, "engage.setup.sender.write"),
                sender_id=uuid.uuid4(),
                to_address="ops@company.com",
                transport=RecordingTransport(),
            )


# --------------------------------------------------------------------- the outbound dial guard
#
# M1: an `smtp` connection's host used to be dialled exactly as the caller typed it, and a
# fixed-provider kind's `host` field was honoured too — so a customer holding only
# `engage.setup.connection.write` could point a real SMTP handshake at `postgres`, `redis`,
# `169.254.169.254`, or anything else reachable from inside the compose network, then read
# connect-vs-refuse-vs-timeout and banner text back through `POST .../test`. Every test below
# proves one class of that is now refused, and that refusing it never repeats the reason.

_FIXED_MESSAGE = (
    "The submission host must be a public mail server hostname, and the port one of "
    "25, 465, 587, or 2525."
)


async def test_a_literal_ip_host_is_refused(session, org_id) -> None:
    with pytest.raises(ValidationProblem) as exc:
        async with session.begin():
            await services.create_connection(
                session,
                _ctx(org_id, "engage.setup.connection.write"),
                kind="smtp",
                username="ops@company.com",
                secret="x",  # pragma: allowlist secret
                host="127.0.0.1",
                port=587,
            )
    assert str(exc.value) == _FIXED_MESSAGE
    assert (await session.execute(select(models.EmailConnection))).scalars().all() == []


async def test_a_single_label_compose_service_name_is_refused(session, org_id) -> None:
    """`postgres`, `redis`, `rabbitmq`, `account` — every name the compose network answers to
    without a search domain appended."""
    with pytest.raises(ValidationProblem) as exc:
        async with session.begin():
            await services.create_connection(
                session,
                _ctx(org_id, "engage.setup.connection.write"),
                kind="smtp",
                username="ops@company.com",
                secret="x",  # pragma: allowlist secret
                host="postgres",
                port=587,
            )
    assert str(exc.value) == _FIXED_MESSAGE


async def test_a_hostname_resolving_to_a_private_address_is_refused(session, org_id) -> None:
    """A name is not the thing being trusted — where it resolves is."""
    resolver = FakeResolver({("mail.attacker.example", "A"): ["10.0.0.5"]})
    with pytest.raises(ValidationProblem) as exc:
        async with session.begin():
            await services.create_connection(
                session,
                _ctx(org_id, "engage.setup.connection.write"),
                kind="smtp",
                username="ops@company.com",
                secret="x",  # pragma: allowlist secret
                host="mail.attacker.example",
                port=587,
                resolver=resolver,
            )
    assert str(exc.value) == _FIXED_MESSAGE


async def test_a_forbidden_port_is_refused_even_bypassing_the_schema(session, org_id) -> None:
    """The schema narrows `port` to the submission set too, but the writer is the boundary that
    actually matters — a caller that reached this function some other way must still be refused."""
    with pytest.raises(ValidationProblem) as exc:
        async with session.begin():
            await services.create_connection(
                session,
                _ctx(org_id, "engage.setup.connection.write"),
                kind="smtp",
                username="ops@company.com",
                secret="x",  # pragma: allowlist secret
                host="mail.example.com",
                port=5432,
                resolver=FakeResolver({("mail.example.com", "A"): ["8.8.8.8"]}),
            )
    assert str(exc.value) == _FIXED_MESSAGE


async def test_a_public_hostname_on_an_allowed_port_is_accepted(session, org_id) -> None:
    resolver = FakeResolver({("mail.example.com", "A"): ["8.8.8.8"]})
    async with session.begin():
        conn = await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="smtp",
            username="ops@company.com",
            secret="x",  # pragma: allowlist secret
            host="mail.example.com",
            port=587,
            resolver=resolver,
        )
    assert (conn.host, conn.port) == ("mail.example.com", 587)


async def test_a_fixed_provider_kind_ignores_the_callers_host_entirely(session, org_id) -> None:
    """The exploit itself: `kind="google"` with `host` aimed at the metadata service. Before the
    fix, `(host or transport.host)` let the caller's value win outright."""
    async with session.begin():
        conn = await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="ops@company.com",
            secret="x",  # pragma: allowlist secret
            host="169.254.169.254",
            port=5432,
        )
    assert (conn.host, conn.port) == ("smtp.gmail.com", 587)


# Replacing a connection is a second way to put a host on the row, so it is held to the same
# rule by the same function. Without that, PUT is simply the way round the check on POST.


async def _connected_google(session, org_id) -> None:
    async with session.begin():
        await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="ops@company.com",
            secret="x",  # pragma: allowlist secret
        )


@pytest.mark.parametrize("host", ["127.0.0.1", "postgres", "169.254.169.254", "localhost"])
async def test_replacing_a_connection_refuses_an_internal_host(session, org_id, host) -> None:
    await _connected_google(session, org_id)
    with pytest.raises(ValidationProblem) as exc:
        async with session.begin():
            await services.replace_connection(
                session,
                _ctx(org_id, "engage.setup.connection.write"),
                kind="smtp",
                username="ops@company.com",
                secret="y",  # pragma: allowlist secret
                host=host,
                port=587,
            )
    assert str(exc.value) == _FIXED_MESSAGE
    # The refusal left the working connection exactly as it was.
    async with session.begin():
        conn = (await session.execute(select(models.EmailConnection))).scalar_one()
    assert (conn.kind, conn.host, conn.port) == ("google", "smtp.gmail.com", 587)


async def test_replacing_a_connection_refuses_a_name_resolving_privately(session, org_id) -> None:
    await _connected_google(session, org_id)
    resolver = FakeResolver({("mail.attacker.example", "A"): ["10.0.0.5"]})
    with pytest.raises(ValidationProblem) as exc:
        async with session.begin():
            await services.replace_connection(
                session,
                _ctx(org_id, "engage.setup.connection.write"),
                kind="smtp",
                username="ops@company.com",
                secret="y",  # pragma: allowlist secret
                host="mail.attacker.example",
                port=587,
                resolver=resolver,
            )
    assert str(exc.value) == _FIXED_MESSAGE


async def test_replacing_with_a_fixed_provider_ignores_the_callers_host(session, org_id) -> None:
    await _connected_google(session, org_id)
    async with session.begin():
        conn = await services.replace_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="microsoft",
            username="ops@company.com",
            secret="y",  # pragma: allowlist secret
            host="169.254.169.254",
            port=5432,
        )
    assert conn.host != "169.254.169.254"
    assert conn.port in {25, 465, 587, 2525}


async def test_a_name_that_rebinds_between_creation_and_dial_is_refused_at_dial_time(
    session, org_id, verified_domain
) -> None:
    """The connection is saved while `mail.rebindy.example` still answers publicly. By the time a
    test send actually runs, its zone has been repointed at the compose network — and the second,
    independent resolution the dial guard performs catches exactly that, because it trusts
    nothing the first resolution already decided."""
    ctx = _ctx(org_id, "engage.setup.connection.write")
    creation_resolver = FakeResolver({("mail.rebindy.example", "A"): ["8.8.8.8"]})
    async with session.begin():
        await services.create_connection(
            session,
            ctx,
            kind="smtp",
            username="ops@company.com",
            secret="app-password",  # pragma: allowlist secret
            host="mail.rebindy.example",
            port=587,
            resolver=creation_resolver,
        )
        sender = await services.create_sender(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            domain_id=verified_domain.id,
            name="Sales",
            address="sales@company.com",
        )

    dial_resolver = FakeResolver({("mail.rebindy.example", "A"): ["10.0.0.5"]})
    sender_id = sender.id
    with pytest.raises(BusinessRuleProblem) as exc:
        async with session.begin():
            await services.send_test(
                session,
                _ctx(org_id, "engage.setup.sender.write"),
                sender_id=sender_id,
                to_address="ops@company.com",
                resolver=dial_resolver,
            )

    assert str(exc.value) == _FIXED_MESSAGE
    # Nothing from the rejected resolution — not the address, not the zone — reaches the caller.
    assert "10.0.0.5" not in str(exc.value)
    assert (await _reread(session, sender_id)).verified_at is None


async def test_a_refused_dial_never_leaks_the_providers_own_words(
    session, org_id, usable_sender
) -> None:
    """The transport's exception text is real diagnostic detail — a banner, an errno, an SMTP
    reply code — and none of it is safe to hand back to a caller who chose the host and can
    retry with a different one on every attempt."""
    sender_id = usable_sender.id
    wire = RecordingTransport(
        fail_with=TransportRefused("550 5.1.1 mailbox postgres@internal does not exist")
    )
    with pytest.raises(BusinessRuleProblem) as exc:
        async with session.begin():
            await services.send_test(
                session,
                _ctx(org_id, "engage.setup.sender.write"),
                sender_id=sender_id,
                to_address="ops@company.com",
                transport=wire,
            )
    assert "550 5.1.1" not in str(exc.value)
    assert "postgres@internal" not in str(exc.value)


class TestTheTestSendRateLimit:
    async def test_a_burst_of_test_sends_is_refused_past_the_limit(
        self, session, org_id, usable_sender, monkeypatch
    ) -> None:
        cfg = settings()
        monkeypatch.setattr(cfg, "engage_test_send_rate_limit_per_minute", 1)

        async with session.begin():
            await services.send_test(
                session,
                _ctx(org_id, "engage.setup.sender.write"),
                sender_id=usable_sender.id,
                to_address="ops@company.com",
                transport=RecordingTransport(),
            )

        with pytest.raises(RateLimitedProblem):
            async with session.begin():
                await services.send_test(
                    session,
                    _ctx(org_id, "engage.setup.sender.write"),
                    sender_id=usable_sender.id,
                    to_address="ops2@company.com",
                    transport=RecordingTransport(),
                )

    async def test_the_limit_is_keyed_per_organization(
        self, session, org_id, usable_sender, monkeypatch
    ) -> None:
        """A neighbour's burst must not spend this organization's budget. Proven at the key the
        limiter is asked to count against, rather than by standing up a second tenant's whole
        sending identity through a session bound to this one's — which RLS would refuse anyway,
        for exactly the reason `test_writing_a_sender_into_another_tenant_is_refused` exists."""
        seen_keys: list[str] = []
        original = services.enforce_rate_limit

        async def _spy(key: str, **kwargs: Any) -> None:
            seen_keys.append(key)
            await original(key, **kwargs)

        monkeypatch.setattr(services, "enforce_rate_limit", _spy)

        async with session.begin():
            await services.send_test(
                session,
                _ctx(org_id, "engage.setup.sender.write"),
                sender_id=usable_sender.id,
                to_address="ops@company.com",
                transport=RecordingTransport(),
            )

        assert seen_keys == [f"engage.test_send:{org_id}"]


# --------------------------------------------------------------------- the HTTP surface, again
#
# The exploit was reachable over HTTP with nothing but `engage.setup.connection.write`, so the
# closing assertion belongs there too: a malicious `host` is refused before a row exists, and the
# response carries no more than the fixed sentence above.


async def test_the_connection_route_refuses_a_literal_ip_host(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{C_WRITE}:ORG"])):
        response = await client.post(
            CONNECTION,
            json={
                "kind": "smtp",
                "username": "ops@company.com",
                "secret": "x",  # pragma: allowlist secret
                "host": "169.254.169.254",
                "port": 587,
            },
        )
    assert response.status_code == 422
    assert response.json()["detail"] == _FIXED_MESSAGE
    assert "169.254.169.254" not in response.text


async def test_the_connection_route_ignores_a_malicious_host_on_a_fixed_provider_kind(
    client, org_id
) -> None:
    """`port` is a value the schema's own `Literal` would accept on its own — 25 is a real mail
    submission port — so a 201 here isolates `create_connection`'s own ignore-the-caller rule
    from the schema's separate, narrower one below."""
    with as_principal(engage_caller(org_id, [f"{C_WRITE}:ORG"])):
        response = await client.post(
            CONNECTION,
            json={
                "kind": "google",
                "username": "ops@company.com",
                "secret": "x",  # pragma: allowlist secret
                "host": "169.254.169.254",
                "port": 25,
            },
        )
    assert response.status_code == 201
    assert response.json()["host"] == "smtp.gmail.com"
    assert response.json()["port"] == 587


async def test_the_connection_route_refuses_a_port_outside_the_submission_set(
    client, org_id
) -> None:
    """Defence in depth, at the boundary: a bad port never reaches the writer at all here."""
    with as_principal(engage_caller(org_id, [f"{C_WRITE}:ORG"])):
        response = await client.post(
            CONNECTION,
            json={
                "kind": "smtp",
                "username": "ops@company.com",
                "secret": "x",  # pragma: allowlist secret
                "host": "mail.example.com",
                "port": 5432,
            },
        )
    assert response.status_code == 422


async def _events(session, event_type: str) -> int:
    rows = (
        (
            await session.execute(
                select(models.OutboxEvent).where(models.OutboxEvent.event_type == event_type)
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


async def test_replacing_the_connection_resets_every_sender_in_the_organization_to_unusable(
    session, org_id, usable_sender
) -> None:
    """`verified_at` is proof a test message reached the provider through the connection that
    is about to stop existing. Once the credential or the provider changes, that proof is
    about a path that no longer exists, so every sender proved through it must go back to
    unusable rather than stay selectable on the strength of a connection nobody has tested."""
    wire = RecordingTransport()
    async with session.begin():
        sender = await services.send_test(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            sender_id=usable_sender.id,
            to_address="ops@company.com",
            transport=wire,
        )
    assert sender.usable is True

    async with session.begin():
        await services.replace_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="smtp",
            username="ops@newprovider.com",
            secret="new-app-password",  # pragma: allowlist secret
            host="mail.newprovider.com",
        )

    refreshed = await _reread(session, sender.id)
    assert refreshed.verified_at is None
    assert refreshed.usable is False


# --------------------------------------------------------------------- the HTTP surface
#
# What is asserted below is the router's own work: the permission each route declares, the
# entitlement behind it, and the one guarantee these response shapes must keep — that no route
# hands back a credential.
#
# The test-send route appears here only where it is refused before a socket would be opened. A
# route test that reached a real SMTP server would be testing whoever owns that mailbox and
# would send mail to a real person every time the suite ran; the paths that actually send are
# the transport-injected writer tests above.

C_READ = "engage.setup.connection.read"
C_WRITE = "engage.setup.connection.write"
D_READ = "engage.setup.domain.read"
D_WRITE = "engage.setup.domain.write"
S_READ = "engage.setup.sender.read"
S_WRITE = "engage.setup.sender.write"
W_READ = "engage.setup.whatsapp.read"
W_WRITE = "engage.setup.whatsapp.write"

CONNECTION = "/api/v1/engage/connection"
DOMAINS = "/api/v1/engage/domains"
SENDERS = "/api/v1/engage/senders"
WHATSAPP_CONNECTION = "/api/v1/engage/whatsapp/connection"

#: An id this organization has no row for. Used where the assertion is about a guard rather
#: than about a row, so the refusal lands before anything is looked up or sent.
ABSENT = uuid.UUID("11111111-1111-4111-8111-111111111111")

READ_ROUTES = [CONNECTION, DOMAINS, SENDERS, WHATSAPP_CONNECTION]

#: Every write this router exposes, paired with the *read* permission on its own resource. A
#: caller holding only that read must be refused each one — which is the whole reason the
#: catalogue splits read from write per resource instead of gating the product as a whole.
WRITE_ROUTES = [
    (
        CONNECTION,
        C_READ,
        {
            "kind": "google",
            "username": "info@company.com",
            "secret": "x",  # pragma: allowlist secret
        },
    ),
    (DOMAINS, D_READ, {"domain": "company.com"}),
    (f"{DOMAINS}/{ABSENT}/verify", D_READ, None),
    (SENDERS, S_READ, {"domain_id": str(ABSENT), "name": "Sales", "address": "sales@company.com"}),
    (f"{SENDERS}/{ABSENT}/test", S_READ, {"to_address": "ops@company.com"}),
    (
        WHATSAPP_CONNECTION,
        W_READ,
        {
            "waba_id": "101000000000001",
            "phone_number_id": "106540000000001",
            "display_number": "+1 555 0100",
            "access_token": "token",  # pragma: allowlist secret
        },
    ),
]


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def engage_caller(org_id: uuid.UUID, perms: list[str]) -> Principal:
    """The principal the gateway forwards for an Engage request.

    The entitlement is spelled out rather than left to `principal_for`'s default, which is
    `notification`: Engage is the product a customer buys and notification is the plumbing
    underneath it, so being entitled to one is not being entitled to the other.
    """
    return principal_for(org_id, perms, entitlements=["engage"])


@pytest.mark.parametrize(("path", "read_permission", "body"), WRITE_ROUTES)
async def test_a_write_refuses_a_caller_holding_only_the_read(
    client, org_id, path, read_permission, body
) -> None:
    with as_principal(engage_caller(org_id, [f"{read_permission}:ORG"])):
        response = await client.post(path, json=body)
    assert response.status_code == 403


@pytest.mark.parametrize("path", READ_ROUTES)
async def test_a_read_refuses_a_caller_without_the_permission(client, org_id, path) -> None:
    with as_principal(engage_caller(org_id, [])):
        response = await client.get(path)
    assert response.status_code == 403


@pytest.mark.parametrize("path", READ_ROUTES)
async def test_the_notification_entitlement_does_not_unlock_engage(client, org_id, path) -> None:
    """402, not 403: the permissions are fine, the subscription is not. Two catalogues served by
    one process are only a product boundary if the entitlement gate tells them apart."""
    perms = [f"{C_READ}:ORG", f"{D_READ}:ORG", f"{S_READ}:ORG"]
    with as_principal(principal_for(org_id, perms, entitlements=["notification"])):
        response = await client.get(path)
    assert response.status_code == 402


async def test_connecting_a_provider_never_returns_the_secret(client, org_id) -> None:
    """The response-shape assertion this slice exists to keep.

    Pinned as an exact key set rather than as "the plaintext is absent", because the guarantee
    is stronger than that: no field here is derived from the credential at all. A mask, a length
    or a last-four would each pass a substring check and each be a secret with a hint attached.
    """
    with as_principal(engage_caller(org_id, [f"{C_WRITE}:ORG"])):
        response = await client.post(
            CONNECTION,
            json={
                "kind": "google",
                "username": "info@company.com",
                "secret": "app-password",  # pragma: allowlist secret
            },
        )

    assert response.status_code == 201
    assert set(response.json()) == {"kind", "username", "host", "port", "status", "daily_cap"}
    assert "app-password" not in response.text


async def test_reading_the_connection_returns_that_same_shape(client, org_id) -> None:
    """Both directions, because the read route is the one a setup screen calls on every visit."""
    with as_principal(engage_caller(org_id, [f"{C_WRITE}:ORG"])):
        await client.post(
            CONNECTION,
            json={
                "kind": "google",
                "username": "info@company.com",
                "secret": "app-password",  # pragma: allowlist secret
            },
        )
    with as_principal(engage_caller(org_id, [f"{C_READ}:ORG"])):
        response = await client.get(CONNECTION)

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 1
    assert set(body["items"][0]) == {"kind", "username", "host", "port", "status", "daily_cap"}
    # Derived from the kind by the writer, which is what lets the wizard skip the question —
    # worth reading back, because a screen showing nothing here looks unconnected.
    assert body["items"][0]["host"] == "smtp.gmail.com"
    assert "app-password" not in response.text


async def test_replacing_a_connection_never_returns_the_secret_and_updates_the_fields(
    client, org_id
) -> None:
    with as_principal(engage_caller(org_id, [f"{C_WRITE}:ORG"])):
        await client.post(
            CONNECTION,
            json={
                "kind": "google",
                "username": "info@company.com",
                "secret": "old-app-password",  # pragma: allowlist secret
            },
        )
        response = await client.put(
            CONNECTION,
            json={
                "kind": "smtp",
                "username": "ops@newprovider.com",
                "secret": "new-app-password",  # pragma: allowlist secret
                "host": "mail.newprovider.com",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"kind", "username", "host", "port", "status", "daily_cap"}
    assert (body["kind"], body["username"], body["host"]) == (
        "smtp",
        "ops@newprovider.com",
        "mail.newprovider.com",
    )
    assert "old-app-password" not in response.text
    assert "new-app-password" not in response.text


async def test_replacing_refuses_a_caller_holding_only_the_read(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{C_READ}:ORG"])):
        response = await client.put(
            CONNECTION,
            json={
                "kind": "google",
                "username": "info@company.com",
                "secret": "x",  # pragma: allowlist secret
            },
        )
    assert response.status_code == 403


async def test_replacing_when_no_connection_exists_is_a_404(client, org_id) -> None:
    """PUT replaces a resource that already exists. The decision, spelled out in
    `services.replace_connection`: a PUT with nothing to replace is a client error, not a
    silent create — `POST /engage/connection` already owns creation, and a PUT that created
    too would leave the two routes ambiguous about which one to call."""
    with as_principal(engage_caller(org_id, [f"{C_WRITE}:ORG"])):
        response = await client.put(
            CONNECTION,
            json={
                "kind": "google",
                "username": "info@company.com",
                "secret": "x",  # pragma: allowlist secret
            },
        )
    assert response.status_code == 404


async def test_replacing_as_another_organization_does_not_touch_this_ones_connection(
    client, admin_session, org_id
) -> None:
    """The write-side mirror of `test_another_tenants_sending_identity_is_invisible`: proof
    that a replace cannot reach a row scoped to a different tenant, not only that a read
    cannot see one."""
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            models.EmailConnection(
                organization_id=other,
                kind="google",
                host="smtp.gmail.com",
                port=587,
                username="ops@theirs.example.com",
                secret_enc=b"their-sealed-envelope",
            )
        )

    with as_principal(engage_caller(org_id, [f"{C_WRITE}:ORG"])):
        response = await client.put(
            CONNECTION,
            json={
                "kind": "smtp",
                "username": "attacker@evil.com",
                "secret": "stolen",  # pragma: allowlist secret
                "host": "mail.evil.com",
            },
        )
    # `org_id` has no connection of its own to replace — proof the write landed on nobody's
    # row rather than reaching across into the other organization's.
    assert response.status_code == 404

    async with admin_session.begin():
        theirs = (
            await admin_session.execute(
                select(models.EmailConnection).where(
                    models.EmailConnection.organization_id == other
                )
            )
        ).scalar_one()
    assert theirs.username == "ops@theirs.example.com"
    assert theirs.host == "smtp.gmail.com"
    assert theirs.secret_enc == b"their-sealed-envelope"


async def test_adding_a_domain_with_no_connection_shows_nothing_to_publish_yet(
    client, org_id
) -> None:
    """ "Connect your email provider first" — a domain has nothing to check DNS against before
    the organization has said how it sends."""
    with as_principal(engage_caller(org_id, [f"{D_WRITE}:ORG"])):
        response = await client.post(DOMAINS, json={"domain": "Company.com"})

    body = response.json()
    assert response.status_code == 201
    assert body["domain"] == "company.com"
    assert body["status"] == "pending"
    assert body["connection_kind"] is None
    assert body["records"] == []


async def test_adding_a_domain_previews_the_records_its_connection_needs(client, org_id) -> None:
    """The record set is the point of the response once a provider is connected. A setup screen
    that cannot show it leaves the customer with a domain row and nothing to paste into their
    registrar."""
    with as_principal(engage_caller(org_id, [f"{C_WRITE}:ORG"])):
        await client.post(
            CONNECTION,
            json={
                "kind": "google",
                "username": "info@company.com",
                "secret": "app-password",  # pragma: allowlist secret
            },
        )
    with as_principal(engage_caller(org_id, [f"{D_WRITE}:ORG"])):
        response = await client.post(DOMAINS, json={"domain": "Company.com"})

    body = response.json()
    assert response.status_code == 201
    assert body["domain"] == "company.com"
    assert body["connection_kind"] == "google"
    assert {r["kind"] for r in body["records"]} == {"spf", "dkim", "dmarc"}
    assert all(r["status"] == "missing" for r in body["records"])
    # No OpsGrids infrastructure of any kind: no bounce host, no tracking host, no signing key
    # this platform minted.
    assert "PRIVATE KEY" not in response.text
    assert "opsgrids.com" not in response.text.lower()


async def test_the_domain_list_shows_what_was_added(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{D_WRITE}:ORG"])):
        await client.post(DOMAINS, json={"domain": "company.com"})
    with as_principal(engage_caller(org_id, [f"{D_READ}:ORG"])):
        response = await client.get(DOMAINS)

    assert response.status_code == 200
    assert [row["domain"] for row in response.json()["items"]] == ["company.com"]


async def test_a_sender_created_through_the_api_arrives_unusable(
    client, org_id, verified_domain
) -> None:
    """`usable` is derived from `verified_at`, and only a test send writes that. A response
    claiming otherwise would let a campaign screen offer a sender nobody has proven."""
    with as_principal(engage_caller(org_id, [f"{S_WRITE}:ORG"])):
        response = await client.post(
            SENDERS,
            json={
                "domain_id": str(verified_domain.id),
                "name": "Sales",
                "address": "sales@company.com",
            },
        )

    body = response.json()
    assert response.status_code == 201
    assert body["address"] == "sales@company.com"
    assert body["usable"] is False
    assert body["verified_at"] is None


async def test_verifying_a_domain_this_organization_does_not_have_is_a_404(client, org_id) -> None:
    """Refused before a single DNS lookup, and without saying whether the domain belongs to
    somebody else."""
    with as_principal(engage_caller(org_id, [f"{D_WRITE}:ORG"])):
        response = await client.post(f"{DOMAINS}/{ABSENT}/verify")

    # The writer's own words, not the framework's "Not Found" for a path nobody mounted —
    # otherwise this passes just as well against a router that does not exist.
    assert response.status_code == 404
    assert response.json()["detail"] == "No such sending domain"


async def test_a_test_send_for_an_unknown_sender_is_refused_before_any_mail_moves(
    client, org_id
) -> None:
    with as_principal(engage_caller(org_id, [f"{S_WRITE}:ORG"])):
        response = await client.post(
            f"{SENDERS}/{ABSENT}/test", json={"to_address": "ops@company.com"}
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "No such sender"


# --------------------------------------------------------------------- whatsapp connection


async def test_connecting_whatsapp_never_returns_the_token(client, org_id) -> None:
    """Same response-shape guarantee as the email connection's: pinned as an exact key set, not
    a substring check, because no field here may be derived from the token at all."""
    with as_principal(engage_caller(org_id, [f"{W_WRITE}:ORG"])):
        response = await client.post(
            WHATSAPP_CONNECTION,
            json={
                "waba_id": "101000000000001",
                "phone_number_id": "106540000000001",
                "display_number": "+1 555 0100",
                "access_token": "super-secret-token",  # pragma: allowlist secret
            },
        )

    assert response.status_code == 201
    assert set(response.json()) == {"waba_id", "phone_number_id", "display_number", "status"}
    assert "super-secret-token" not in response.text


async def test_reading_the_whatsapp_connection_returns_that_same_shape(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{W_WRITE}:ORG"])):
        await client.post(
            WHATSAPP_CONNECTION,
            json={
                "waba_id": "101000000000001",
                "phone_number_id": "106540000000001",
                "display_number": "+1 555 0100",
                "access_token": "super-secret-token",  # pragma: allowlist secret
            },
        )
    with as_principal(engage_caller(org_id, [f"{W_READ}:ORG"])):
        response = await client.get(WHATSAPP_CONNECTION)

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 1
    assert set(body["items"][0]) == {"waba_id", "phone_number_id", "display_number", "status"}
    assert body["items"][0]["status"] == "connected"
    assert "super-secret-token" not in response.text


async def test_a_second_whatsapp_connection_is_refused(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{W_WRITE}:ORG"])):
        await client.post(
            WHATSAPP_CONNECTION,
            json={
                "waba_id": "101000000000001",
                "phone_number_id": "106540000000001",
                "display_number": "+1 555 0100",
                "access_token": "token-one",  # pragma: allowlist secret
            },
        )
        response = await client.post(
            WHATSAPP_CONNECTION,
            json={
                "waba_id": "101000000000002",
                "phone_number_id": "106540000000002",
                "display_number": "+1 555 0200",
                "access_token": "token-two",  # pragma: allowlist secret
            },
        )

    assert response.status_code == 422


async def test_replacing_the_whatsapp_connection_never_returns_the_token(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{W_WRITE}:ORG"])):
        await client.post(
            WHATSAPP_CONNECTION,
            json={
                "waba_id": "101000000000001",
                "phone_number_id": "106540000000001",
                "display_number": "+1 555 0100",
                "access_token": "old-token",  # pragma: allowlist secret
            },
        )
        response = await client.put(
            WHATSAPP_CONNECTION,
            json={
                "waba_id": "101000000000001",
                "phone_number_id": "106540000000001",
                "display_number": "+1 555 0199",
                "access_token": "new-token",  # pragma: allowlist secret
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["display_number"] == "+1 555 0199"
    assert "old-token" not in response.text
    assert "new-token" not in response.text


async def test_replacing_whatsapp_when_no_connection_exists_is_a_404(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{W_WRITE}:ORG"])):
        response = await client.put(
            WHATSAPP_CONNECTION,
            json={
                "waba_id": "101000000000001",
                "phone_number_id": "106540000000001",
                "display_number": "+1 555 0100",
                "access_token": "token",  # pragma: allowlist secret
            },
        )
    assert response.status_code == 404


async def test_replacing_whatsapp_refuses_a_caller_holding_only_the_read(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{W_READ}:ORG"])):
        response = await client.put(
            WHATSAPP_CONNECTION,
            json={
                "waba_id": "101000000000001",
                "phone_number_id": "106540000000001",
                "display_number": "+1 555 0100",
                "access_token": "token",  # pragma: allowlist secret
            },
        )
    assert response.status_code == 403


@pytest.mark.parametrize("field", ["waba_id", "phone_number_id"])
@pytest.mark.parametrize(
    "value", ["waba-1", "12345678901234567890123456789012", "1e10", "", " 12345"]
)
async def test_a_non_numeric_or_oversized_id_is_rejected_at_the_boundary(
    client, org_id, field: str, value: str
) -> None:
    """Meta's own ids are bare digit strings. Before this pattern, a stray path segment or a
    URL could sail past the length check alone and reach a Graph API call or a database row."""
    body = {
        "waba_id": "101000000000001",
        "phone_number_id": "106540000000001",
        "display_number": "+1 555 0100",
        "access_token": "token",  # pragma: allowlist secret
    }
    body[field] = value
    with as_principal(engage_caller(org_id, [f"{W_WRITE}:ORG"])):
        response = await client.post(WHATSAPP_CONNECTION, json=body)
    assert response.status_code == 422


async def test_another_organizations_whatsapp_connection_is_invisible(client, org_id) -> None:
    other_org = uuid.uuid4()
    with as_principal(engage_caller(other_org, [f"{W_WRITE}:ORG"])):
        await client.post(
            WHATSAPP_CONNECTION,
            json={
                "waba_id": "109000000000009",
                "phone_number_id": "106549999999999",
                "display_number": "+1 555 9999",
                "access_token": "other-token",  # pragma: allowlist secret
            },
        )

    with as_principal(engage_caller(org_id, [f"{W_READ}:ORG"])):
        response = await client.get(WHATSAPP_CONNECTION)

    assert response.status_code == 200
    assert response.json()["total"] == 0
