"""Document's `core` slice is well-formed and cannot reach another product.

No database: the catalogue is code, and this is the guard that turns "a role quietly holds a
foreign product's permission" — the defect this structure exists to prevent — into a red build.
"""

from __future__ import annotations

from app.catalogue import DOCUMENT, DOCUMENT_ROLE_TEMPLATES
from platform_core.catalogue import (
    Grant,
    PermissionCatalogue,
    PermissionSpec,
    RoleTemplate,
    validate,
)


def test_the_slice_is_exactly_two_permissions() -> None:
    """Two, not three. There is no `core.content.asset.delete`: nobody has asked for an
    upload-but-not-remove role, a third permission is a third thing ADR-0007's reconcile has to
    deliver to every live tenant, and an ordinary delete is non-destructive by design."""
    assert set(DOCUMENT.codes()) == {
        "core.content.asset.read",
        "core.content.asset.write",
    }


def test_both_permissions_pin_default_and_max_at_org() -> None:
    """The point of the whole design (ADR-0009 §4).

    `max_scope` is a ladder *ceiling*, not an allowed-set — account validates a grant with a
    single rank comparison — so ceilinging at ORG alone would leave OWN, TEAM and LOCATION
    grantable and undefined, and a rung with no meaning gets improvised per endpoint. Pinning
    the *default* at ORG too makes every rung below it unreachable rather than merely ungranted.
    """
    for spec in DOCUMENT.specs:
        assert spec.default_scope == "ORG", spec.resource
        assert spec.max_scope == "ORG", spec.resource


def test_no_permission_is_marked_dangerous() -> None:
    """`is_dangerous` has zero references in `libs/platform-core` and changes no authorization
    outcome. What gates these routes is the ORG floor and the audit row."""
    assert not any(spec.is_dangerous for spec in DOCUMENT.specs)


def test_owner_and_admin_hold_both_grants_at_org() -> None:
    for code in ("ORG_OWNER", "ORG_ADMIN"):
        template = next(t for t in DOCUMENT_ROLE_TEMPLATES if t.code == code)
        assert {g.code for g in template.grants} == set(DOCUMENT.codes())
        assert {g.scope for g in template.grants} == {"ORG"}


def test_member_holds_neither_grant() -> None:
    """An ordinary employee has no reason to list or upload to the organization's asset
    library, and "the images are public anyway so read is harmless" is an argument for adding a
    grant that nothing uses."""
    assert [t.code for t in DOCUMENT_ROLE_TEMPLATES] == ["ORG_OWNER", "ORG_ADMIN"]
    assert not any(t.is_default for t in DOCUMENT_ROLE_TEMPLATES)


def test_there_is_no_storefront_role() -> None:
    """No `storefront` product is registered anywhere in the platform. When one is, whether
    store editing gets its own role is that ADR's question, not this one's."""
    assert not any("STOREFRONT" in t.code for t in DOCUMENT_ROLE_TEMPLATES)


def test_validate_refuses_a_foreign_product_grant() -> None:
    foreign = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product="core",
        grants=[Grant("hrms.time.attendance.write", "ORG")],
    )
    try:
        validate([foreign])
    except ValueError as exc:
        assert "ROGUE" in str(exc)
    else:
        raise AssertionError("expected validate() to refuse a foreign-product grant")


def test_pick_refuses_an_unknown_code() -> None:
    catalogue = PermissionCatalogue(
        "core", [PermissionSpec("content", "asset", "read", "read", "ORG", "ORG")]
    )
    try:
        catalogue.pick(("content.asset.delete", "ORG"))
    except ValueError:
        pass
    else:
        raise AssertionError("expected pick() to refuse an unknown permission code")


def test_every_declared_permission_is_actually_enforced_by_a_route() -> None:
    """The pin between the catalogue and the code that reads it.

    A permission renamed in one place and not the other is either a route nobody can call or a
    catalogue entry nothing enforces, and neither fails loudly on its own. Asserted by reading
    the router's source rather than its dependency objects, because `require()` closes over the
    name and a `Depends` gives no way back to it.
    """
    import inspect

    import app.api.routes as routes

    source = inspect.getsource(routes)
    for code in DOCUMENT.codes():
        assert f'require("{code}"' in source, f"{code} is declared but no route guards it"


def test_every_guarded_route_is_floored_at_org() -> None:
    """The floor is redundant given the ceiling and is written anyway: the ceiling is catalogue
    data a later edit can widen, the floor is code in front of the query."""
    import inspect

    import app.api.routes as routes

    source = inspect.getsource(routes)
    for code in DOCUMENT.codes():
        start = source.index(f'require("{code}"')
        assert "minimum=Scope.ORG" in source[start : start + 120], code
