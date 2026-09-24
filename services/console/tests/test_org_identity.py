"""The `core.org.{created,updated}` fold, run through the real consumer path.

Same shape as `test_metrics.py`'s subscription folds: the envelope goes through
`consumers.consume` — the tenant-bound, idempotent wrapper the broker drives — calling
`services.fold_org_event` directly, no broker needed. Then the bulk list and the flattened
subscription list are read on the directory session, exactly as the operator read API serves them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers, selectors
from app.price import PriceResolver
from app.services import fold_org_event, fold_subscription_event
from tests.helpers import envelope, subscription_payload

pytestmark = pytest.mark.integration

PV1 = "11111111-1111-1111-1111-111111111111"


async def _flat_price(_plan_version_id: str) -> tuple[int, str | None]:
    return 50000, "INR"


RESOLVE_PRICE: PriceResolver = _flat_price


def _org_event(
    event: str,
    org: uuid.UUID,
    *,
    slug: str | None = None,
    name: str | None = None,
    changed: list[str] | None = None,
    **kw,
) -> dict:
    payload: dict = {"organization_id": str(org)}
    if event == "created":
        payload["slug"] = slug
        payload["name"] = name
    else:
        payload["changed"] = changed if changed is not None else (["name"] if name else [])
        payload["name"] = name
    return envelope(f"core.org.{event}", organization_id=org, payload=payload, **kw)


async def _fold(env: dict) -> None:
    await consumers.consume(env, fold_org_event)


class TestOrgIdentityFold:
    async def test_a_created_event_sets_the_name_and_slug(
        self, engine_ready: None, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _fold(_org_event("created", org_id, slug="northwind-metals", name="Northwind Metals"))

        states = await selectors.all_org_states(directory_session)
        assert states[0].name == "Northwind Metals"
        assert states[0].slug == "northwind-metals"

    async def test_an_updated_event_renames_without_touching_the_slug(
        self, engine_ready: None, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _fold(_org_event("created", org_id, slug="northwind-metals", name="Northwind Metals"))
        await _fold(_org_event("updated", org_id, name="Northwind Group", changed=["name"]))

        directory_session.expire_all()
        states = await selectors.all_org_states(directory_session)
        assert states[0].name == "Northwind Group"
        assert states[0].slug == "northwind-metals", "an update must not touch the immutable slug"

    async def test_an_update_that_did_not_change_the_name_leaves_it_alone(
        self, engine_ready: None, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`changed` naming some other field (e.g. `country`) carries `name: null` — this must not
        blank the name."""
        await _fold(_org_event("created", org_id, slug="northwind-metals", name="Northwind Metals"))
        await _fold(_org_event("updated", org_id, name=None, changed=["country"]))

        directory_session.expire_all()
        states = await selectors.all_org_states(directory_session)
        assert states[0].name == "Northwind Metals"

    async def test_a_redelivered_created_event_is_a_no_op(
        self, engine_ready: None, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        event = _org_event("created", org_id, slug="northwind-metals", name="Northwind Metals")
        await _fold(event)
        await _fold(event)  # exact redelivery — same event_id

        states = await selectors.all_org_states(directory_session)
        assert len(states) == 1
        assert states[0].name == "Northwind Metals"

    async def test_a_stale_redelivery_after_a_rename_does_not_resurrect_the_old_name(
        self, engine_ready: None, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        t0 = datetime.now(UTC)
        await _fold(
            _org_event(
                "created", org_id, slug="northwind-metals", name="Northwind Metals", occurred_at=t0
            )
        )
        await _fold(
            _org_event(
                "updated",
                org_id,
                name="Northwind Group",
                changed=["name"],
                occurred_at=t0 + timedelta(minutes=10),
            )
        )
        # A DLQ replay of the original create, arriving after the rename it predates.
        await _fold(
            _org_event(
                "created", org_id, slug="northwind-metals", name="Northwind Metals", occurred_at=t0
            )
        )

        directory_session.expire_all()
        states = await selectors.all_org_states(directory_session)
        assert states[0].name == "Northwind Group", "a stale replay resurrected an old name"

    async def test_the_flattened_subscription_list_carries_the_same_name(
        self, engine_ready: None, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The bulk `/admin/subscriptions` composite reads the same projection row — a name folded
        by the identity event shows there too, per row."""
        await _fold(_org_event("created", org_id, slug="northwind-metals", name="Northwind Metals"))

        async def fold_sub(session, env):  # type: ignore[no-untyped-def]
            await fold_subscription_event(session, env, resolve_price=RESOLVE_PRICE)

        await consumers.consume(
            envelope(
                "commerce.subscription.activated",
                organization_id=org_id,
                payload=subscription_payload(
                    org_id, product_key="hrms", state="active", plan_version_id=PV1
                ),
            ),
            fold_sub,
        )

        directory_session.expire_all()
        states = await selectors.all_org_states(directory_session)
        assert states[0].name == "Northwind Metals"
        assert states[0].subscription_states["hrms"]["state"] == "active"
