"""The store's identity: its brand, its address, its status and its domain claim (ADR-0011).

Four things here are worth more than the rest, because each is a failure that would otherwise be
found by a merchant rather than by this file:

* **A brand is identity, not styling.** These tests read the name back off the identity row, not
  off a theme document, which is the whole of ADR-0011 §2.
* **A settings save cannot publish a store.** `status` is not on the payload at all, and
  `extra="forbid"` turns an attempt into a 422. Publishing is its own endpoint with its own audit
  row, because putting a store in front of the internet is not a side effect of editing a tagline.
* **Two tenants claiming one domain is a named 409, not a 500.** The collision is reachable only
  across organizations — the row that collides is invisible to the tenant colliding with it — so
  the other organization's store is seeded on the bypass role and the claim is made over HTTP.
* **The domain claim does not make anything reachable.** The status it comes back with says so,
  and there is no path off `unverified`, because nothing in this repository serves a custom
  domain.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.domain import MAX_CUSTOM_DOMAIN_LENGTH
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


class TestTheCustomDomainIsAnIntentionAndNotAnAddress:
    async def test_claiming_one_stores_it_unverified_and_says_so(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """Nothing in this platform serves a custom domain — no ingress, no TLS termination, no
        host-based routing — so the response must not let a merchant conclude their site is
        live (ADR-0011 §5)."""
        with as_principal(all_perms(org_id)):
            response = await client.put(
                STORE, json=store_payload(slug, custom_domain="Shop.Example.COM.")
            )

        body = response.json()
        assert body["custom_domain"] == "shop.example.com"
        assert body["custom_domain_status"] == "unverified"

    def test_there_is_no_route_that_moves_it_off_unverified(self) -> None:
        """An honest dead end, asserted against the published contract. If a verification
        endpoint ever appears, this is what tells whoever adds it to update ADR-0011 rather than
        leaving the field quietly lying about what it does."""
        assert not [p for p in app.openapi()["paths"] if "verif" in p.lower()]

    async def test_changing_the_domain_resets_the_status(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """A verification attaches to a value, not to a row. Written now, while it costs a
        line."""
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug, custom_domain="shop.example.com"))
            changed = await client.put(
                STORE, json=store_payload(slug, custom_domain="store.example.com")
            )

        assert changed.json()["custom_domain"] == "store.example.com"
        assert changed.json()["custom_domain_status"] == "unverified"

    async def test_clearing_it_releases_the_claim_and_the_status_with_it(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug, custom_domain="shop.example.com"))
            cleared = await client.put(STORE, json=store_payload(slug, custom_domain=None))

        assert cleared.json()["custom_domain"] is None
        assert cleared.json()["custom_domain_status"] is None

    async def test_omitting_it_leaves_the_claim_alone(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        """The identity form no longer carries the domain; the domain screen owns it. A save of
        the name must not release a claim that screen made."""
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug, custom_domain="shop.example.com"))
            renamed = await client.put(STORE, json=store_payload(slug, name="Verma & Sons"))

        assert renamed.json()["name"] == "Verma & Sons"
        assert renamed.json()["custom_domain"] == "shop.example.com"
        assert renamed.json()["custom_domain_status"] == "unverified"

    async def test_a_released_domain_can_then_be_claimed_by_another_organization(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
    ) -> None:
        """The partial unique index is over live claims, so releasing one actually releases
        it — which is the only remedy a squatted domain has until verification exists."""
        theirs = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, theirs, custom_domain="shop.example.com")

        with as_principal(all_perms(org_id)):
            refused = await client.put(
                STORE, json=store_payload(slug, custom_domain="shop.example.com")
            )

        await admin_session.rollback()
        store = (
            await admin_session.execute(
                select(Storefront).where(Storefront.organization_id == other_org)
            )
        ).scalar_one()
        store.custom_domain = None
        store.custom_domain_status = None
        await admin_session.commit()

        with as_principal(all_perms(org_id)):
            accepted = await client.put(
                STORE, json=store_payload(slug, custom_domain="shop.example.com")
            )

        assert refused.status_code == 409
        assert accepted.status_code == 200

    async def test_two_organizations_claiming_one_domain_is_a_named_409_not_a_500(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
    ) -> None:
        """**The collision path.** It is only reachable across tenants: row-level security hides
        the row that collides from the organization colliding with it, which is exactly why the
        unique index is the arbiter and a pre-flight SELECT would be both a cross-tenant read
        and a race."""
        theirs = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, theirs, custom_domain="shop.example.com")

        with as_principal(all_perms(org_id)):
            response = await client.put(
                STORE, json=store_payload(slug, custom_domain="shop.example.com")
            )

        assert response.status_code == 409
        assert _code(response) == "custom_domain_taken"
        # Nothing about the other tenant: not its slug, not its id, not its name.
        assert theirs not in response.text
        assert str(other_org) not in response.text

    async def test_the_losing_claim_leaves_the_store_editable_afterwards(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
    ) -> None:
        """The savepoint earns its place here: a 409 must not leave a merchant unable to save
        anything else."""
        theirs = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, theirs, custom_domain="shop.example.com")

        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            refused = await client.put(
                STORE, json=store_payload(slug, custom_domain="shop.example.com")
            )
            recovered = await client.put(STORE, json=store_payload(slug, name="Still Editable"))

        assert refused.status_code == 409
        assert recovered.status_code == 200
        assert recovered.json()["name"] == "Still Editable"
        assert recovered.json()["custom_domain"] is None

    @pytest.mark.parametrize(
        "value",
        [
            "https://shop.example.com",
            "shop.example.com/path",
            "shop.example.com:8443",
            "*.example.com",
            "localhost",
            "192.168.0.1",
            "köln.de",
            "-shop.example.com",
            "shop..example.com",
        ],
    )
    async def test_a_value_that_is_not_a_hostname_is_refused(
        self, client, as_principal, org_id: uuid.UUID, slug: str, value: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, custom_domain=value))
        assert response.status_code == 422
        assert _code(response) in {"custom_domain_invalid", "custom_domain_reserved"}

    async def test_a_domain_longer_than_a_domain_can_be_is_refused(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        too_long = ".".join(["a" * 60] * 4) + ".example.com"
        assert len(too_long) > MAX_CUSTOM_DOMAIN_LENGTH
        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, custom_domain=too_long))
        assert response.status_code == 422

    @pytest.mark.parametrize("value", ["opsgrids.com", "api.opsgrids.com", "shop.opsgrids.dev"])
    async def test_the_platforms_own_domains_cannot_be_claimed(
        self, client, as_principal, org_id: uuid.UUID, slug: str, value: str
    ) -> None:
        """Nothing verifies ownership, so the one claim that must not be storable is the
        platform's own: an unverified row naming `api.opsgrids.com` becomes live routing the day
        somebody builds host-based routing and trusts this column."""
        with as_principal(all_perms(org_id)):
            response = await client.put(STORE, json=store_payload(slug, custom_domain=value))
        assert response.status_code == 422
        assert _code(response) == "custom_domain_reserved"

    async def test_a_claim_is_audited_on_its_own_row(
        self, client, as_principal, session, org_id: uuid.UUID, slug: str
    ) -> None:
        """A domain claim is the one field here two organizations can contest, and support
        adjudicating one should not have to read it out of a generic update's `after` blob."""
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug, custom_domain="shop.example.com"))
            await client.put(STORE, json=store_payload(slug, custom_domain=None))

        # Sorted, not ordered: the two rows a single request writes share its transaction and
        # therefore share `at` to the microsecond, so their relative order is not a fact.
        assert sorted(await _audit_actions(session, org_id)) == [
            "storefront.created",
            "storefront.custom_domain.claimed",
            "storefront.custom_domain.released",
            "storefront.updated",
        ]


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
