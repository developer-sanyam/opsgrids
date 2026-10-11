"""What the deal reads say about a deal's stage once its key is resolved against the
organization's pipeline: `stage_label`, `stage_outcome`, `stage_position`, `stage_is_last_open`
and `closed_at` on every deal, and the `outcome` filter on the list.

Every deal read loads the pipeline once and resolves each row against it, so a custom stage is
as readable as a legacy one — and a stored key the pipeline cannot resolve renders as an open
deal, labelled by its own key, rather than blanking the list.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from sqlalchemy import text

from app.main import app
from app.pipeline import StageSpec
from platform_core.auth import Principal, get_principal
from tests.conftest import DEMO_NOW
from tests.helpers import principal_for
from tests.pipeline_support import (
    CUSTOM_STAGES,
    OWNER,
    insert_deal,
    install_pipeline,
    make_deal,
)

pytestmark = pytest.mark.integration

WIRE = (
    "stage",
    "stage_label",
    "stage_outcome",
    "stage_position",
    "stage_is_last_open",
)

#: The six default stages as a reader should see them: (label, outcome, position, last open).
DEFAULT_READ = {
    "lead": ("Lead", "open", 0, False),
    "qualified": ("Qualified", "open", 1, False),
    "proposal": ("Proposal", "open", 2, False),
    "negotiation": ("Negotiation", "open", 3, True),
    "closed_won": ("Closed Won", "won", 4, False),
    "closed_lost": ("Closed Lost", "lost", 5, False),
}


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def reader(org_id: uuid.UUID, scope: str = "ORG", subject: uuid.UUID | None = None) -> Principal:
    return principal_for(org_id, [f"crm.pipeline.deal.read:{scope}"], subject_ref=subject or OWNER)


async def list_deals(client, query: str = "") -> list[dict]:
    response = await client.get(f"/api/v1/deals?size=100{query}")
    assert response.status_code == 200, response.text
    return response.json()["items"]


class TestDealOut:
    async def test_a_default_pipeline_deal_names_its_stage_outcome_and_position(
        self, client, org_id, data
    ) -> None:
        with as_principal(reader(org_id)):
            open_row = (await client.get(f"/api/v1/deals/{data['deal_a_id']}")).json()
            won_row = (await client.get(f"/api/v1/deals/{data['deal_b_id']}")).json()
        assert {k: open_row[k] for k in WIRE} == {
            "stage": "negotiation",
            "stage_label": "Negotiation",
            "stage_outcome": "open",
            "stage_position": 3,
            "stage_is_last_open": True,
        }
        assert open_row["closed_at"] is None
        assert {k: won_row[k] for k in WIRE} == {
            "stage": "closed_won",
            "stage_label": "Closed Won",
            "stage_outcome": "won",
            "stage_position": 4,
            "stage_is_last_open": False,
        }
        assert datetime.fromisoformat(won_row["closed_at"]) == DEMO_NOW

    async def test_every_default_stage_resolves_with_its_own_position(
        self, client, session, org_id
    ) -> None:
        for key in DEFAULT_READ:
            await make_deal(session, org_id, name=f"In {key}", stage=key)
        with as_principal(reader(org_id)):
            rows = {row["stage"]: row for row in await list_deals(client)}
        assert {
            key: (
                row["stage_label"],
                row["stage_outcome"],
                row["stage_position"],
                row["stage_is_last_open"],
            )
            for key, row in rows.items()
        } == DEFAULT_READ

    async def test_a_custom_pipelines_deal_reads_its_own_stage(
        self, client, session, admin_session, org_id
    ) -> None:
        await install_pipeline(admin_session, org_id)
        for stage in CUSTOM_STAGES:
            await make_deal(session, org_id, name=f"In {stage.key}", stage=stage.key)
        with as_principal(reader(org_id)):
            rows = {row["stage"]: row for row in await list_deals(client)}
        assert {key: tuple(row[k] for k in WIRE[1:]) for key, row in rows.items()} == {
            "discovery": ("Discovery", "open", 0, False),
            "scoping": ("Scoping", "open", 1, False),
            "quote": ("Quote", "open", 2, True),
            "won": ("Won", "won", 3, False),
            "lost": ("Lost", "lost", 4, False),
        }
        assert rows["won"]["closed_at"] is not None
        assert rows["won"]["probability"] == 100
        assert rows["quote"]["closed_at"] is None

    async def test_a_stage_archived_after_the_deal_entered_it_still_resolves(
        self, client, session, admin_session, org_id
    ) -> None:
        await install_pipeline(admin_session, org_id)
        await make_deal(session, org_id, name="In scoping", stage="scoping")
        async with admin_session.begin():
            await admin_session.execute(
                text(
                    "UPDATE deal.pipeline_stages SET archived_at = now() "
                    "WHERE organization_id = :org AND key = 'scoping'"
                ),
                {"org": org_id},
            )
        with as_principal(reader(org_id)):
            (row,) = await list_deals(client)
        assert tuple(row[k] for k in WIRE) == ("scoping", "Scoping", "open", 1, False)

    async def test_an_unresolvable_stored_key_renders_open_and_never_raises(
        self, client, admin_session, org_id
    ) -> None:
        await insert_deal(admin_session, org_id, stage="ghost")
        with as_principal(reader(org_id)):
            (row,) = await list_deals(client)
        assert tuple(row[k] for k in WIRE) == ("ghost", "ghost", "open", -1, False)

    async def test_the_detail_route_resolves_the_stage_like_the_list(
        self, client, session, admin_session, org_id
    ) -> None:
        await install_pipeline(admin_session, org_id)
        deal_id = await make_deal(session, org_id, name="Quoted", stage="quote")
        with as_principal(reader(org_id)):
            detail = (await client.get(f"/api/v1/deals/{deal_id}")).json()
            (listed,) = await list_deals(client)
        assert {k: detail[k] for k in (*WIRE, "closed_at")} == {
            k: listed[k] for k in (*WIRE, "closed_at")
        }


class TestOutcomeFilter:
    async def test_the_outcome_filter_selects_by_what_the_stage_means(
        self, client, org_id, data
    ) -> None:
        with as_principal(reader(org_id)):
            names = {
                outcome: sorted(
                    row["name"] for row in await list_deals(client, f"&outcome={outcome}")
                )
                for outcome in ("open", "won", "lost")
            }
        assert names == {
            "open": ["Fleet telematics rollout"],
            "won": ["Omnichannel CRM"],
            "lost": [],
        }

    async def test_the_outcome_filter_selects_by_outcome_not_by_key(
        self, client, session, admin_session, org_id
    ) -> None:
        stages = (
            *CUSTOM_STAGES[:4],
            StageSpec("signed_2025", "Signed 2025", 9, "won", 100, "success", archived=True),
            CUSTOM_STAGES[4],
        )
        await install_pipeline(admin_session, org_id, stages)
        await make_deal(session, org_id, name="Quoting", stage="quote")
        await make_deal(session, org_id, name="Won now", stage="won")
        await make_deal(session, org_id, name="Lost now", stage="lost")
        await insert_deal(
            admin_session, org_id, name="Won last year", stage="signed_2025", closed=True
        )
        await insert_deal(admin_session, org_id, name="Mystery", stage="ghost")
        with as_principal(reader(org_id)):
            by_outcome = {
                outcome: sorted(
                    row["name"] for row in await list_deals(client, f"&outcome={outcome}")
                )
                for outcome in ("open", "won", "lost")
            }
            by_key = sorted(row["name"] for row in await list_deals(client, "&stage=won"))
        assert by_outcome == {
            "open": ["Mystery", "Quoting"],  # an unresolvable key is open, as `is_open` says
            "won": ["Won last year", "Won now"],  # an archived won stage is still a won stage
            "lost": ["Lost now"],
        }
        assert by_key == ["Won now"], "`stage=` stays a key filter"

    async def test_an_unknown_outcome_is_422(self, client, org_id) -> None:
        with as_principal(reader(org_id)):
            response = await client.get("/api/v1/deals?outcome=maybe")
        assert response.status_code == 422

    async def test_a_stage_filter_naming_no_stage_returns_an_empty_page(
        self, client, org_id, data
    ) -> None:
        """`stage=` stays an unvalidated key: asking for one that does not exist is an empty page,
        as it always was, and not a 422."""
        with as_principal(reader(org_id)):
            response = await client.get("/api/v1/deals?stage=galaxy_brain")
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 0

    async def test_the_outcome_filter_cannot_widen_scope(self, client, org_id, data) -> None:
        own = reader(org_id, "OWN", data["subject_a"])
        with as_principal(own):
            mine = [row["name"] for row in await list_deals(client, "&outcome=open")]
            theirs = [row["name"] for row in await list_deals(client, "&outcome=won")]
        assert mine == ["Fleet telematics rollout"]
        assert theirs == [], "the won deal belongs to the other rep"
