"""`platform_core.auth.column_scope_resolver` — the one-owner-column `ScopeResolver` factory."""

from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, Table, Uuid

from platform_core.auth import (
    OwnerSubject,
    ScopeResolver,
    column_scope_resolver,
    owner_subject_for,
    register_scope_resolver,
    reset_scope_resolvers,
)

_t = Table(
    "things",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("owner_id", Uuid),
    Column("team_id", Uuid),
    Column("location_id", Uuid),
)


def test_it_exposes_the_owner_column_and_no_team_or_location_by_default() -> None:
    resolver: ScopeResolver = column_scope_resolver(owner=_t.c.owner_id)()
    assert resolver.owner_column() is _t.c.owner_id
    assert resolver.team_column() is None
    assert resolver.location_column() is None


def test_team_and_location_can_be_named() -> None:
    resolver = column_scope_resolver(
        owner=_t.c.owner_id, team=_t.c.team_id, location=_t.c.location_id
    )()
    assert resolver.team_column() is _t.c.team_id
    assert resolver.location_column() is _t.c.location_id


def test_each_call_of_the_factory_yields_a_resolver_over_the_same_columns() -> None:
    factory = column_scope_resolver(owner=_t.c.owner_id)
    assert factory().owner_column() is factory().owner_column()


def test_the_factory_registers_like_a_resolver_class_does() -> None:
    reset_scope_resolvers()
    try:
        factory = column_scope_resolver(owner=_t.c.owner_id)
        register_scope_resolver("things", factory, owner_subject=OwnerSubject.MEMBERSHIP)
        # Idempotent for the same subject, the way a product registered from two modules relies on.
        register_scope_resolver("things", factory, owner_subject=OwnerSubject.MEMBERSHIP)
        assert owner_subject_for("things.thing.read") is OwnerSubject.MEMBERSHIP
    finally:
        reset_scope_resolvers()
