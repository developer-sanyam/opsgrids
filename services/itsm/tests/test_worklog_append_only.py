"""Incident worklogs are append-only, and the database enforces it.

Appending is normal; rewriting history is not. A `BEFORE UPDATE OR DELETE` trigger refuses both,
so a worklog is a record of what happened rather than a draft — a guarantee that survives the
app role being re-granted DML after every migration.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app import selectors, services
from app.schemas import WorklogIn
from platform_core.auth import Scope
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


class TestWorklogAppendOnly:
    async def test_appending_grows_the_thread(self, session, org_id, data) -> None:
        """The ordinary path works: an appended entry joins the incident's thread."""
        ctx = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)
        incident = data["inc_a"]
        async with session.begin():
            before = len(await selectors.worklogs_for(session, org_id, incident.id))
            await services.add_worklog(
                session,
                ctx,
                incident,
                WorklogIn(
                    author="Aravind Pillai", body="Swinging traffic to standby.", kind="note"
                ),
            )
        after = await selectors.worklogs_for(session, org_id, incident.id)
        assert len(after) == before + 1
        assert after[-1].body == "Swinging traffic to standby."

    async def test_updating_a_worklog_is_refused_by_the_database(
        self, session, org_id, data
    ) -> None:
        async with session.begin():
            entries = await selectors.worklogs_for(session, org_id, data["inc_a"].id)
        target = entries[0].id
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    text("UPDATE itsm.incident_worklogs SET body = 'tampered' WHERE id = :id"),
                    {"id": target},
                )

    async def test_deleting_a_worklog_is_refused_by_the_database(
        self, session, org_id, data
    ) -> None:
        async with session.begin():
            entries = await selectors.worklogs_for(session, org_id, data["inc_a"].id)
        target = entries[0].id
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    text("DELETE FROM itsm.incident_worklogs WHERE id = :id"), {"id": target}
                )
