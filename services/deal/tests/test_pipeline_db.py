"""The pipeline tables against a real PostgreSQL: row-level security, the constraints that make a
stage's identity safe, the grants, and what `materialise_default_pipeline` and
`load_pipeline_spec` do with them.

The constraint tests go through the **admin** session (BYPASSRLS, the migration role), so the
database itself is what refuses a bad row — RLS would otherwise get there first for some of them
and prove less. The tenant tests go through the **app** role, which is what the service uses.
"""

from __future__ import annotations

import ast
import asyncio
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.models import AuditLog, OutboxEvent, Pipeline, PipelineStage
from app.pipeline import DEFAULT_PIPELINE, StageSpec
from tests.pipeline_support import (
    CUSTOM_STAGES,
    insert_pipeline,
    insert_stage,
    install_pipeline,
    tenant_session,
)

pytestmark = pytest.mark.integration

DEAL_APP = Path(__file__).resolve().parents[1] / "app"


class TestSchema:
    async def test_rls_is_enabled_and_forced_on_both_tables(self, admin_session) -> None:
        async with admin_session.begin():
            rows = (
                await admin_session.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'deal' "
                        "AND c.relname IN ('pipelines', 'pipeline_stages') "
                        "ORDER BY c.relname"
                    )
                )
            ).all()
        assert [tuple(row) for row in rows] == [
            ("pipeline_stages", True, True),
            ("pipelines", True, True),
        ]

    async def test_each_table_has_the_tenant_isolation_policy_with_a_check(
        self, admin_session
    ) -> None:
        async with admin_session.begin():
            rows = (
                await admin_session.execute(
                    text(
                        "SELECT tablename, policyname, roles, cmd, qual, with_check "
                        "FROM pg_policies WHERE schemaname = 'deal' "
                        "AND tablename IN ('pipelines', 'pipeline_stages') ORDER BY tablename"
                    )
                )
            ).all()
        assert [(r.tablename, r.policyname, list(r.roles), r.cmd) for r in rows] == [
            ("pipeline_stages", "tenant_isolation", [settings().db_app_role], "ALL"),
            ("pipelines", "tenant_isolation", [settings().db_app_role], "ALL"),
        ]
        for row in rows:
            # USING gates existing rows; only WITH CHECK stops a tenant planting a row elsewhere.
            assert "current_org" in row.qual
            assert row.with_check is not None
            assert "current_org" in row.with_check

    @pytest.mark.parametrize("table", ["pipelines", "pipeline_stages"])
    async def test_each_table_has_an_organization_index(self, admin_session, table: str) -> None:
        """The policy ANDs `organization_id = current_org()` into every query, so the tenant
        column needs an index of its own, or each read of the table scans all of it."""
        async with admin_session.begin():
            result = await admin_session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'deal' AND tablename = :table"
                ),
                {"table": table},
            )
            definitions = list(result.scalars())
        assert any(d.endswith("USING btree (organization_id)") for d in definitions), definitions

    async def test_a_session_bound_to_another_organization_sees_no_pipeline_and_no_stage(
        self, session, admin_session, org_id
    ) -> None:
        other = uuid.uuid4()
        pipeline = await insert_pipeline(admin_session, other)
        await insert_stage(admin_session, other, pipeline, "alpha")

        async with session.begin():  # bound to org_id, not to `other`
            seen = (
                await session.execute(
                    text(
                        "SELECT (SELECT count(*) FROM deal.pipelines), "
                        "(SELECT count(*) FROM deal.pipeline_stages)"
                    )
                )
            ).one()
        assert tuple(seen) == (0, 0)

        async with tenant_session(other) as theirs, theirs.begin():
            own = (
                await theirs.execute(
                    text(
                        "SELECT (SELECT count(*) FROM deal.pipelines), "
                        "(SELECT count(*) FROM deal.pipeline_stages)"
                    )
                )
            ).one()
        assert tuple(own) == (1, 1), "the owning organization must see its own rows"

    async def test_a_session_bound_to_another_organization_cannot_insert_into_this_ones(
        self, session, org_id
    ) -> None:
        """`WITH CHECK`: USING gates existing rows and is not applied to the new row of an
        INSERT, so without it a tenant could plant a pipeline in somebody else's organization."""
        other = uuid.uuid4()
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin():  # bound to org_id
                await session.execute(
                    text(
                        "INSERT INTO deal.pipelines (id, organization_id, key, name) "
                        "VALUES (:id, :org, 'sales', 'Sales pipeline')"
                    ),
                    {"id": uuid.uuid4(), "org": other},
                )

    async def test_a_stage_cannot_reference_another_organizations_pipeline(
        self, admin_session
    ) -> None:
        """The admin role bypasses RLS, so this is the composite foreign key refusing, not a
        policy: referential-integrity checks bypass RLS, and a single-column key would have let
        a tenant point a stage at somebody else's pipeline."""
        org_a, org_b = uuid.uuid4(), uuid.uuid4()
        pipeline_a = await insert_pipeline(admin_session, org_a)
        with pytest.raises(IntegrityError, match="fk_pipeline_stages_pipeline"):
            await insert_stage(admin_session, org_b, pipeline_a, "alpha")

    async def test_one_default_pipeline_per_organization(self, admin_session) -> None:
        org = uuid.uuid4()
        await insert_pipeline(admin_session, org)
        with pytest.raises(IntegrityError, match="uq_pipelines_org_default"):
            await insert_pipeline(admin_session, org, key="second")
        # Room for more later: a non-default pipeline is allowed beside it.
        await insert_pipeline(admin_session, org, key="second", is_default=False)

    async def test_a_soft_deleted_default_pipeline_does_not_hold_the_default_slot(
        self, admin_session
    ) -> None:
        org = uuid.uuid4()
        await insert_pipeline(admin_session, org, deleted=True)
        await insert_pipeline(admin_session, org, key="replacement")

    async def test_a_stage_key_is_never_reusable_even_after_archive(self, admin_session) -> None:
        org = uuid.uuid4()
        pipeline = await insert_pipeline(admin_session, org)
        await insert_stage(admin_session, org, pipeline, "alpha", label="Alpha", archived=True)
        with pytest.raises(IntegrityError, match="uq_pipeline_stages_org_pipeline_key"):
            await insert_stage(admin_session, org, pipeline, "alpha", label="Alpha again")

    async def test_active_labels_are_unique_case_insensitively(self, admin_session) -> None:
        org = uuid.uuid4()
        pipeline = await insert_pipeline(admin_session, org)
        await insert_stage(admin_session, org, pipeline, "alpha", label="Discovery")
        with pytest.raises(IntegrityError, match="uq_pipeline_stages_org_pipeline_label_active"):
            await insert_stage(admin_session, org, pipeline, "beta", label="DISCOVERY")

    async def test_an_archived_stage_may_share_an_active_label(self, admin_session) -> None:
        org = uuid.uuid4()
        pipeline = await insert_pipeline(admin_session, org)
        await insert_stage(admin_session, org, pipeline, "alpha", label="Discovery", archived=True)
        await insert_stage(admin_session, org, pipeline, "beta", label="Discovery")
        await insert_stage(admin_session, org, pipeline, "gamma", label="Other", archived=True)
        await insert_stage(admin_session, org, pipeline, "delta", label="Other")

    async def test_key_pipeline_organization_and_outcome_are_frozen_by_the_trigger(
        self, admin_session
    ) -> None:
        org = uuid.uuid4()
        pipeline = await insert_pipeline(admin_session, org)
        second = await insert_pipeline(admin_session, org, key="second", is_default=False)
        stage = await insert_stage(admin_session, org, pipeline, "alpha")
        refused = [
            ("SET key = 'renamed'", {}),
            ("SET pipeline_id = :other", {"other": second}),
            ("SET organization_id = :stranger", {"stranger": uuid.uuid4()}),
            ("SET outcome = 'won', default_probability = 100", {}),
        ]
        for assignment, params in refused:
            with pytest.raises(DBAPIError, match="immutable"):
                async with admin_session.begin():
                    await admin_session.execute(
                        text(f"UPDATE deal.pipeline_stages {assignment} WHERE id = :id"),
                        {"id": stage, **params},
                    )
        # What a stage-configuration write does change is not frozen.
        async with admin_session.begin():
            await admin_session.execute(
                text(
                    "UPDATE deal.pipeline_stages SET label = 'Renamed', tone = 'warning', "
                    "default_probability = 25, position = 3, archived_at = now() WHERE id = :id"
                ),
                {"id": stage},
            )
            label = (
                await admin_session.execute(
                    text("SELECT label FROM deal.pipeline_stages WHERE id = :id"), {"id": stage}
                )
            ).scalar_one()
        assert label == "Renamed"

    @pytest.mark.parametrize(
        ("fields", "constraint"),
        [
            ({"outcome": "won", "default_probability": 90}, "ck_pipeline_stages_closing_pin"),
            ({"outcome": "lost", "default_probability": 5}, "ck_pipeline_stages_closing_pin"),
            ({"outcome": "pending"}, "ck_pipeline_stages_(outcome|closing_pin)"),
            ({"tone": "purple"}, "ck_pipeline_stages_tone"),
            ({"default_probability": 101}, "ck_pipeline_stages_probability"),
            ({"position": -1}, "ck_pipeline_stages_position"),
        ],
    )
    async def test_the_row_local_rules_are_database_constraints(
        self, admin_session, fields: dict, constraint: str
    ) -> None:
        org = uuid.uuid4()
        pipeline = await insert_pipeline(admin_session, org)
        with pytest.raises(IntegrityError, match=constraint):
            await insert_stage(admin_session, org, pipeline, "alpha", **fields)

    @pytest.mark.parametrize("key", ["Has Caps", "with-dash", "", "has space"])
    async def test_a_key_outside_the_pattern_is_refused(self, admin_session, key: str) -> None:
        org = uuid.uuid4()
        pipeline = await insert_pipeline(admin_session, org)
        with pytest.raises(IntegrityError, match="ck_pipeline_stages_key"):
            await insert_stage(admin_session, org, pipeline, key, label="Whatever")

    async def test_the_app_role_holds_dml_and_the_relay_role_holds_nothing_on_the_new_tables(
        self, admin_session
    ) -> None:
        """The relay runs as a BYPASSRLS role that may read and update `outbox` and nothing else
        (the project rules), and no app role holds TRUNCATE, which RLS does not filter."""
        cfg = settings()
        privileges = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")

        async def held(role: str, table: str) -> dict[str, bool]:
            async with admin_session.begin():
                exists = (
                    await admin_session.execute(
                        text("SELECT count(*) FROM pg_roles WHERE rolname = :r"), {"r": role}
                    )
                ).scalar_one()
                assert exists == 1, f"role {role} is missing: run `make reconcile-roles`"
                return {
                    privilege: (
                        await admin_session.execute(
                            text(
                                "SELECT has_table_privilege("
                                "CAST(:r AS name), CAST(:t AS text), CAST(:p AS text))"
                            ),
                            {"r": role, "t": f"deal.{table}", "p": privilege},
                        )
                    ).scalar_one()
                    for privilege in privileges
                }

        for table in ("pipelines", "pipeline_stages"):
            assert await held(cfg.db_app_role, table) == {
                "SELECT": True,
                "INSERT": True,
                "UPDATE": True,
                "DELETE": True,
                "TRUNCATE": False,
            }
            assert await held(cfg.db_outbox_role, table) == dict.fromkeys(privileges, False)


def _delete_offences(source: str) -> list[str]:
    """Where `source` deletes a pipeline stage: `delete(PipelineStage)`, a `.delete(<something
    named for a stage>)`, or SQL that deletes from `pipeline_stages`."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func_name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
            argument = node.args[0] if node.args else None
            names_a_stage = isinstance(argument, ast.Name) and (
                argument.id == "PipelineStage" or "stage" in argument.id.lower()
            )
            if func_name == "delete" and names_a_stage:
                found.append(f"line {node.lineno}: delete(<a stage>)")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.search(r"delete\s+from\s+\S*pipeline_stages", node.value, re.IGNORECASE):
                found.append(f"line {node.lineno}: SQL that deletes from pipeline_stages")
    return found


class TestNoStageIsEverDeleted:
    """A stage is archived, never deleted: its key is a published identifier. There is no delete
    trigger to enforce it (that would enlarge the purge tool's trigger set for a path no code
    takes), so this static test is the enforcement."""

    def test_no_code_path_deletes_a_stage(self) -> None:
        offences = {
            path.relative_to(DEAL_APP).as_posix(): found
            for path in sorted(DEAL_APP.rglob("*.py"))
            if (found := _delete_offences(path.read_text(encoding="utf-8")))
        }
        assert offences == {}

    @pytest.mark.parametrize(
        "snippet",
        [
            "await session.execute(delete(PipelineStage))",
            "await session.delete(stage)",
            "text('DELETE FROM deal.pipeline_stages WHERE id = :id')",
        ],
    )
    def test_the_scanner_finds_each_way_of_deleting_one(self, snippet: str) -> None:
        assert _delete_offences(snippet)

    def test_the_scanner_ignores_other_deletes(self) -> None:
        assert not _delete_offences("await session.delete(deal)\ntext('DELETE FROM deal.deals')")


class TestMaterialise:
    async def test_it_writes_the_six_rows_one_audit_row_and_no_outbox_event(
        self, session, org_id
    ) -> None:
        actor = uuid.uuid4()
        async with session.begin():
            spec = await services.materialise_default_pipeline(session, org_id, actor)
        assert spec.materialised is True
        assert spec.id is not None
        assert [s.key for s in spec.stages] == [s.key for s in DEFAULT_PIPELINE.stages]
        assert all(s.id is not None for s in spec.stages)

        async with session.begin():
            audits = (
                (await session.execute(select(AuditLog).where(AuditLog.entity == "pipeline")))
                .scalars()
                .all()
            )
            outbox = (
                await session.execute(select(func.count()).select_from(OutboxEvent))
            ).scalar_one()
            created_by = (
                (await session.execute(select(PipelineStage.created_by).distinct())).scalars().all()
            )
        assert [(a.action, a.entity_id, a.actor_id) for a in audits] == [
            ("pipeline.materialised", spec.id, actor)
        ]
        assert audits[0].after == {"pipeline_id": str(spec.id), "stages": 6}
        assert outbox == 0, "materialising changes nothing a consumer needs to hear about"
        assert created_by == [str(actor)]

    async def test_a_second_call_changes_nothing(self, session, org_id) -> None:
        async with session.begin():
            first = await services.materialise_default_pipeline(session, org_id, None)
        async with session.begin():
            second = await services.materialise_default_pipeline(session, org_id, None)
        assert second == first
        async with session.begin():
            counts = (
                await session.execute(
                    select(
                        select(func.count()).select_from(Pipeline).scalar_subquery(),
                        select(func.count()).select_from(PipelineStage).scalar_subquery(),
                        select(func.count())
                        .select_from(AuditLog)
                        .where(AuditLog.action == "pipeline.materialised")
                        .scalar_subquery(),
                    )
                )
            ).one()
        assert tuple(counts) == (1, 6, 1)

    async def test_two_concurrent_first_writes_make_one_pipeline(self, session, org_id) -> None:
        async def first_write(db: AsyncSession) -> None:
            async with db.begin():
                await services.materialise_default_pipeline(db, org_id, None)

        async with tenant_session(org_id) as other:
            await asyncio.gather(first_write(session), first_write(other))

        async with session.begin():
            counts = (
                await session.execute(
                    select(
                        select(func.count()).select_from(Pipeline).scalar_subquery(),
                        select(func.count()).select_from(PipelineStage).scalar_subquery(),
                    )
                )
            ).one()
        assert tuple(counts) == (1, 6), "the second writer must re-read under the lock and stop"

    async def test_the_materialised_spec_equals_the_virtual_default(self, session, org_id) -> None:
        async with session.begin():
            spec = await services.materialise_default_pipeline(session, org_id, None)

        def shape(stages: tuple[StageSpec, ...]) -> list[tuple[object, ...]]:
            return [
                (s.key, s.label, s.position, s.outcome, s.default_probability, s.tone, s.archived)
                for s in stages
            ]

        assert shape(spec.stages) == shape(DEFAULT_PIPELINE.stages)
        assert (spec.key, spec.name) == (DEFAULT_PIPELINE.key, DEFAULT_PIPELINE.name)

    async def test_organization_b_stays_virtual_while_a_is_materialised(
        self, session, org_id
    ) -> None:
        stranger = uuid.uuid4()
        async with session.begin():
            await services.materialise_default_pipeline(session, org_id, None)
        async with tenant_session(stranger) as theirs, theirs.begin():
            spec = await selectors.load_pipeline_spec(theirs, stranger)
        assert spec is DEFAULT_PIPELINE


class TestLoad:
    async def test_a_bare_organization_reads_the_virtual_default(self, session, org_id) -> None:
        async with session.begin():
            spec = await selectors.load_pipeline_spec(session, org_id)
        assert spec is DEFAULT_PIPELINE
        assert spec.materialised is False

    async def test_a_soft_deleted_stage_is_not_loaded(self, session, admin_session, org_id) -> None:
        await install_pipeline(admin_session, org_id)
        async with admin_session.begin():
            await admin_session.execute(
                text(
                    "UPDATE deal.pipeline_stages SET deleted_at = now() "
                    "WHERE organization_id = :org AND key = 'scoping'"
                ),
                {"org": org_id},
            )
        async with session.begin():
            spec = await selectors.load_pipeline_spec(session, org_id)
        assert [s.key for s in spec.stages] == ["discovery", "quote", "won", "lost"]

    async def test_an_archived_stage_is_loaded_and_marked(
        self, session, admin_session, org_id
    ) -> None:
        stages = (
            *CUSTOM_STAGES,
            StageSpec("retired", "Retired", 9, "open", 5, "neutral", archived=True),
        )
        await install_pipeline(admin_session, org_id, stages)
        async with session.begin():
            spec = await selectors.load_pipeline_spec(session, org_id)
        retired = spec.get("retired")
        assert retired is not None
        assert retired.archived is True
        assert [s.key for s in spec.active()] == ["discovery", "scoping", "quote", "won", "lost"]

    async def test_one_organizations_pipeline_never_reaches_another(
        self, session, admin_session, org_id
    ) -> None:
        await install_pipeline(admin_session, org_id)
        stranger = uuid.uuid4()
        async with tenant_session(stranger) as theirs, theirs.begin():
            their_spec = await selectors.load_pipeline_spec(theirs, stranger)
        async with session.begin():
            our_spec = await selectors.load_pipeline_spec(session, org_id)
        assert their_spec is DEFAULT_PIPELINE
        assert [s.key for s in our_spec.stages] == [s.key for s in CUSTOM_STAGES]
        assert our_spec.materialised is True

    async def test_the_loaded_spec_carries_ids_and_the_pipeline_identity(
        self, session, admin_session, org_id
    ) -> None:
        installed = await install_pipeline(admin_session, org_id, name="Enterprise pipeline")
        async with session.begin():
            spec = await selectors.load_pipeline_spec(session, org_id)
        assert spec.id == installed.pipeline_id
        assert spec.name == "Enterprise pipeline"
        assert {s.key: s.id for s in spec.stages} == installed.stage_ids


class TestLocks:
    async def test_a_shared_lock_waits_for_an_open_exclusive_lock(self, session, org_id) -> None:
        async with tenant_session(org_id) as other:
            async with session.begin():
                await services.lock_pipeline_exclusive(session, org_id)
                with pytest.raises(DBAPIError, match="lock timeout"):
                    async with other.begin():
                        await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
                        await services.lock_pipeline_shared(other, org_id)
            # The exclusive holder committed, so the shared lock is free to take.
            async with other.begin():
                await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
                await services.lock_pipeline_shared(other, org_id)

    async def test_an_exclusive_lock_waits_for_an_open_shared_lock(self, session, org_id) -> None:
        async with tenant_session(org_id) as other, session.begin():
            await services.lock_pipeline_shared(session, org_id)
            with pytest.raises(DBAPIError, match="lock timeout"):
                async with other.begin():
                    await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
                    await services.lock_pipeline_exclusive(other, org_id)

    async def test_two_shared_locks_do_not_block_each_other(self, session, org_id) -> None:
        async with tenant_session(org_id) as other, session.begin():
            await services.lock_pipeline_shared(session, org_id)
            async with other.begin():
                await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
                await services.lock_pipeline_shared(other, org_id)

    async def test_another_organizations_lock_is_independent(self, session, org_id) -> None:
        stranger = uuid.uuid4()
        async with tenant_session(stranger) as other, session.begin():
            await services.lock_pipeline_exclusive(session, org_id)
            async with other.begin():
                await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
                await services.lock_pipeline_exclusive(other, stranger)
