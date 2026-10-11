"""`GET /deals/stages`: the pipeline a deal's `stage` key is read against.

Stage rows are tenant configuration, like the rep roster, so the bare list has **no scope floor**
by design: an OWN-scoped rep still needs every stage's label to read their own deals, and a
floor would deny that and protect nothing. What *is* deal-derived is `deal_count`, and that
follows the deal read's scope: only a caller whose `crm.pipeline.deal.read` grant is ORG gets it,
it counts live deals only, and for anyone else it is absent — not zero.

So the negative-authz set here is: no token (401), no deal-read permission (403), no `crm`
entitlement (402), the OWN reader who is allowed (200), another tenant's custom labels never
returned, and the counts withheld from OWN and TEAM readers and never taken from another tenant.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest

from app import services
from app.api.routes import router
from app.main import app
from app.models import Pipeline, PipelineStage
from app.pipeline import DEFAULT_PIPELINE, StageSpec
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for
from tests.pipeline_support import (
    CUSTOM_STAGES,
    OWNER,
    count_rows,
    insert_deal,
    install_pipeline,
)

pytestmark = pytest.mark.integration

PATH = "/api/v1/deals/stages"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def reader(org_id: uuid.UUID, scope: str = "ORG", **kwargs) -> Principal:
    return principal_for(org_id, [f"crm.pipeline.deal.read:{scope}"], subject_ref=OWNER, **kwargs)


def keys(body: dict) -> list[str]:
    return [stage["key"] for stage in body["stages"]]


class TestAuthorization:
    async def test_unauthenticated_is_401(self, client) -> None:
        response = await client.get(PATH)
        assert response.status_code == 401

    async def test_missing_the_deal_read_permission_is_403(self, client, org_id) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.activity.read:ORG", "crm.book.company.read:ORG"]
        )
        with as_principal(principal):
            response = await client.get(PATH)
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_crm_entitlement_is_402(self, client, org_id) -> None:
        with as_principal(reader(org_id, entitlements=[])):
            response = await client.get(PATH)
        assert response.status_code == 402

    async def test_an_own_scoped_reader_may_read_the_stage_list(self, client, org_id) -> None:
        """Explicit, because it is a decision: tenant configuration has no row floor."""
        with as_principal(reader(org_id, "OWN")):
            response = await client.get(PATH)
        assert response.status_code == 200
        assert keys(response.json()) == [s.key for s in DEFAULT_PIPELINE.stages]

    async def test_another_tenants_custom_label_is_never_returned(
        self, client, admin_session, org_id
    ) -> None:
        stranger = uuid.uuid4()
        secret = (
            StageSpec("discovery", "Their secret stage", 0, "open", 10, "info"),
            *CUSTOM_STAGES[3:],
        )
        await install_pipeline(admin_session, stranger, secret)
        with as_principal(reader(org_id)):
            mine = await client.get(PATH)
        assert mine.status_code == 200
        assert "Their secret stage" not in mine.text
        assert keys(mine.json()) == [s.key for s in DEFAULT_PIPELINE.stages]
        with as_principal(reader(stranger)):
            theirs = await client.get(PATH)
        assert [s["label"] for s in theirs.json()["stages"]] == [
            "Their secret stage",
            "Won",
            "Lost",
        ]


class TestShape:
    async def test_a_bare_organization_gets_the_virtual_default_unmaterialised(
        self, client, org_id
    ) -> None:
        with as_principal(reader(org_id)):
            body = (await client.get(PATH)).json()
        assert body["pipeline"] is None
        assert body["materialised"] is False
        assert [
            (
                s["id"],
                s["key"],
                s["label"],
                s["position"],
                s["outcome"],
                s["default_probability"],
                s["tone"],
                s["archived"],
            )
            for s in body["stages"]
        ] == [
            (None, s.key, s.label, s.position, s.outcome, s.default_probability, s.tone, False)
            for s in DEFAULT_PIPELINE.stages
        ]
        assert all("deal_count" not in s for s in body["stages"])

    async def test_a_materialised_organization_gets_its_rows(self, client, session, org_id) -> None:
        async with session.begin():
            spec = await services.materialise_default_pipeline(session, org_id, None)
        with as_principal(reader(org_id)):
            body = (await client.get(PATH)).json()
        assert body["materialised"] is True
        assert body["pipeline"] == {"id": str(spec.id), "key": "sales", "name": "Sales pipeline"}
        assert [s["id"] for s in body["stages"]] == [str(s.id) for s in spec.stages]
        assert keys(body) == [s.key for s in DEFAULT_PIPELINE.stages]

    async def test_a_custom_pipeline_comes_back_in_position_order(
        self, client, admin_session, org_id
    ) -> None:
        await install_pipeline(admin_session, org_id)
        with as_principal(reader(org_id)):
            body = (await client.get(PATH)).json()
        assert keys(body) == [s.key for s in CUSTOM_STAGES]
        assert [s["outcome"] for s in body["stages"]] == ["open", "open", "open", "won", "lost"]

    async def test_include_archived_lists_archived_stages_at_any_scope(
        self, client, admin_session, org_id
    ) -> None:
        stages = (
            CUSTOM_STAGES[0],
            StageSpec("scoping", "Scoping", 1, "open", 30, "info", archived=True),
            *CUSTOM_STAGES[2:],
        )
        await install_pipeline(admin_session, org_id, stages)
        with as_principal(reader(org_id, "OWN")):
            active = (await client.get(PATH)).json()
            everything = (await client.get(f"{PATH}?include_archived=true")).json()
        assert keys(active) == ["discovery", "quote", "won", "lost"]
        assert keys(everything) == ["discovery", "scoping", "quote", "won", "lost"]
        scoping = next(s for s in everything["stages"] if s["key"] == "scoping")
        assert scoping["archived"] is True

    async def test_a_get_never_materialises(self, client, session, org_id) -> None:
        with as_principal(reader(org_id)):
            first = await client.get(PATH)
            second = await client.get(f"{PATH}?include=counts&include_archived=true")
        assert first.status_code == second.status_code == 200
        assert await count_rows(session, Pipeline) == 0
        assert await count_rows(session, PipelineStage) == 0

    async def test_the_route_is_declared_before_the_detail_route(self, client, org_id) -> None:
        """Starlette matches in declaration order: after `/deals/{deal_id}`, `stages` would be
        parsed as a deal id and answer 422."""
        declared = [route.path for route in router.routes]
        assert declared.index("/deals/stages") < declared.index("/deals/{deal_id}")
        with as_principal(reader(org_id)):
            assert (await client.get(PATH)).status_code == 200


class TestCounts:
    async def test_an_org_scoped_reader_gets_live_deal_counts_per_stage(
        self, client, org_id, data
    ) -> None:
        with as_principal(reader(org_id)):
            body = (await client.get(f"{PATH}?include=counts")).json()
        assert {s["key"]: s["deal_count"] for s in body["stages"]} == {
            "lead": 0,
            "qualified": 0,
            "proposal": 0,
            "negotiation": 1,
            "closed_won": 1,
            "closed_lost": 0,
        }

    async def test_counts_never_include_a_soft_deleted_deal(
        self, client, admin_session, org_id
    ) -> None:
        await insert_deal(admin_session, org_id, stage="lead")
        await insert_deal(admin_session, org_id, stage="lead")
        await insert_deal(admin_session, org_id, stage="lead", deleted=True)
        with as_principal(reader(org_id)):
            body = (await client.get(f"{PATH}?include=counts")).json()
        assert {s["key"]: s["deal_count"] for s in body["stages"]}["lead"] == 2

    @pytest.mark.parametrize("scope", ["OWN", "TEAM"])
    async def test_an_own_or_team_scoped_reader_asking_for_counts_gets_no_deal_count(
        self, client, org_id, data, scope: str
    ) -> None:
        """The negative-authz case for the counts: the request succeeds, no stage carries
        `deal_count`, and the response is byte for byte the one without `include=counts`, so it
        gives no way to infer one."""
        with as_principal(reader(org_id, scope)):
            counted = await client.get(f"{PATH}?include=counts")
            plain = await client.get(PATH)
        assert counted.status_code == 200
        assert all("deal_count" not in s for s in counted.json()["stages"])
        assert counted.json() == plain.json()

    async def test_counts_never_cross_tenants(self, client, admin_session, org_id, data) -> None:
        stranger = uuid.uuid4()
        await insert_deal(admin_session, stranger, stage="negotiation")
        await insert_deal(admin_session, stranger, stage="negotiation")
        await insert_deal(admin_session, stranger, stage="negotiation", deleted=True)
        with as_principal(reader(org_id)):
            body = (await client.get(f"{PATH}?include=counts")).json()
        assert {s["key"]: s["deal_count"] for s in body["stages"]}["negotiation"] == 1

    async def test_an_unknown_include_value_is_422(self, client, org_id) -> None:
        with as_principal(reader(org_id)):
            response = await client.get(f"{PATH}?include=everything")
        assert response.status_code == 422
