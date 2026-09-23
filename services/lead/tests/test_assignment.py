"""Round-robin assignment, end to end against the real write path — `test_domain.py` already
covers `domain.next_owner` in isolation; these prove `services.create_lead` and
`services.set_assignment_rule` wire it up correctly, including the cursor surviving across
separate transactions the way separate HTTP requests would see it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import clients, selectors, services
from app.config import settings
from app.models import SCHEMA
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import ForbiddenProblem, UpstreamUnavailableProblem, ValidationProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


async def _create(session, ctx, name: str, *, owner_id=None):
    return await services.create_lead(
        session,
        ctx,
        full_name=name,
        company=None,
        email=None,
        phone=None,
        source="form",
        owner_id=owner_id,
        notes="",
    )


class TestAssignmentRule:
    async def test_configuring_requires_org_scope(self, session, org_id) -> None:
        own = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN, subject_ref=uuid.uuid4())
        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.set_assignment_rule(
                    session, own, mode="round_robin", owner_ids=[uuid.uuid4()]
                )

    async def test_a_lead_created_with_no_rule_configured_gets_no_owner(
        self, session, org_id
    ) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await _create(session, write, "Unassigned")
        assert lead.owner_id is None

    async def test_leads_rotate_through_the_configured_owners_in_order(
        self, session, org_id
    ) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        owners = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        async with session.begin():
            await services.set_assignment_rule(session, write, mode="round_robin", owner_ids=owners)

        assigned = []
        for i in range(5):
            async with session.begin():
                lead = await _create(session, write, f"Lead {i}")
            assigned.append(lead.owner_id)

        assert assigned == [owners[0], owners[1], owners[2], owners[0], owners[1]]

    async def test_an_explicit_owner_bypasses_the_rotation(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        owners = [uuid.uuid4(), uuid.uuid4()]
        explicit = uuid.uuid4()
        async with session.begin():
            await services.set_assignment_rule(session, write, mode="round_robin", owner_ids=owners)
            lead = await _create(session, write, "Named owner", owner_id=explicit)
        assert lead.owner_id == explicit

        # The rotation did not advance for the explicitly-owned lead.
        rule = await selectors.get_assignment_rule(session, org_id)
        assert rule is not None
        assert rule.cursor == 0

    async def test_manual_mode_assigns_nobody(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            await services.set_assignment_rule(
                session, write, mode="manual", owner_ids=[uuid.uuid4()]
            )
            lead = await _create(session, write, "Manual mode")
        assert lead.owner_id is None

    async def test_reconfiguring_resets_the_cursor(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        owners = [uuid.uuid4(), uuid.uuid4()]
        async with session.begin():
            await services.set_assignment_rule(session, write, mode="round_robin", owner_ids=owners)
            await _create(session, write, "First")  # advances cursor to 1

        async with session.begin():
            rule = await services.set_assignment_rule(
                session, write, mode="round_robin", owner_ids=[uuid.uuid4(), uuid.uuid4()]
            )
        assert rule.cursor == 0

    async def test_an_inactive_or_unknown_owner_refuses_the_rule(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad_owner = uuid.uuid4()

        async def _one_invalid(cfg, organization_id, owner_ids):  # type: ignore[no-untyped-def]
            return [bad_owner]

        monkeypatch.setattr(clients, "invalid_owner_ids", _one_invalid)

        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        with pytest.raises(ValidationProblem):
            async with session.begin():
                await services.set_assignment_rule(
                    session, write, mode="round_robin", owner_ids=[bad_owner]
                )

    async def test_the_owner_directory_being_unreachable_is_upstream_unavailable(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _unreachable(cfg, organization_id, owner_ids):  # type: ignore[no-untyped-def]
            raise clients.OwnersValidateUnavailable("down")

        monkeypatch.setattr(clients, "invalid_owner_ids", _unreachable)

        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        with pytest.raises(UpstreamUnavailableProblem):
            async with session.begin():
                await services.set_assignment_rule(
                    session, write, mode="round_robin", owner_ids=[uuid.uuid4()]
                )


class TestRoundRobinSkipsInactiveOwners:
    """m4: a rep configured as active into the rotation, then deactivated later, must not
    keep collecting leads forever — nothing revalidated the saved list again until this."""

    async def test_an_owner_deactivated_after_configuration_is_skipped(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        good_a, gone_bad, good_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await services.set_assignment_rule(
                session, write, mode="round_robin", owner_ids=[good_a, gone_bad, good_b]
            )

        async def _one_now_inactive(cfg, organization_id, owner_ids):  # type: ignore[no-untyped-def]
            return [gone_bad]

        monkeypatch.setattr(clients, "invalid_owner_ids", _one_now_inactive)

        assigned = []
        for i in range(4):
            async with session.begin():
                lead = await _create(session, write, f"Lead {i}")
            assigned.append(lead.owner_id)

        assert gone_bad not in assigned
        assert set(assigned) == {good_a, good_b}

    async def test_every_owner_inactive_leaves_the_lead_unassigned(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        owners = [uuid.uuid4(), uuid.uuid4()]
        async with session.begin():
            await services.set_assignment_rule(session, write, mode="round_robin", owner_ids=owners)

        async def _all_invalid(cfg, organization_id, owner_ids):  # type: ignore[no-untyped-def]
            return list(owner_ids)

        monkeypatch.setattr(clients, "invalid_owner_ids", _all_invalid)

        async with session.begin():
            lead = await _create(session, write, "Nobody left")
        assert lead.owner_id is None

    async def test_the_directory_is_not_asked_again_within_the_cache_window(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        owners = [uuid.uuid4(), uuid.uuid4()]
        async with session.begin():
            await services.set_assignment_rule(session, write, mode="round_robin", owner_ids=owners)

        calls = 0

        async def _counted(cfg, organization_id, owner_ids):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            return []

        monkeypatch.setattr(clients, "invalid_owner_ids", _counted)

        for i in range(5):
            async with session.begin():
                await _create(session, write, f"Lead {i}")

        assert calls == 1


class TestRoundRobinConcurrency:
    """Two real sessions creating a lead against the same rotation at once — the row lock
    `_get_or_create_rule`/`get_assignment_rule_for_update` takes must serialize them, or two
    concurrent creates both read cursor `0`, both pick `owner_ids[0]`, and both advance the
    cursor to `1` — the same rep assigned twice with the rotation never having moved."""

    def _connect_args(self) -> dict:
        return {"server_settings": {"search_path": f"{SCHEMA},public"}}

    async def test_two_concurrent_creates_each_get_a_different_owner(
        self, org_id, session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _none_invalid(cfg, organization_id, owner_ids):  # type: ignore[no-untyped-def]
            return []

        monkeypatch.setattr(clients, "invalid_owner_ids", _none_invalid)

        owners = [uuid.uuid4(), uuid.uuid4()]
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            await services.set_assignment_rule(session, write, mode="round_robin", owner_ids=owners)

        cfg = settings()
        engine_a = create_async_engine(cfg.app_dsn, connect_args=self._connect_args())
        engine_b = create_async_engine(cfg.app_dsn, connect_args=self._connect_args())
        maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
        maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

        def _bind(org: uuid.UUID):
            def _listener(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

            return _listener

        try:
            async with maker_a() as session_a, maker_b() as session_b:
                sa_event.listen(session_a.sync_session, "after_begin", _bind(org_id))
                sa_event.listen(session_b.sync_session, "after_begin", _bind(org_id))

                async def _create_in(sess, name: str):
                    async with sess.begin():
                        return await _create(sess, write, name)

                lead_a, lead_b = await asyncio.gather(
                    _create_in(session_a, "Concurrent A"),
                    _create_in(session_b, "Concurrent B"),
                )
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

        assert {lead_a.owner_id, lead_b.owner_id} == set(owners)

        async with session.begin():
            rule = await selectors.get_assignment_rule(session, org_id)
        assert rule is not None
        assert rule.cursor == 2
