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

import base64
import contextlib
import json
import uuid
from collections.abc import Iterator

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from sqlalchemy import select

from app import models, services
from app.config import settings
from app.main import app
from app.transports import RecordingTransport, TransportRefused, TransportUnavailable
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import BusinessRuleProblem, NotFoundProblem, UpstreamUnavailableProblem
from platform_core.pii import open_sealed
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


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


async def test_creating_a_domain_mints_a_dkim_keypair(session, org_id) -> None:
    """`dns_records_for` is pure and leaves the DKIM value empty for its caller to fill. This
    is that caller: a domain created without a key publishes a record that can never verify,
    which makes the whole setup flow decorative."""
    async with session.begin():
        row = await services.create_domain(
            session, _ctx(org_id, "engage.setup.domain.write"), domain="company.com"
        )

    dkim = next(r for r in row.records if r["kind"] == "dkim")
    assert dkim["value"].startswith("v=DKIM1; k=rsa; p=")
    # Decodes to a real SubjectPublicKeyInfo of the size verifiers expect, rather than to a
    # plausible-looking string: a record that parses everywhere except at a verifier is exactly
    # the failure this assertion exists to catch.
    published = base64.b64decode(dkim["value"].split("p=", 1)[1])
    assert serialization.load_der_public_key(published).key_size == 2048


async def test_the_dkim_private_key_is_sealed(session, org_id) -> None:
    async with session.begin():
        row = await services.create_domain(
            session, _ctx(org_id, "engage.setup.domain.write"), domain="company.com"
        )

    assert row.dkim_private_key_enc is not None
    assert b"PRIVATE KEY" not in row.dkim_private_key_enc
    assert "BEGIN PRIVATE KEY" in open_sealed(row.dkim_private_key_enc, settings=settings())


async def test_the_dkim_selector_is_derived_rather_than_random(session, org_id) -> None:
    """Stability is the point: the record set is stored so the setup screen shows the values it
    showed yesterday, and a selector minted from a uuid would hand the customer a different
    record to paste every time anything regenerated it."""
    async with session.begin():
        row = await services.create_domain(
            session, _ctx(org_id, "engage.setup.domain.write"), domain="company.com"
        )

    dkim = next(r for r in row.records if r["kind"] == "dkim")
    selector = dkim["host"].split("._domainkey.")[0]
    assert selector == services.dkim_selector(org_id, "company.com")
    assert selector != services.dkim_selector(org_id, "other.example.com")


async def test_a_domain_verifies_once_dns_publishes_every_required_record(session, org_id) -> None:
    ctx = _ctx(org_id, "engage.setup.domain.write")
    async with session.begin():
        row = await services.create_domain(session, ctx, domain="company.com")

    published = {(r["host"], r["type"]): [r["value"]] for r in row.records if r["required"]}
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
        row = await services.create_domain(session, ctx, domain="company.com")

    async with session.begin():
        checked = await services.verify_domain(
            session, ctx, domain_id=row.id, resolver=FakeResolver({})
        )

    assert checked.status == "pending"
    assert checked.last_checked_at is not None
    assert await _events(session, services.EVT_DOMAIN_VERIFIED) == 0


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
    provider rejected is a campaign that fails one address at a time, in front of customers."""
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

    # The provider's own words, carried out intact. "Check your settings" would leave the
    # customer guessing which of the fourteen things they typed is the wrong one.
    assert "535 5.7.8 Username and Password not accepted" in str(refusal.value)
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

    assert "Operation timed out" in str(outage.value)
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

CONNECTION = "/api/v1/engage/connection"
DOMAINS = "/api/v1/engage/domains"
SENDERS = "/api/v1/engage/senders"

#: An id this organization has no row for. Used where the assertion is about a guard rather
#: than about a row, so the refusal lands before anything is looked up or sent.
ABSENT = uuid.UUID("11111111-1111-4111-8111-111111111111")

READ_ROUTES = [CONNECTION, DOMAINS, SENDERS]

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


async def test_adding_a_domain_returns_what_the_customer_has_to_publish(client, org_id) -> None:
    """The record set is the point of the response. A setup screen that cannot show it leaves
    the customer with a domain row and nothing to paste into their registrar."""
    with as_principal(engage_caller(org_id, [f"{D_WRITE}:ORG"])):
        response = await client.post(DOMAINS, json={"domain": "Company.com"})

    body = response.json()
    assert response.status_code == 201
    assert body["domain"] == "company.com"
    assert body["status"] == "pending"
    assert {r["kind"] for r in body["records"]} == {
        "spf",
        "dkim",
        "dmarc",
        "return_path",
        "tracking",
    }
    # The DKIM record carries the public half. The private half is sealed in the row and has no
    # field on the way out — a signing key in a response body is a kit for forging this
    # customer's mail.
    assert "PRIVATE KEY" not in response.text


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
