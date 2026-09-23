"""Stored company names are kept exactly as typed — nothing trims them on the way in — so a
name with trailing whitespace is a real, live row. The lookup that refuses a duplicate has to
fold **both** sides with the same `lower(btrim(...))`; trimming only the input (this file's
regression) makes "Acme " never match itself, and the insert then falls through to
`uq_companies_org_name` as an unhandled `IntegrityError` — a 500 where there used to be a 409,
and, on the lead-conversion path, a 503 the lead can never get out of.

Three cases, each written as the failure it once was:
  * "Acme " twice                    -> 409, not an IntegrityError
  * "Acme " then "ACME "             -> 409, not a new case-insensitive duplicate
  * a lead converting into "Acme "   -> the existing company is reused, not a 500
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app import services
from platform_core.auth import Scope
from platform_core.errors import ConflictProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


async def _owner(session, write):  # type: ignore[no-untyped-def]
    return await services.create_owner(
        session, write, full_name="Rep", email=f"rep-{uuid.uuid4().hex[:6]}@example.com"
    )


async def test_the_same_name_with_trailing_space_twice_is_a_409(session, org_id) -> None:  # type: ignore[no-untyped-def]
    write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
    async with session.begin():
        rep = await _owner(session, write)
        await services.create_company(session, write, name="Acme ", owner_id=rep.id)

    with pytest.raises(ConflictProblem):
        async with session.begin():
            await services.create_company(session, write, name="Acme ", owner_id=rep.id)


async def test_a_case_variant_of_a_trailing_space_name_is_a_409(session, org_id) -> None:  # type: ignore[no-untyped-def]
    write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
    async with session.begin():
        rep = await _owner(session, write)
        await services.create_company(session, write, name="Acme ", owner_id=rep.id)

    with pytest.raises(ConflictProblem):
        async with session.begin():
            await services.create_company(session, write, name="ACME ", owner_id=rep.id)


async def test_a_lead_converting_into_an_existing_trailing_space_company_reuses_it(  # type: ignore[no-untyped-def]
    session, org_id
) -> None:
    write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
    async with session.begin():
        rep = await _owner(session, write)
    lead_ctx = services.lead_conversion_ctx(org_id, None)

    async with session.begin():
        first, _ = await services.ensure_company_and_contact_for_lead(
            session,
            lead_ctx,
            company_id=uuid.uuid4(),
            contact_id=uuid.uuid4(),
            company_name="Acme ",
            owner_id=rep.id,
            contact_full_name="A",
            contact_email=None,
            contact_raw_phone=None,
        )
    try:
        async with session.begin():
            second, _ = await services.ensure_company_and_contact_for_lead(
                session,
                lead_ctx,
                company_id=uuid.uuid4(),
                contact_id=uuid.uuid4(),
                company_name="Acme ",
                owner_id=rep.id,
                contact_full_name="B",
                contact_email=None,
                contact_raw_phone=None,
            )
    except IntegrityError as exc:  # pragma: no cover - the regression this test exists for
        pytest.fail(f"a second conversion into the same company hit the constraint: {exc!r}")
    assert second.id == first.id


async def test_a_padded_name_and_its_trimmed_spelling_are_one_name(session, org_id) -> None:  # type: ignore[no-untyped-def]
    """The stored side, on its own: an existing "Acme " must be found by a plain "acme"."""
    write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
    async with session.begin():
        rep = await _owner(session, write)
        await services.create_company(session, write, name="Acme ", owner_id=rep.id)

    with pytest.raises(ConflictProblem):
        async with session.begin():
            await services.create_company(session, write, name="acme", owner_id=rep.id)
