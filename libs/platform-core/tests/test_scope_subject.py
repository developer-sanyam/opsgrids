"""Whose id OWN and TEAM compare: the membership subject (CRM overhaul R2.2, registry A1-A12).

`register_scope_resolver` used to write a dict nothing read, and `ScopeContext.apply` and
`assert_can_touch` hard-wired `Principal.subject_ref`, which is only ever an HR employee id. A
product now declares, once, whose id its OWN and TEAM rungs compare (`OwnerSubject`), `require()`
snapshots that onto the context, and `ScopeContext.own_ref` is the one place that reads it.

Nothing in the platform opts in yet: every product stays on the employee link, and these tests
are what says so.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import platform_core
from platform_core import auth
from platform_core.auth import (
    OwnerSubject,
    owner_subject_for,
    register_scope_resolver,
    reset_scope_resolvers,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class _Resolver:
    """A stand-in `ScopeResolver`. The registry stores the factory and never calls it."""

    def owner_column(self) -> Any:
        return None

    def team_column(self) -> Any | None:
        return None

    def location_column(self) -> Any | None:
        return None


class _OtherResolver(_Resolver):
    """A second factory, so a test can tell which one the registry kept."""


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """The registry is process-global. Empty it around every test so import order and test
    pollution can never decide which subject a context carries."""
    reset_scope_resolvers()
    yield
    reset_scope_resolvers()


class TestOwnerSubject:
    def test_values_are_the_wire_strings(self) -> None:
        assert [member.value for member in OwnerSubject] == ["employee_link", "membership"]
        assert OwnerSubject("employee_link") is OwnerSubject.EMPLOYEE_LINK
        assert OwnerSubject("membership") is OwnerSubject.MEMBERSHIP

    def test_it_is_exported_from_auth_and_the_package_root(self) -> None:
        assert "OwnerSubject" in auth.__all__
        assert "OwnerSubject" in platform_core.__all__
        assert platform_core.OwnerSubject is OwnerSubject


class TestRegistry:
    def test_two_positional_arguments_still_register_the_employee_link(self) -> None:
        # The sixteen existing `scope.register()` call shapes: (product, factory), nothing else.
        register_scope_resolver("hrms", _Resolver)
        assert auth._resolvers["hrms"] == (_Resolver, OwnerSubject.EMPLOYEE_LINK)
        assert owner_subject_for("hrms.time.leave.read") is OwnerSubject.EMPLOYEE_LINK

    def test_a_membership_registration_is_recorded(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert auth._resolvers["crm"] == (_Resolver, OwnerSubject.MEMBERSHIP)
        assert owner_subject_for("crm.pipeline.deal.read") is OwnerSubject.MEMBERSHIP

    def test_the_subject_is_keyword_only(self) -> None:
        # Called through `Any` so the type checker does not refuse the very call being pinned.
        register: Any = register_scope_resolver
        with pytest.raises(TypeError):
            register("crm", _Resolver, OwnerSubject.MEMBERSHIP)

    def test_re_registering_the_same_subject_is_a_no_op_that_keeps_the_first_factory(self) -> None:
        # customer registers `crm` twice from one process (api/routes.py and api/c1_customer360.py).
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        register_scope_resolver("crm", _OtherResolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert auth._resolvers["crm"] == (_Resolver, OwnerSubject.MEMBERSHIP)

        register_scope_resolver("hrms", _Resolver)
        register_scope_resolver("hrms", _OtherResolver)
        assert auth._resolvers["hrms"] == (_Resolver, OwnerSubject.EMPLOYEE_LINK)

    def test_re_registering_a_different_subject_raises_value_error_naming_both(self) -> None:
        register_scope_resolver("crm", _Resolver)
        with pytest.raises(ValueError, match="'crm'") as refused:
            register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert "employee_link" in str(refused.value)
        assert "membership" in str(refused.value)
        # The refused call changed nothing.
        assert auth._resolvers["crm"] == (_Resolver, OwnerSubject.EMPLOYEE_LINK)

    def test_dropping_the_subject_on_a_second_registration_is_a_different_subject(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        with pytest.raises(ValueError, match="'crm'"):
            register_scope_resolver("crm", _Resolver)
        assert owner_subject_for("crm.pipeline.deal.read") is OwnerSubject.MEMBERSHIP

    def test_reset_empties_the_registry(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        reset_scope_resolvers()
        assert auth._resolvers == {}
        assert owner_subject_for("crm.pipeline.deal.read") is OwnerSubject.EMPLOYEE_LINK
        # A product can register again, with any subject, once the registry is empty.
        register_scope_resolver("crm", _Resolver)
        assert owner_subject_for("crm.pipeline.deal.read") is OwnerSubject.EMPLOYEE_LINK


class TestOwnerSubjectFor:
    def test_uses_the_text_before_the_first_dot(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert owner_subject_for("crm.book.company.read") is OwnerSubject.MEMBERSHIP
        assert owner_subject_for("crm.lead.lead.read_sensitive") is OwnerSubject.MEMBERSHIP
        # A product is a whole word: a permission that merely starts with the same letters is not.
        assert owner_subject_for("crmx.book.company.read") is OwnerSubject.EMPLOYEE_LINK

    def test_an_unregistered_product_is_the_employee_link(self) -> None:
        assert owner_subject_for("hrms.time.leave.read") is OwnerSubject.EMPLOYEE_LINK

    def test_a_prefix_miss_is_the_employee_link(self) -> None:
        # notification registers `notification`, while most of its routes require `engage.*`
        # codes. Opting `notification` in would therefore not reach them, and must not guess.
        register_scope_resolver("notification", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert owner_subject_for("notification.delivery.template.read") is OwnerSubject.MEMBERSHIP
        assert owner_subject_for("engage.campaign.campaign.send") is OwnerSubject.EMPLOYEE_LINK

    def test_a_permission_with_no_dot_is_looked_up_whole(self) -> None:
        register_scope_resolver("crm", _Resolver, owner_subject=OwnerSubject.MEMBERSHIP)
        assert owner_subject_for("crm") is OwnerSubject.MEMBERSHIP
        assert owner_subject_for("hrms") is OwnerSubject.EMPLOYEE_LINK


class TestNoProductionReset:
    def test_no_service_calls_reset_scope_resolvers(self) -> None:
        """`reset_scope_resolvers` is a test hook. A service that called it at import or at
        request time would let ordering decide which subject a context carries."""
        files = sorted((REPO_ROOT / "services").glob("*/app/**/*.py"))
        assert (REPO_ROOT / "services" / "customer" / "app" / "scope.py") in files, (
            "the scan did not find service code; the directory layout moved"
        )
        offenders: list[str] = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                name = (
                    node.id
                    if isinstance(node, ast.Name)
                    else node.attr
                    if isinstance(node, ast.Attribute)
                    else node.name
                    if isinstance(node, ast.alias)
                    else None
                )
                if name == "reset_scope_resolvers":
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{getattr(node, 'lineno', 0)}")
        assert offenders == []
