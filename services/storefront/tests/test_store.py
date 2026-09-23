"""The store's identity: its brand, its address and its status (ADR-0011).

Three things here are worth more than the rest, because each is a failure that would otherwise be
found by a merchant rather than by this file:

* **A brand is identity, not styling.** These tests read the name back off the identity row, not
  off a theme document, which is the whole of ADR-0011 §2.
* **A settings save cannot publish a store.** `status` is not on the payload at all, and
  `extra="forbid"` turns an attempt into a 422. Publishing is its own endpoint with its own audit
  row, because putting a store in front of the internet is not a side effect of editing a tagline.
* **`custom_domain` is not a field on this payload at all** (ADR-0012 review finding 2b) — ADR-0011
  shipped it here first, evidence-free, and `PUT /storefront/store/domain`
  (`tests/test_store_domain.py`) is now the only writer. This file only proves the field is
  refused; the domain claim itself — its verification, its `www`-partner collision, its
  reachability (or lack of it) — is that other file's job.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.main import app
from app.models import (
    LOGO_TEXT_LENGTH,
    STORE_NAME_LENGTH,
    TAGLINE_LENGTH,
    AuditLog,
    Storefront,
)
from tests.helpers import all_perms, seed_store, store_payload

pytestmark = pytest.mark.integration

STORE = "/api/v1/storefront/store"
PUBLISH = "/api/v1/storefront/store/publish"
UNPUBLISH = "/api/v1/storefront/store/unpublish"
SERVICEABILITY = "/api/v1/storefront/public/serviceability"


def _code(response) -> str:
    return str(response.json()["errors"][0]["code"])


async def _audit_actions(session, org_id: uuid.UUID) -> list[str]:
    rows = (
        await session.execute(
            select(AuditLog.action)
            .where(AuditLog.organization_id == org_id)
            .order_by(AuditLog.at.asc())
        )
    ).scalars()
    return list(rows)


class TestTheBrandIsIdentity:
    async def test_put_creates_the_store_with_its_brand_and_get_reads_it_back(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """The name, tagline and logo text are on the store's own row — not on the theme
        document, and therefore not at risk when a merchant adopts a different theme."""
        with as_principal(all_perms(org_id)):
            created = await client.put(
                STORE,
                json=store_payload(
                    slug, name="Verma Traders", tagline="Since 1994", logo_text="VT"
                ),
            )
            read = await client.get(STORE)

        assert created.status_code == 200
        body = read.json()
        assert body["name"] == "Verma Traders"
        assert body["tagline"] == "Since 1994"
        assert body["logo_text"] == "VT"
        assert body["id"] == created.json()["id"]

    async def test_a_new_store_is_always_draft(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """A store cannot be born published. There is no payload that can do it."""
        with as_principal(all_perms(org_id)):
            created = await client.put(STORE, json=store_payload(slug))
        assert created.json()["status"] == "draft"

    async def test_status_is_not_a_field_on_the_settings_payload(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """The whole of ADR-0011 §4 in one assertion: a save cannot go live. `extra="forbid"`
        makes the attempt a 422 rather than a silently ignored field, because a setting a
        merchant believes they changed is worse than one they were refused."""
        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, status="published"))
        assert response.status_code == 422

    async def test_an_unknown_field_is_refused(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, theme="retail"))
        assert response.status_code == 422

    async def test_tagline_and_logo_text_are_optional_and_null_clears_them(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """Cleared and never-set are one state. Storing `''` would make them two that render
        identically and compare differently."""
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug, tagline="Since 1994", logo_text="VT"))
            cleared = await client.put(STORE, json=store_payload(slug, tagline=None, logo_text=""))

        assert cleared.json()["tagline"] is None
        assert cleared.json()["logo_text"] is None

    async def test_an_unknown_timezone_is_refused_in_the_merchants_words(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """A bad IANA name would silently produce promised dates in UTC — wrong by hours, and
        perfectly well-formed."""
        with as_principal(all_perms(org_id)):
            response = await client.put(
                STORE, json=store_payload(slug, timezone="Mars/Olympus_Mons")
            )
        assert response.status_code == 422
        assert _code(response) == "timezone_invalid"


class TestBrandTextAtAndBeyondItsLimits:
    async def test_a_name_at_the_limit_is_accepted_and_one_past_it_is_not(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            at_limit = await client.put(
                STORE, json=store_payload(slug, name="v" * STORE_NAME_LENGTH)
            )
            past_limit = await client.put(
                STORE, json=store_payload(slug, name="v" * (STORE_NAME_LENGTH + 1))
            )

        assert at_limit.status_code == 200
        assert len(at_limit.json()["name"]) == STORE_NAME_LENGTH
        assert past_limit.status_code == 422
        # Pinned because a client branches on it: the boundary's own `too_long` is what a
        # merchant's form sees, not the domain's `name_too_long`, which is the net behind it for
        # a caller that is not a request.
        assert _code(past_limit) == "too_long"

    @pytest.mark.parametrize(
        ("field", "limit"),
        [("tagline", TAGLINE_LENGTH), ("logo_text", LOGO_TEXT_LENGTH)],
    )
    async def test_each_optional_field_is_bounded_too(
        self, client, as_principal, org_id: uuid.UUID, slug: str, field: str, limit: int
    ) -> None:
        with as_principal(all_perms(org_id)):
            at_limit = await client.put(STORE, json=store_payload(slug, **{field: "x" * limit}))
            past_limit = await client.put(
                STORE, json=store_payload(slug, **{field: "x" * (limit + 1)})
            )

        assert at_limit.status_code == 200
        assert len(at_limit.json()[field]) == limit
        assert past_limit.status_code == 422
        assert _code(past_limit) == "too_long"

    async def test_whitespace_is_trimmed_and_collapsed_before_it_is_stored(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """A pasted name with a trailing space is the same shop sign as one without, and it must
        not sort, search or render differently."""
        with as_principal(all_perms(org_id)):
            response = await client.put(
                STORE, json=store_payload(slug, name="  Verma   Traders  ", tagline=" Since 1994 ")
            )

        assert response.json()["name"] == "Verma Traders"
        assert response.json()["tagline"] == "Since 1994"

    async def test_trimming_happens_before_the_limit_so_trailing_spaces_cannot_overflow_it(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.put(
                STORE, json=store_payload(slug, name=("v" * STORE_NAME_LENGTH) + "   ")
            )
        assert response.status_code == 200

    async def test_a_name_that_is_empty_after_trimming_is_refused_by_name(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, name="   "))
        assert response.status_code == 422
        assert _code(response) == "name_required"

    async def test_an_empty_tagline_is_cleared_rather_than_refused(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, tagline="   "))
        assert response.status_code == 200
        assert response.json()["tagline"] is None

    @pytest.mark.parametrize("value", ["Verma‮Traders", "Verma\x00Traders", "Verma\x07"])
    async def test_text_that_cannot_be_rendered_on_a_line_is_refused(
        self, client, as_principal, org_id: uuid.UUID, slug: str, value: str
    ) -> None:
        """Control characters and bidi overrides — the latter being a display-spoofing trick.
        **This is not HTML escaping**: the renderer owns output encoding, and this service
        stores exactly what it was given once whitespace is collapsed (ADR-0011 §6)."""
        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, name=value))
        assert response.status_code == 422
        assert _code(response) == "name_invalid"

    async def test_a_pasted_line_break_is_collapsed_rather_than_refused(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """A name pasted out of a spreadsheet cell carries a newline. Refusing that would be
        arguing with a merchant about their clipboard; a store name is one line, so it becomes
        one line."""
        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, name="Verma\nTraders\t"))
        assert response.status_code == 200
        assert response.json()["name"] == "Verma Traders"

    async def test_markup_is_stored_verbatim_because_escaping_belongs_to_the_renderer(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """Deliberate, and the frontend needs to know it: a name containing angle brackets comes
        back exactly as it was typed. Encoding it here would double-escape the first time
        anything else read this column, and would still not protect a client that injected it
        as HTML."""
        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, name="<b>Verma</b> & Sons"))
        assert response.json()["name"] == "<b>Verma</b> & Sons"


class TestTheSlugCannotChange:
    async def test_a_different_slug_on_an_existing_store_is_a_named_409(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """Every published link, the buyer's localStorage key and the five-minute slug cache all
        key on it (ADR-0011 §7)."""
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            response = await client.put(STORE, json=store_payload(f"{slug}x"))

        assert response.status_code == 409
        assert _code(response) == "slug_immutable"

    async def test_sending_the_same_slug_back_is_how_the_form_saves(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            response = await client.put(STORE, json=store_payload(slug, name="Renamed"))

        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"

    async def test_a_slug_another_organization_holds_is_a_named_409(
        self, client, as_principal, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        """Slugs are globally unique, so this collision crosses a tenant boundary — and nothing
        about the other organization is disclosed beyond "taken"."""
        taken = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, taken)

        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(taken))

        assert response.status_code == 409
        assert _code(response) == "slug_taken"
        assert "organization" not in response.json()["detail"].lower()


class TestTheCustomDomainIsNoLongerOnThisPayload:
    """ADR-0012 review finding 2b. ADR-0011 shipped `custom_domain` on this route first, and a
    merchant could land on `custom_domain_status = 'unverified'` with **no evidence row behind
    it** — nothing for the domain sweep to ever pick up, and no `www`-partner collision check.
    `PUT /storefront/store/domain` (`tests/test_store_domain.py`) is now the only writer, and it
    always creates the evidence row alongside the column. This route refuses the field outright
    rather than silently ignoring it, and says where to go instead."""

    async def test_sending_custom_domain_is_a_422_naming_the_dedicated_route(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.put(
                STORE, json=store_payload(slug, custom_domain="shop.example.com")
            )

        assert response.status_code == 422
        detail = response.json()["detail"] + str(response.json().get("errors", ""))
        assert "/storefront/store/domain" in detail

    async def test_a_store_can_still_be_created_and_updated_with_no_domain_field_at_all(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            created = await client.put(STORE, json=store_payload(slug))
            updated = await client.put(STORE, json=store_payload(slug, name="Verma & Sons"))

        assert created.status_code == 200
        assert created.json()["custom_domain"] is None
        assert updated.status_code == 200
        assert updated.json()["name"] == "Verma & Sons"

    def test_there_is_no_route_that_verifies_a_domain_set_through_this_form(self) -> None:
        """An honest dead end for the field's own old promise, asserted against the published
        contract: `custom_domain` cannot be set here at all any more, so nothing here could ever
        verify one either."""
        assert not [p for p in app.openapi()["paths"] if "verif" in p.lower()]


class TestPublishingIsItsOwnDeliberateAct:
    async def test_publish_then_unpublish_moves_the_status_and_nothing_else(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            created = await client.put(STORE, json=store_payload(slug))
            published = await client.post(PUBLISH)
            unpublished = await client.post(UNPUBLISH)

        assert created.json()["status"] == "draft"
        assert published.json()["status"] == "published"
        assert unpublished.json()["status"] == "draft"

    async def test_both_transitions_are_idempotent(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """A double-click on the one control in this product that has consequences is not an
        error."""
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            first = await client.post(PUBLISH)
            second = await client.post(PUBLISH)

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["status"] == "published"

    async def test_publishing_is_audited_as_its_own_act(
        self, client, as_principal, session, org_id: uuid.UUID, slug: str
    ) -> None:
        """A status change is what makes a store visible to the internet — and with it that
        merchant's delivery map readable by anyone who knows the slug. It is not a field inside
        a generic update row (ADR-0010 §14)."""
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            await client.post(PUBLISH)
            await client.post(UNPUBLISH)

        assert await _audit_actions(session, org_id) == [
            "storefront.created",
            "storefront.published",
            "storefront.unpublished",
        ]

    @pytest.mark.parametrize("path", [PUBLISH, UNPUBLISH])
    async def test_a_transition_without_a_store_is_a_404(
        self, client, as_principal, org_id: uuid.UUID, path: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.post(path)
        assert response.status_code == 404

    @pytest.mark.parametrize("path", [PUBLISH, UNPUBLISH])
    async def test_a_suspended_store_refuses_both_transitions_by_name(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        slug: str,
        path: str,
    ) -> None:
        """`suspended` belongs to the entitlement-lapse consumer ADR-0010 §4 describes and does
        not own. A merchant who could leave the state would make that mechanism advisory."""
        await seed_store(admin_session, org_id, slug, status="suspended")

        with as_principal(all_perms(org_id)):
            response = await client.post(path)

        assert response.status_code == 409
        assert _code(response) == "store_suspended"

    async def test_a_suspended_store_can_still_have_its_brand_edited(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """Suspension stops a store being served, not a merchant fixing their own settings."""
        await seed_store(admin_session, org_id, slug, status="suspended")

        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, name="Still Mine"))

        assert response.status_code == 200
        assert response.json()["status"] == "suspended"

    async def test_publishing_is_what_makes_the_anonymous_check_answer(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """End to end, through the real bypass reader and the real Redis cache: a draft store is
        invisible to a stranger, publishing makes it answer, and unpublishing hides it again.
        The cache invalidation in `set_store_status` is what makes the middle step work — without
        it the merchant publishes and watches nothing happen for five minutes."""
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))

        while_draft = await client.post(
            SERVICEABILITY, json={"store": slug, "postal_code": "400050"}
        )
        with as_principal(all_perms(org_id)):
            await client.post(PUBLISH)
        while_published = await client.post(
            SERVICEABILITY, json={"store": slug, "postal_code": "400050"}
        )
        with as_principal(all_perms(org_id)):
            await client.post(UNPUBLISH)
        after_unpublish = await client.post(
            SERVICEABILITY, json={"store": slug, "postal_code": "400050"}
        )

        assert while_draft.status_code == 404
        assert while_published.status_code == 200
        assert after_unpublish.status_code == 404


class TestCrossTenantIsolationOnTheIdentityRow:
    async def test_a_put_cannot_reach_another_organizations_store(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
    ) -> None:
        """Their row is invisible under row-level security, so this creates *this* tenant's
        store instead of editing theirs — and theirs is untouched."""
        theirs = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, theirs, name="Theirs")

        with as_principal(all_perms(org_id)):
            mine = await client.put(STORE, json=store_payload(slug, name="Mine"))

        await admin_session.rollback()
        rows = {
            row.organization_id: row.name
            for row in (await admin_session.execute(select(Storefront))).scalars().all()
        }
        assert mine.status_code == 200
        assert rows[other_org] == "Theirs"
        assert rows[org_id] == "Mine"

    @pytest.mark.parametrize("path", [PUBLISH, UNPUBLISH])
    async def test_a_transition_cannot_reach_another_organizations_store(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        path: str,
    ) -> None:
        theirs = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, theirs, status="draft")

        with as_principal(all_perms(org_id)):
            response = await client.post(path)

        await admin_session.rollback()
        store = (
            await admin_session.execute(
                select(Storefront).where(Storefront.organization_id == other_org)
            )
        ).scalar_one()
        assert response.status_code == 404
        assert store.status == "draft"
