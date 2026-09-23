"""Engage audience — contacts, suppressions, the public unsubscribe link, and the selector a
campaign will send through.

Real PostgreSQL as the app role, like the rest of the integration suite: uniqueness is a
database constraint on the blind index, isolation is a row-level-security policy, and the
unsubscribe route binds its own tenant — none of which Python alone can prove.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import func, select

from app import domain, models, selectors, services
from app.main import app
from platform_core.auth import Principal, Scope, get_principal
from platform_core.pii import open_sealed
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

CONTACTS = "/api/v1/engage/contacts"
IMPORT = f"{CONTACTS}/import"
SUPPRESSIONS = "/api/v1/engage/suppressions"
UNSUBSCRIBE = "/api/v1/engage/unsubscribe"

C_READ = "engage.audience.contact.read"
C_WRITE = "engage.audience.contact.write"
S_READ = "engage.audience.suppression.read"
S_WRITE = "engage.audience.suppression.write"
ALL = [f"{p}:ORG" for p in (C_READ, C_WRITE, S_READ, S_WRITE)]
ABSENT = uuid.UUID(int=0)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def caller(org_id: uuid.UUID, perms: list[str]) -> Principal:
    return principal_for(org_id, perms, entitlements=["engage"])


async def _count(session, model) -> int:
    async with session.begin():
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --------------------------------------------------------------------- contacts: happy paths


async def test_contact_crud_round_trip_masks_and_seals_the_address(client, session, org_id):
    with as_principal(caller(org_id, ALL)):
        created = await client.post(
            CONTACTS,
            json={"email": "  Asha@Example.COM ", "name": "Asha", "attributes": {"tier": "gold"}},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert "asha@example.com" not in created.text
        assert body["email"] != "asha@example.com"
        cid = body["id"]

        got = await client.get(f"{CONTACTS}/{cid}")
        assert got.status_code == 200
        assert got.json()["attributes"] == {"tier": "gold"}

        patched = await client.patch(f"{CONTACTS}/{cid}", json={"name": "Asha R"})
        assert patched.status_code == 200
        assert patched.json()["name"] == "Asha R"

        dup = await client.post(CONTACTS, json={"email": "asha@example.com"})
        assert dup.status_code == 409

        bad = await client.post(CONTACTS, json={"email": "not-an-address"})
        assert bad.status_code == 422

    async with session.begin():
        row = await session.get(models.Contact, uuid.UUID(cid))
        assert row is not None
        # Normalised before sealing, and only the sealed column holds it.
        assert open_sealed(row.email_enc, settings=services.settings()) == "asha@example.com"
        audits = (
            await session.execute(
                select(models.AuditLog.action).where(models.AuditLog.entity == "contact")
            )
        ).scalars()
        assert set(audits) >= {"engage.contact.created", "engage.contact.updated"}

    with as_principal(caller(org_id, ALL)):
        gone = await client.delete(f"{CONTACTS}/{cid}")
        assert gone.status_code == 204
        assert (await client.get(f"{CONTACTS}/{cid}")).status_code == 404


async def test_list_paginates_and_searches_by_name_and_exact_email(client, org_id):
    with as_principal(caller(org_id, ALL)):
        for i in range(3):
            await client.post(CONTACTS, json={"email": f"p{i}@example.com", "name": f"Person {i}"})
        await client.post(CONTACTS, json={"email": "zed@example.com", "name": "Zed 100%"})

        page = await client.get(CONTACTS, params={"size": 2, "page": 1})
        assert page.status_code == 200
        assert page.json()["total"] == 4
        assert len(page.json()["items"]) == 2

        by_name = await client.get(CONTACTS, params={"search": "person"})
        assert by_name.json()["total"] == 3

        by_email = await client.get(CONTACTS, params={"search": "ZED@example.com"})
        assert [c["name"] for c in by_email.json()["items"]] == ["Zed 100%"]

        # A literal `%` matches itself, not everything.
        literal = await client.get(CONTACTS, params={"search": "0%"})
        assert [c["name"] for c in literal.json()["items"]] == ["Zed 100%"]


async def test_bulk_import_reports_each_row_and_is_idempotent(client, org_id):
    rows = [
        {"email": "a@example.com", "name": "A"},
        {"email": "B@Example.com", "name": "B", "attributes": {"k": 1}},
        {"email": "nope", "name": "Bad"},
        {"email": "b@example.com", "name": "B again"},
    ]
    with as_principal(caller(org_id, ALL)):
        first = await client.post(IMPORT, json=rows)
        assert first.status_code == 200, first.text
        body = first.json()
        assert [r["status"] for r in body["results"]] == [
            "created",
            "created",
            "invalid",
            "updated",
        ]
        assert body["results"][2]["error"]
        assert (body["created"], body["updated"], body["invalid"]) == (2, 1, 1)

        again = await client.post(IMPORT, json=rows)
        assert [r["status"] for r in again.json()["results"]] == [
            "updated",
            "updated",
            "invalid",
            "updated",
        ]
        listing = await client.get(CONTACTS)
        assert listing.json()["total"] == 2


async def test_bulk_import_refuses_more_than_1000_rows(client, org_id):
    rows = [{"email": f"u{i}@example.com"} for i in range(1001)]
    with as_principal(caller(org_id, ALL)):
        response = await client.post(IMPORT, json=rows)
    assert response.status_code == 422


# --------------------------------------------------------------------- suppressions


async def test_manual_suppression_add_list_remove(client, org_id):
    with as_principal(caller(org_id, ALL)):
        added = await client.post(SUPPRESSIONS, json={"email": "Quiet@Example.com"})
        assert added.status_code == 201, added.text
        assert added.json()["reason"] == "manual"
        assert "quiet@example.com" not in added.text

        again = await client.post(SUPPRESSIONS, json={"email": "quiet@example.com"})
        assert again.status_code == 409

        listing = await client.get(SUPPRESSIONS, params={"reason": "manual"})
        assert listing.json()["total"] == 1

        removed = await client.delete(f"{SUPPRESSIONS}/{added.json()['id']}")
        assert removed.status_code == 204
        assert (await client.get(SUPPRESSIONS)).json()["total"] == 0


async def test_only_manual_suppressions_can_be_removed(client, org_id):
    token = services.unsubscribe_token(org_id, "left@example.com")
    assert (await client.post(f"{UNSUBSCRIBE}/{token}")).status_code == 200
    with as_principal(caller(org_id, ALL)):
        row = (await client.get(SUPPRESSIONS)).json()["items"][0]
        assert row["reason"] == "unsubscribed"
        response = await client.delete(f"{SUPPRESSIONS}/{row['id']}")
    assert response.status_code == 422
    with as_principal(caller(org_id, ALL)):
        assert (await client.get(SUPPRESSIONS)).json()["total"] == 1


# --------------------------------------------------------------------- unsubscribe


async def test_get_renders_a_confirmation_page_without_changing_state(client, session, org_id):
    """The link-preview-bot case: prefetching the GET must never unsubscribe anyone."""
    token = services.unsubscribe_token(org_id, "Reader@Example.com")
    page = await client.get(f"{UNSUBSCRIBE}/{token}")
    assert page.status_code == 200, page.text
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["x-robots-tag"] == "noindex"
    assert "<form" in page.text and 'method="post"' in page.text
    # The masked address may appear as a courtesy; the raw one never does.
    assert "reader@example.com" not in page.text.lower()

    assert await _count(session, models.Suppression) == 0
    assert await _count(session, models.AuditLog) == 0

    # Fetching it any number of times is still just a page.
    await client.get(f"{UNSUBSCRIBE}/{token}")
    assert await _count(session, models.Suppression) == 0


async def test_form_post_unsubscribes_and_renders_a_result_page(client, session, org_id):
    token = services.unsubscribe_token(org_id, "Reader@Example.com")
    response = await client.post(
        f"{UNSUBSCRIBE}/{token}", headers={"Accept": "text/html,application/xhtml+xml"}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-robots-tag"] == "noindex"
    assert "unsubscribed" in response.text.lower()
    assert "reader@example.com" not in response.text.lower()

    async with session.begin():
        rows = list((await session.execute(select(models.Suppression))).scalars())
        assert len(rows) == 1
        assert rows[0].reason == "unsubscribed"
        assert rows[0].organization_id == org_id


async def test_one_click_post_unsubscribes_with_the_json_body_unchanged(client, session, org_id):
    """RFC 8058: a mailbox provider's one-click POST gets exactly what it always has."""
    token = services.unsubscribe_token(org_id, "Reader@Example.com")
    first = await client.post(f"{UNSUBSCRIBE}/{token}")
    assert first.status_code == 200, first.text
    assert first.json() == {"unsubscribed": True}
    assert first.headers["content-type"].startswith("application/json")

    async with session.begin():
        audits = list(
            (
                await session.execute(
                    select(models.AuditLog).where(
                        models.AuditLog.action == "engage.suppression.unsubscribed"
                    )
                )
            ).scalars()
        )
        assert len(audits) == 1
        assert audits[0].actor_id is None


async def test_repeat_unsubscribe_is_idempotent_across_get_form_and_one_click(
    client, session, org_id
):
    token = services.unsubscribe_token(org_id, "Reader@Example.com")
    # A prefetch, a one-click POST, and a form POST, in that order — none of it writes twice.
    assert (await client.get(f"{UNSUBSCRIBE}/{token}")).status_code == 200
    assert (await client.post(f"{UNSUBSCRIBE}/{token}")).status_code == 200
    form = await client.post(f"{UNSUBSCRIBE}/{token}", headers={"Accept": "text/html"})
    assert form.status_code == 200
    assert (await client.get(f"{UNSUBSCRIBE}/{token}")).status_code == 200

    assert await _count(session, models.Suppression) == 1
    async with session.begin():
        audits = list(
            (
                await session.execute(
                    select(models.AuditLog).where(
                        models.AuditLog.action == "engage.suppression.unsubscribed"
                    )
                )
            ).scalars()
        )
        assert len(audits) == 1


def _tamper(token: str) -> str:
    org, _email, mac = token.split(".")
    return f"{org}.{domain._b64e(b'victim@example.com')}.{mac}"


@pytest.mark.parametrize(
    "make_token",
    [
        lambda org: "garbage",
        lambda org: "a.b.c",
        lambda org: "",
        lambda org: _tamper(services.unsubscribe_token(org, "reader@example.com")),
        # Signed with the wrong key.
        lambda org: domain.make_unsubscribe_token(org, "reader@example.com", b"wrong-key"),
        # A genuine token for another organization's address, with this org's id swapped in.
        lambda org: (
            domain._b64e(org.bytes)
            + "."
            + services.unsubscribe_token(uuid.uuid4(), "reader@example.com").split(".", 1)[1]
        ),
    ],
)
async def test_bad_or_forged_unsubscribe_token_is_refused_without_side_effects(
    client, session, org_id, make_token
):
    token = make_token(org_id)
    path = f"{UNSUBSCRIBE}/{token}" if token else f"{UNSUBSCRIBE}/x"
    get_response = await client.get(path)
    assert get_response.status_code == 404
    assert get_response.headers["content-type"].startswith("text/html")

    post_response = await client.post(path)
    assert post_response.status_code == 404
    assert post_response.headers["content-type"].startswith("application/problem+json")

    html_post_response = await client.post(path, headers={"Accept": "text/html"})
    assert html_post_response.status_code == 404
    assert html_post_response.headers["content-type"].startswith("text/html")

    assert await _count(session, models.Suppression) == 0
    assert await _count(session, models.AuditLog) == 0


async def test_token_round_trip_and_constant_shape():
    org = uuid.uuid4()
    token = domain.make_unsubscribe_token(org, "a@example.com", b"k")
    assert domain.verify_unsubscribe_token(token, b"k") == (org, "a@example.com")
    assert domain.verify_unsubscribe_token(token, b"other") is None
    assert domain.verify_unsubscribe_token(token + "x", b"k") is None


async def test_placeholder_key_is_refused_in_a_real_environment(monkeypatch):
    cfg = services.settings()
    monkeypatch.setattr(cfg, "environment", "production")
    assert services.unsubscribe_key() is None


# --------------------------------------------------------------------- the send selector


async def test_sendable_contacts_excludes_suppressed_addresses(session, org_id):
    write = ctx_for(org_id, C_WRITE, Scope.ORG)
    s_write = ctx_for(org_id, S_WRITE, Scope.ORG)
    async with session.begin():
        keep = await services.create_contact(session, write, email="keep@example.com")
        await services.create_contact(session, write, email="manual@example.com")
        await services.create_contact(session, write, email="unsub@example.com")
        await services.add_suppression(session, s_write, email="MANUAL@example.com")
        await services.unsubscribe(session, organization_id=org_id, email="unsub@example.com")
        # A suppression for an address that is not (yet) a contact is harmless.
        await services.add_suppression(session, s_write, email="later@example.com")

    read = ctx_for(org_id, C_READ, Scope.ORG)
    async with session.begin():
        sendable = await selectors.sendable_contacts(session, read)
    assert [c.id for c in sendable] == [keep.id]

    # A suppressed address that is imported later is still excluded.
    async with session.begin():
        await services.create_contact(session, write, email="later@example.com")
        sendable = await selectors.sendable_contacts(session, read)
    assert [c.id for c in sendable] == [keep.id]


# --------------------------------------------------------------------- negative authz

ROUTES = [
    ("get", CONTACTS, None, C_WRITE),
    ("get", f"{CONTACTS}/{ABSENT}", None, C_WRITE),
    ("post", CONTACTS, {"email": "x@example.com"}, C_READ),
    ("patch", f"{CONTACTS}/{ABSENT}", {"name": "x"}, C_READ),
    ("delete", f"{CONTACTS}/{ABSENT}", None, C_READ),
    ("post", IMPORT, [{"email": "x@example.com"}], C_READ),
    ("get", SUPPRESSIONS, None, S_WRITE),
    ("post", SUPPRESSIONS, {"email": "x@example.com"}, S_READ),
    ("delete", f"{SUPPRESSIONS}/{ABSENT}", None, S_READ),
]


@pytest.mark.parametrize(("method", "path", "body", "other"), ROUTES)
async def test_every_route_refuses_a_caller_without_its_permission(
    client, session, org_id, method, path, body, other
):
    """Holding the sibling permission (read for a write, write for a read) is not enough."""
    kwargs = {"json": body} if body is not None else {}
    with as_principal(caller(org_id, [f"{other}:ORG"])):
        response = await client.request(method.upper(), path, **kwargs)
    assert response.status_code == 403
    assert await _count(session, models.Contact) == 0


@pytest.mark.parametrize(("method", "path", "body", "other"), ROUTES)
async def test_the_notification_entitlement_does_not_unlock_the_audience(
    client, org_id, method, path, body, other
):
    kwargs = {"json": body} if body is not None else {}
    with as_principal(principal_for(org_id, ALL, entitlements=["notification"])):
        response = await client.request(method.upper(), path, **kwargs)
    assert response.status_code == 402


# --------------------------------------------------------------------- cross-tenant isolation


async def test_another_tenants_audience_is_invisible_and_untouchable(
    client, session, admin_session, org_id
):
    other = uuid.uuid4()
    other_ctx = ctx_for(other, C_WRITE, Scope.ORG)
    async with admin_session.begin():
        theirs = await services.create_contact(admin_session, other_ctx, email="them@example.com")
        their_sup = await services.add_suppression(
            admin_session, ctx_for(other, S_WRITE, Scope.ORG), email="gone@example.com"
        )
    theirs_id, their_sup_id = theirs.id, their_sup.id

    try:
        with as_principal(caller(org_id, ALL)):
            assert (await client.get(CONTACTS)).json()["total"] == 0
            assert (await client.get(SUPPRESSIONS)).json()["total"] == 0
            assert (await client.get(f"{CONTACTS}/{theirs_id}")).status_code == 404
            assert (
                await client.patch(f"{CONTACTS}/{theirs_id}", json={"name": "pwned"})
            ).status_code == 404
            assert (await client.delete(f"{CONTACTS}/{theirs_id}")).status_code == 404
            assert (await client.delete(f"{SUPPRESSIONS}/{their_sup_id}")).status_code == 404
            # Same address in this tenant is a different contact, not a conflict.
            mine = await client.post(CONTACTS, json={"email": "them@example.com"})
            assert mine.status_code == 201

        # RLS, not just the filter: the app-role session sees none of theirs.
        async with session.begin():
            ids = set((await session.execute(select(models.Contact.id))).scalars())
        assert theirs_id not in ids

        async with admin_session.begin():
            row = await admin_session.get(models.Contact, theirs_id)
            assert row is not None and row.name == ""
    finally:
        async with admin_session.begin():
            for model in (models.Contact, models.Suppression):
                await admin_session.execute(
                    model.__table__.delete().where(model.organization_id == other)
                )


async def test_unsubscribe_binds_the_tenant_from_the_token(client, session, org_id):
    """The link's organization is the one written to — and a link for another organization
    cannot write into this one, because the GUC is bound to the token's org."""
    token = services.unsubscribe_token(org_id, "mine@example.com")
    assert (await client.post(f"{UNSUBSCRIBE}/{token}")).status_code == 200
    async with session.begin():
        orgs = set((await session.execute(select(models.Suppression.organization_id))).scalars())
    assert orgs == {org_id}
