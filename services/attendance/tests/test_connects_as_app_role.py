"""The tests must run as the application role, or they prove nothing about isolation.

ADR-0002's sharpest warning is about a *false pass*: if the suite connects as the superuser,
row-level security is invisible here and every isolation assertion passes whatever the
policies say. That is worse than having no test, because the green tick is mistaken for
evidence.

So the fixture's connection identity is itself asserted. These are cheap and they fail loudly
the moment somebody points the test engine back at the convenient DSN.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


class TestTheSuiteRunsUnprivileged:
    async def test_the_session_is_the_app_role(self, session: AsyncSession) -> None:
        who = (await session.execute(text("SELECT current_user"))).scalar_one()
        assert who == "atmora_attendance", (
            f"tests are connected as {who!r}. They must run as the application role, or the "
            "privilege split and row-level security are invisible here."
        )

    async def test_the_app_role_cannot_bypass_row_level_security(
        self, session: AsyncSession
    ) -> None:
        """The single property the whole design rests on.

        A superuser bypasses RLS unconditionally and `FORCE` cannot override it, so if this
        ever becomes true the policies stop being enforcement and become decoration.
        """
        row = (
            await session.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        assert row.rolsuper is False, "the app role is a superuser — RLS cannot apply to it"
        assert row.rolbypassrls is False, "the app role has BYPASSRLS — RLS cannot apply to it"

    async def test_the_app_role_cannot_reach_another_service_schema(
        self, session: AsyncSession
    ) -> None:
        """attendance-service must not be able to read `employee.employees`.

        Each service connects as its own NOBYPASSRLS role, granted usage on its own schema and
        nothing else — so one service's connection cannot read another's tables.
        """
        with pytest.raises(Exception, match="permission denied"):
            await session.execute(text("SELECT 1 FROM employee.employees"))

    async def test_the_app_role_cannot_truncate(self, session: AsyncSession) -> None:
        """TRUNCATE is not filtered by row-level security — it is all or nothing.

        An app role holding it could empty a table across every tenant at once, so it is
        withheld and teardown uses the admin role instead.
        """
        with pytest.raises(Exception, match="permission denied"):
            await session.execute(text("TRUNCATE attendance.audit_logs"))

    async def test_the_audit_trail_cannot_be_rewritten(self, session: AsyncSession) -> None:
        """A trail the application can edit is not a trail.

        Enforced with a table privilege rather than a convention or a policy, because this is
        exactly what table privileges are for.
        """
        with pytest.raises(Exception, match="permission denied"):
            await session.execute(text("UPDATE attendance.audit_logs SET action = 'tampered'"))
