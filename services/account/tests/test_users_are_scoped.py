"""`users` is not protected by row-level security, so this is what protects it.

ADR-0002 decided to leave `account.users` un-policied. The membership-`EXISTS` policy is the
more secure design and is not viable: Postgres applies a SELECT policy to `INSERT ... RETURNING`,
SQLAlchemy flushes `User` with `RETURNING` because of the server-default timestamps, and a new
user has no membership yet — so the policy would fail `add_member` on its first statement and
take invitation acceptance with it.

The ADR promised a compensating control in its place: **no route may read `users` without going
through a membership.** This is that control.

It is a static check rather than a runtime one on purpose. The risk is not that today's code is
wrong — it is that somebody adds a query in six months. A test that reads the source fails on the
diff that introduces the problem, which is the only moment anybody is in a position to fix it
cheaply.

Two exemptions are allowed, both deliberate, both named. Adding a third has to be an explicit
edit to this file, which is the point: it turns a silent widening of the read surface into a
line in a code review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

APP = Path(__file__).resolve().parent.parent / "app"

#: Reads of `users` that legitimately carry no organization, with the reason each is allowed.
GLOBAL_READS = {
    "get_user_by_email": (
        "Resolving an existing platform account before a membership exists — this is what lets "
        "one person work for two customers with one login. Called by add_member and invite."
    ),
}


def _functions_selecting_users(path: Path) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in `path` whose body contains `select(User ...)`."""
    tree = ast.parse(path.read_text())
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "select"
                and any(isinstance(a, ast.Name) and a.id == "User" for a in inner.args)
            ):
                found[node.name] = node
                break
    return found


def _joins_membership(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function ties `users` to `memberships`.

    Either by selecting both — `select(Membership, User).join(...)` — or by naming `Membership`
    anywhere in the statement it builds. Deliberately loose: the goal is to catch a bare
    `select(User)`, not to police how the join is spelled.
    """
    return any(isinstance(node, ast.Name) and node.id == "Membership" for node in ast.walk(fn))


class TestUsersAreReadThroughMemberships:
    def test_every_user_query_joins_memberships_or_is_a_named_exception(self) -> None:
        selectors = APP / "selectors.py"
        offenders: list[str] = []

        for name, fn in _functions_selecting_users(selectors).items():
            if name in GLOBAL_READS or _joins_membership(fn):
                continue
            offenders.append(name)

        assert not offenders, (
            f"{offenders} read account.users without joining memberships. `users` carries no "
            "RLS policy (ADR-0002), so a query with no organization behind it returns every "
            "person on the platform. Join through memberships, or add the function to "
            "GLOBAL_READS with the reason it is safe."
        )

    def test_the_public_routes_never_query_users_directly(self) -> None:
        """The ADR's claim that `selectors.py` is the only place that queries it.

        A route building its own `select(User)` would bypass the check above entirely, so the
        claim is asserted rather than trusted.
        """
        routes = APP / "api" / "routes.py"
        assert not _functions_selecting_users(routes), (
            "a route queries `users` directly. Route-level queries escape the scoping check in "
            "this module — go through a selector."
        )

    def test_the_exemptions_are_real_and_documented(self) -> None:
        """A stale allowlist is worse than none: it exempts a name nobody can find.

        Adding an entry means the reader can see what was allowed and why. Removing the function
        without removing the entry silently re-opens the exemption for whatever is named next.
        """
        present = _functions_selecting_users(APP / "selectors.py")

        for name, reason in GLOBAL_READS.items():
            assert name in present, (
                f"GLOBAL_READS names {name!r}, which no longer queries users. Remove the entry — "
                "a stale exemption widens the read surface for the next function to take the name."
            )
            assert len(reason) > 40, f"{name} is exempt without a real reason"


class TestTheInternalApiIsTheKnownException:
    def test_internal_reads_are_cross_tenant_by_design(self) -> None:
        """`internal.py` reads `users` with no organization, and that is the point.

        Recorded here so the exception is visible from the same place as the rule. It is guarded
        differently — the router sits behind `require_service_token`, is excluded from the public
        schema, and its reads are traced. It is not reachable by a user token at all.
        """
        internal = _functions_selecting_users(APP / "api" / "internal.py")
        assert internal, (
            "the internal directory lookups no longer query users — if they moved, this "
            "exception and the tracing that covers it need to move with them."
        )
