"""`tools/verify_membership_links.py` (R21): report where the two halves of the employee link
disagree, and change nothing unless `--apply` — which is audited and refused in CI.

The properties that earn the tool its place are the negative ones: the default leaves the database
exactly as it found it *even when it finds something*; `--apply` cannot run where `CI` is set, under
either variable name a pipeline uses; a clean run is not mistaken for a ready-to-drop one unless it
covered the whole fleet and wrote nothing; and a finding of any of the four kinds moves the exit
status a scheduler alerts on. Everything runs the real orchestration against real PostgreSQL under
the NOBYPASSRLS app role — only employee-service's page is stubbed.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.models import AuditLog, Membership, Organization
from app.schemas import MemberPatch
from platform_core.auth import Scope
from platform_core.cache import permission_version_key
from tests.helpers import FakeRedis, ctx_for

pytestmark = pytest.mark.integration

_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
import reconcile_membership_links as reconcile  # type: ignore[import-not-found]  # noqa: E402
import verify_membership_links as verify_tool  # type: ignore[import-not-found]  # noqa: E402

MEMBER_WRITE = "core.identity.membership.write"


class _Source:
    """Employee-service's `GET /internal/v1/employee-links`, one page: every employee with its
    `membership_id`, null or not."""

    def __init__(self, rows: list[tuple[uuid.UUID, uuid.UUID | None]]) -> None:
        self.rows = rows

    async def fetch_page(
        self, organization_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int = 500
    ) -> object:
        return reconcile.LinkPage(
            items=[reconcile.LinkRow(employee_id=e, membership_id=m) for e, m in self.rows],
            next_cursor=None,
        )


async def _employee_id_of(session: AsyncSession, membership_id: uuid.UUID) -> uuid.UUID | None:
    async with session.begin():
        return (
            await session.execute(
                select(Membership.employee_id).where(Membership.id == membership_id)
            )
        ).scalar_one()


async def _link(
    session: AsyncSession,
    org: Organization,
    actor: Membership,
    target: Membership,
    employee_id: uuid.UUID | None,
) -> None:
    ctx = ctx_for(org.id, actor, MEMBER_WRITE, Scope.ORG)
    async with session.begin():
        await services.update_member(session, ctx, target, MemberPatch(employee_id=employee_id))


async def _audit_rows(session: AsyncSession, action: str) -> list[AuditLog]:
    async with session.begin():
        return list(
            (await session.execute(select(AuditLog).where(AuditLog.action == action)))
            .scalars()
            .all()
        )


async def _run(org: Organization, source: _Source, *, apply: bool = False, fleet: bool = True):
    return await verify_tool.verify(
        [org.id], source, apply=apply, fleet=fleet, pace_ms=0, page_pace_ms=0
    )


# --------------------------------------------------------------------- read-only by default


class TestTheDefaultChangesNothing:
    async def test_a_fill_candidate_is_reported_and_left_unwritten(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Employee says E↔M and account has not linked M. That is a mismatch worth reporting, and
        the report must not repair it."""
        employee_id = uuid.uuid4()
        source = _Source([(employee_id, people["report"].id)])

        results, summary = await _run(org, source)

        assert results[0]["dry_run"] is True
        assert results[0]["links_written"] == 1  # what --apply *would* write
        assert summary["fill_candidates_total"] == 1
        assert "links_written_total" not in summary
        assert summary["read_only"] is True and summary["mode"] == "verify"
        assert await _employee_id_of(session, people["report"].id) is None
        assert await _audit_rows(session, "membership.links_reconciled") == []

    async def test_it_leaves_no_trace_even_when_it_finds_a_conflict(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        held = uuid.uuid4()
        await _link(session, org, people["manager"], people["report"], held)
        source = _Source([(held, people["outsider"].id)])

        results, summary = await _run(org, source)

        assert results[0]["conflicts_employee_taken"] == 1
        assert summary["clean"] is False
        assert await _employee_id_of(session, people["outsider"].id) is None
        assert await _employee_id_of(session, people["report"].id) == held

    async def test_it_never_bumps_the_permission_version(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        redis: FakeRedis,
    ) -> None:
        await _run(org, _Source([(uuid.uuid4(), people["report"].id)]))
        assert permission_version_key(str(org.id)) not in redis.store

    async def test_a_second_read_only_run_reports_the_same_thing(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Nothing was written, so the second run finds exactly what the first did."""
        source = _Source([(uuid.uuid4(), people["report"].id)])
        first, _ = await _run(org, source)
        second, _ = await _run(org, source)
        assert first == second

    async def test_the_default_is_read_only_when_the_caller_says_nothing(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The whole point of a second tool: `apply` has no default that writes."""
        import inspect

        assert (
            inspect.signature(verify_tool.verify).parameters["apply"].default
            is inspect.Parameter.empty
        )
        results, summary = await verify_tool.verify(
            [org.id], _Source([]), apply=False, fleet=True, pace_ms=0, page_pace_ms=0
        )
        assert results[0]["dry_run"] is True and summary["read_only"] is True


# --------------------------------------------------------------------- what counts as a finding


class TestFindings:
    async def test_a_matching_pair_is_clean(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        agreed = uuid.uuid4()
        await _link(session, org, people["manager"], people["report"], agreed)
        results, summary = await _run(org, _Source([(agreed, people["report"].id)]))
        assert verify_tool.findings(results[0]) == []
        assert summary["clean"] is True
        assert verify_tool.exit_status(summary) == 0

    async def test_an_unlinked_active_member_is_not_a_finding(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        """A tenant with no HRMS, or a contractor: an ordinary state, not a mismatch."""
        results, summary = await _run(org, _Source([]))
        assert results[0]["memberships_unlinked"] == 4
        assert summary["clean"] is True
        assert summary["memberships_unlinked_total"] == 4

    async def test_a_fill_candidate_is_a_finding(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        results, summary = await _run(org, _Source([(uuid.uuid4(), people["report"].id)]))
        assert "would be filled" in verify_tool.findings(results[0])[0]
        assert verify_tool.exit_status(summary) == 3

    async def test_a_disagreement_is_a_finding(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        await _link(session, org, people["manager"], people["report"], uuid.uuid4())
        results, summary = await _run(org, _Source([(uuid.uuid4(), people["report"].id)]))
        assert results[0]["conflicts_disagree"] == 1
        assert "conflict" in verify_tool.findings(results[0])[0]
        assert verify_tool.exit_status(summary) == 3

    async def test_an_id_employee_does_not_recognise_is_a_finding(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """ADR-0008 §4's mistyped UUID — the reason the check is scheduled at all."""
        typo = uuid.uuid4()
        await _link(session, org, people["manager"], people["report"], typo)
        results, summary = await _run(org, _Source([(uuid.uuid4(), None)]))
        assert results[0]["unrecognised_employee_ids"] == [str(typo)]
        assert "does not recognise" in verify_tool.findings(results[0])[0]
        assert verify_tool.exit_status(summary) == 3

    async def test_a_membership_employee_names_but_account_lacks_is_a_finding(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        results, summary = await _run(org, _Source([(uuid.uuid4(), uuid.uuid4())]))
        assert len(results[0]["unrecognised_membership_ids"]) == 1
        assert verify_tool.exit_status(summary) == 3

    async def test_the_summary_names_which_organizations_have_findings(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        _, summary = await _run(org, _Source([(uuid.uuid4(), people["report"].id)]))
        assert list(summary["organizations_with_findings"]) == [str(org.id)]


# --------------------------------------------------------------------- the column-drop evidence


class TestTheColumnDropPrecondition:
    """ADR-0008 Q4: the column is dropped only after the owner's production dry-run reports zero.
    This is the line that says whether it did."""

    async def test_a_clean_fleet_wide_read_only_run_is_ready(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        _, summary = await _run(org, _Source([]), fleet=True)
        assert summary["column_drop_precondition"] == {"ready": True, "blockers": []}

    async def test_a_single_organization_is_never_enough(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        _, summary = await _run(org, _Source([]), fleet=False)
        precondition = summary["column_drop_precondition"]
        assert precondition["ready"] is False
        assert any("fleet-wide" in b for b in precondition["blockers"])

    async def test_a_finding_blocks_it(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        _, summary = await _run(org, _Source([(uuid.uuid4(), people["report"].id)]), fleet=True)
        precondition = summary["column_drop_precondition"]
        assert precondition["ready"] is False
        assert any("findings" in b for b in precondition["blockers"])

    async def test_an_apply_run_is_not_the_evidence(
        self, org: Organization, people: dict[str, Membership]
    ) -> None:
        """A run that wrote links cannot also be the read-only proof that nothing needed writing."""
        _, summary = await _run(org, _Source([]), apply=True, fleet=True)
        precondition = summary["column_drop_precondition"]
        assert precondition["ready"] is False
        assert any("--apply" in b for b in precondition["blockers"])

    def test_no_organizations_is_not_a_clean_bill_of_health(self) -> None:
        summary = verify_tool.summarise([], apply=False, fleet=True)
        assert summary["column_drop_precondition"]["ready"] is False
        assert any("no organizations" in b for b in summary["column_drop_precondition"]["blockers"])


# --------------------------------------------------------------------- --apply


class TestApply:
    async def test_it_fills_a_null_and_says_so(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        employee_id = uuid.uuid4()
        results, summary = await _run(
            org, _Source([(employee_id, people["report"].id)]), apply=True
        )

        assert results[0]["dry_run"] is False
        assert summary["links_written_total"] == 1 and "fill_candidates_total" not in summary
        assert summary["mode"] == "apply" and summary["read_only"] is False
        assert await _employee_id_of(session, people["report"].id) == employee_id

    async def test_the_write_is_audited_as_this_command(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        await _run(org, _Source([(uuid.uuid4(), people["report"].id)]), apply=True)

        (row,) = await _audit_rows(session, "membership.links_reconciled")
        assert row.acting_as == "verify-membership-links-cli"
        assert row.actor_id is None
        assert row.after["links_written"] == 1

    async def test_it_still_never_overwrites_a_populated_link(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        interactive = uuid.uuid4()
        await _link(session, org, people["manager"], people["report"], interactive)
        results, _ = await _run(org, _Source([(uuid.uuid4(), people["report"].id)]), apply=True)
        assert results[0]["links_written"] == 0
        assert results[0]["conflicts_disagree"] == 1
        assert await _employee_id_of(session, people["report"].id) == interactive

    async def test_it_never_bumps_the_permission_version_either(
        self,
        org: Organization,
        people: dict[str, Membership],
        redis: FakeRedis,
    ) -> None:
        await _run(org, _Source([(uuid.uuid4(), people["report"].id)]), apply=True)
        assert permission_version_key(str(org.id)) not in redis.store

    async def test_a_removed_memberships_link_is_reported_and_not_written(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """ADR-0008 §5, from the reconcile side: a removed membership's link only ever changes by
        explicit human action."""
        async with session.begin():
            membership = await session.get(Membership, people["outsider"].id)
            assert membership is not None
            membership.status = "removed"
        results, _ = await _run(org, _Source([(uuid.uuid4(), people["outsider"].id)]), apply=True)
        assert results[0]["links_written"] == 0
        assert len(results[0]["unrecognised_membership_ids"]) == 1

    @pytest.mark.parametrize("name", ["CI", "GITHUB_ACTIONS"])
    @pytest.mark.parametrize("value", ["true", "1", "True", "yes", "anything-at-all"])
    def test_it_is_refused_when_a_pipeline_variable_is_set(self, name: str, value: str) -> None:
        refusal = verify_tool.refusal_for_apply({name: value})
        assert refusal is not None
        assert name in refusal and "pipeline" in refusal

    @pytest.mark.parametrize("env", [{}, {"CI": ""}, {"CI": "false"}, {"CI": "0"}, {"CI": " no "}])
    def test_it_is_allowed_where_no_pipeline_variable_is_set(self, env: dict[str, str]) -> None:
        assert verify_tool.refusal_for_apply(env) is None

    def test_one_variable_being_off_does_not_excuse_the_other(self) -> None:
        """A workflow can `unset CI` and forget `GITHUB_ACTIONS`; either one refuses."""
        assert verify_tool.refusal_for_apply({"CI": "false", "GITHUB_ACTIONS": "true"}) is not None

    def test_the_command_refuses_before_touching_anything_when_ci_is_set(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Through the real entrypoint: exit status 2, a message on stderr, no engine opened."""
        monkeypatch.setenv("CI", "true")
        monkeypatch.setattr(sys, "argv", ["verify_membership_links.py", "--all", "--apply"])
        with pytest.raises(SystemExit) as excinfo:
            verify_tool._main()
        assert excinfo.value.code == 2
        assert "refused" in capsys.readouterr().err


# --------------------------------------------------------------------- the command line


class TestCommandLine:
    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["--apply"],
            ["--org", "not-a-uuid"],
            ["--org", str(uuid.uuid4()), "--all"],
            ["--all", "--pace", "fast"],
        ],
    )
    def test_a_bad_invocation_is_refused_by_the_parser(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.setattr(sys, "argv", ["verify_membership_links.py", *argv])
        with pytest.raises(SystemExit) as excinfo:
            verify_tool._main()
        assert excinfo.value.code == 2

    async def test_it_is_a_thin_face_over_the_reconcile_not_a_second_implementation(self) -> None:
        """One set of rules for filling and verifying; this tool only flips the default."""
        assert verify_tool.reconcile is reconcile
        assert verify_tool.ACTING_AS != reconcile.ACTING_AS

    async def test_the_audit_table_is_untouched_by_an_empty_read_only_run(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        await _run(org, _Source([]))
        async with session.begin():
            count = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
        # Only what creating the fixtures wrote; the run added nothing.
        before = count
        await _run(org, _Source([]))
        async with session.begin():
            after = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
        assert after == before
