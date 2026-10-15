"""The step-up marker is minted in exactly one place and required by no reveal route
(ADR-0023, second review round).

`POST /auth/mfa/step-up` puts `otp` **and** a marker in the token's `amr`; lead- and
customer-service require both on their reveal routes, because `otp` alone is what every session
that gave a code at sign-in carries for its whole life. Two properties matter, and both are pinned
here as *source-level* rules — a test that drives the code can only show that the paths it thought
of are safe, while a rule over the source fails on the path somebody adds later:

1. **Nothing but `step_up` can mint it.** The literal `"stepup"` appears in the codebase in exactly
   one place (auth's definition), and `STEP_UP_AMR` is referenced
   only where it is meant to be: `step_up` (to add it to one token) and `_session_amr` (to
   strip it). Every route by which a token or a session's `amr` is built
   goes through `_session_amr`, so even a session row that somehow held the marker cannot mint it.
2. **No reveal route requires it.** Since 2026-10-15 (ADR-0023 addendum) lead and customer ask for
   the permission alone; the marker stays only because `POST /auth/mfa/step-up` still mints it.

Read with `ast`: `import app` is ambiguous across services (each exposes a top-level `app`), so
they cannot be imported into one process.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from _helpers import parse_python

REPO_ROOT = Path(__file__).resolve().parents[2]

AUTH = REPO_ROOT / "services/auth/app/services.py"
#: The routes that used to require the marker. Since 2026-10-15 (ADR-0023 addendum) none does: a
#: reveal needs the permission alone. They are kept here so the test below can prove it stays so.
FORMER_GUARDS = {
    "lead": REPO_ROOT / "services/lead/app/api/routes.py",
    "customer": REPO_ROOT / "services/customer/app/api/routes.py",
}
EXPECTED = "stepup"

#: Where `"stepup"` may be written as a literal: the one definition, nowhere else.
LITERAL_HOMES = {AUTH}

#: The functions in auth-service allowed to *name* `STEP_UP_AMR`. `step_up` adds it to one token;
#: `_session_amr` removes it from a session's record. Nothing else in the service may mention it.
AUTH_FUNCTIONS_THAT_MAY_NAME_IT = {"step_up", "_session_amr"}

#: Every source file that ships: the services' `app/` packages and the shared library.
SHIPPED_SOURCE = sorted(
    [*REPO_ROOT.glob("services/*/app/**/*.py"), *REPO_ROOT.glob("libs/*/src/**/*.py")]
)


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _string_constant(path: Path, name: str) -> str:
    """The module-level `NAME = "literal"` in `path`."""
    for node in parse_python(path).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise AssertionError(f"{_rel(path)} defines no string constant {name}")


def _calls(tree: ast.AST) -> Iterator[ast.Call]:
    return (node for node in ast.walk(tree) if isinstance(node, ast.Call))


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _mentions_session_amr(node: ast.AST) -> bool:
    """Does this expression pass through `_session_amr(...)`?"""
    return any(_called_name(call) == "_session_amr" for call in _calls(node))


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """`id(node) -> name of the innermost function containing it`, for every node in `tree`."""
    owner: dict[int, str] = {}

    def visit(node: ast.AST, current: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (
                child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else current
            )
            if inner is not None:
                owner[id(child)] = inner
            visit(child, inner)

    visit(tree, None)
    return owner


# ------------------------------------------------------------------ nothing requires it any more


def test_the_marker_is_defined_in_auth_and_the_documented_value() -> None:
    assert _string_constant(AUTH, "STEP_UP_AMR") == EXPECTED


@pytest.mark.parametrize("service", sorted(FORMER_GUARDS))
def test_no_reveal_route_requires_a_step_up(service: str) -> None:
    """Owner decision 2026-10-15 (ADR-0023 addendum): "if the user has access they don't need to
    ask for 2FA". The reveal routes are `require(PERMISSION)` with no `amr`. This pins it, so a
    step-up cannot creep back onto a reveal without an ADR."""
    tree = parse_python(FORMER_GUARDS[service])
    calls = [
        call
        for call in _calls(tree)
        if _called_name(call) == "require" and any(k.arg == "amr" for k in call.keywords)
    ]
    assert calls == [], f"{service}: a route asks for amr again"
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "STEP_UP_AMR" not in names


# ------------------------------------------------------- only step_up can mint it: the rules


def test_the_literal_appears_only_where_the_marker_is_defined() -> None:
    """Any string equal to `stepup`, anywhere that ships, other than auth's definition. (Exact
    equality, so prose that mentions the word in a docstring or comment is not what is being
    scanned; a literal somebody types into an `amr` list is.)"""
    found: list[str] = []
    for path in SHIPPED_SOURCE:
        for node in ast.walk(parse_python(path)):
            if (
                isinstance(node, ast.Constant)
                and node.value == EXPECTED
                and path not in LITERAL_HOMES
            ):
                found.append(f"{_rel(path)}:{node.lineno}")
    assert found == [], f"the literal {EXPECTED!r} outside its definitions: {found}"
    for path in LITERAL_HOMES:
        literals = [
            node
            for node in ast.walk(parse_python(path))
            if isinstance(node, ast.Constant) and node.value == EXPECTED
        ]
        assert len(literals) == 1, (
            f"{_rel(path)}: the literal should appear once, as the definition"
        )


def test_the_constant_is_named_only_where_it_is_meant_to_be() -> None:
    """`STEP_UP_AMR` in auth-service: the definition, `step_up` and `_session_amr`, nothing else. In
    every other shipped file: nowhere."""
    offenders: list[str] = []
    for path in SHIPPED_SOURCE:
        tree = parse_python(path)
        owner = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Name) and node.id == "STEP_UP_AMR"):
                continue
            where = f"{_rel(path)}:{node.lineno}"
            function = owner.get(id(node))
            if path == AUTH:
                if function is None and isinstance(node.ctx, ast.Store):
                    continue  # the definition
                if function in AUTH_FUNCTIONS_THAT_MAY_NAME_IT:
                    continue
            offenders.append(f"{where} (in {function or 'module level'})")
    assert offenders == [], f"STEP_UP_AMR named where it may not be: {offenders}"


def test_only_step_up_hands_anything_to_a_token_beyond_its_sessions_record() -> None:
    """`extra_amr=` — the only way to add to a token's `amr` beyond what its session holds — is
    passed from `step_up` alone. (`_mint_access_only`, which accepts it, is what `step_up`
    calls.)"""
    tree = parse_python(AUTH)
    owner = _enclosing_functions(tree)
    passed_from = {
        owner.get(id(call))
        for call in _calls(tree)
        if any(k.arg == "extra_amr" for k in call.keywords)
    }
    assert passed_from == {"step_up"}, passed_from


def test_every_token_minted_takes_its_amr_from_the_stripped_session_record() -> None:
    """Every `mint_access(...)` in auth-service — the member and the operator mints, four in all —
    builds `amr` from `_session_amr(...)`. That is what makes "no sign-in, refresh, organization
    switch, rotation or operator flow can produce the marker" literally true, whatever a session
    row holds."""
    tree = parse_python(AUTH)
    mints = [call for call in _calls(tree) if _called_name(call) == "mint_access"]
    assert len(mints) == 4, f"expected four mint_access calls, found {len(mints)}"
    for call in mints:
        amr = [k.value for k in call.keywords if k.arg == "amr"]
        assert len(amr) == 1, f"mint_access at line {call.lineno} has no amr="
        assert _mentions_session_amr(amr[0]), (
            f"mint_access at line {call.lineno} builds amr from {ast.unparse(amr[0])!r}, "
            "not from `_session_amr(...)`"
        )


def test_every_write_to_a_sessions_amr_passes_through_the_strip() -> None:
    """`something.amr = ...` — verify_mfa and the operator's two steps — is built from
    `_session_amr(...)` too, so a marker that got into a row is dropped the next time it is
    written."""
    tree = parse_python(AUTH)
    writes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "amr" for t in node.targets)
    ]
    assert len(writes) == 3, f"expected three writes to a session's amr, found {len(writes)}"
    for node in writes:
        assert _mentions_session_amr(node.value), (
            f"line {node.lineno}: {ast.unparse(node)!r} is not built from `_session_amr(...)`"
        )


def test_a_session_is_built_only_by_start_session_and_never_updated_in_bulk() -> None:
    """`Session(..., amr=...)` — a row created with an `amr` of the caller's choosing — happens in
    `start_session` and nowhere else, and no statement anywhere that ships sets `amr` through
    `.values(amr=...)` (`update(Session).values(...)` or `Session.__table__.update()...`)."""
    for path in SHIPPED_SOURCE:
        tree = parse_python(path)
        owner = _enclosing_functions(tree)
        for call in _calls(tree):
            keywords = {k.arg for k in call.keywords}
            where = f"{_rel(path)}:{call.lineno}"
            if _called_name(call) == "values" and "amr" in keywords:
                raise AssertionError(f"{where}: a bulk write of `amr` via .values(amr=...)")
            if path == AUTH and _called_name(call) == "Session" and "amr" in keywords:
                function = owner.get(id(call))
                assert function == "start_session", (
                    f"{where}: Session(amr=...) built in {function}, not start_session"
                )


#: The federated-sign-in methods `start_session` may be told by name: single sign-on, and the
#: self-serve social login that was added after it with its own label on purpose (see the comment
#: on `OAUTH_AMR` in auth's services.py). Each must be a module-level string literal there, and
#: must never spell what only a verified second factor or a step-up may put in `amr`.
FEDERATED_AMR_CONSTANTS = {"SSO_AMR", "OAUTH_AMR"}


def test_start_session_is_only_ever_told_plain_methods() -> None:
    """The callers of `start_session` pass `amr=` as a literal list of the methods a sign-in used
    (`pwd`, or one of the federated-sign-in constants). None of them is a list built from anything
    else."""
    tree = parse_python(AUTH)
    for call in _calls(tree):
        if _called_name(call) != "start_session":
            continue
        amr = [k.value for k in call.keywords if k.arg == "amr"]
        assert len(amr) == 1, f"line {call.lineno}: start_session without amr="
        assert isinstance(amr[0], ast.List), f"line {call.lineno}: amr is {ast.unparse(amr[0])!r}"
        for element in amr[0].elts:
            assert isinstance(element, ast.Constant | ast.Name), ast.unparse(element)
            if isinstance(element, ast.Constant):
                assert element.value in {"pwd"}, ast.unparse(element)
            else:
                assert element.id in FEDERATED_AMR_CONSTANTS, ast.unparse(element)


def test_federated_amr_constants_never_claim_a_second_factor() -> None:
    """A federated sign-in is asserted by an external issuer, so what it puts in `amr` must be
    its own label: never `pwd` (the platform did not check a password), never `otp` (nobody gave
    a code), never the step-up marker, and not shared with another federated flow."""
    values = {name: _string_constant(AUTH, name) for name in sorted(FEDERATED_AMR_CONSTANTS)}
    assert all(values.values()), values
    for name, value in values.items():
        assert value not in {"pwd", "otp", EXPECTED}, f"{name} = {value!r}"
    assert len(set(values.values())) == len(values), values
