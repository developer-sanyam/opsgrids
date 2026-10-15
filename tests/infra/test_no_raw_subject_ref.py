"""A static guard for the membership subject (CRM overhaul R2.2, registry A11, DD-7, DD-38).

A service that declares `OwnerSubject.MEMBERSHIP` has OWN and TEAM compare the caller's membership
id through `ScopeContext.own_ref`. That only holds if nothing in the service reads the employee
link (`subject_ref`) behind the platform's back, because a raw read keeps comparing the wrong
person-id and nothing fails: a deal defaults its owner to the employee link, an `owner=me` summary
asks the wrong question, an OWN rep is "forced" to someone else's id.

**What it scans.** Every `*.py` under `services/<name>/app` (never `migrations`), by AST only. A
text search is wrong: docstrings, comments and `libs/contracts/openapi/deal.json` all say the word.

**What it enforces, and on whom.** `OPTED_IN` names the services the full rules apply to. R2.2
ships it **empty**, because no service switches in R2.2; R2.7 fills it with `customer`, `deal` and
`lead`. An empty set scans nothing, so three things hold the line meanwhile (DD-7, DD-38):

1. the scanner has self-tests on in-test source strings, so "it flags every spelling" is a test and
   not a belief;
2. `OPTED_IN` must equal the set of services whose code registers a non-default owner subject, so
   a service cannot opt in and escape the guard, nor sit in the set without opting in;
3. a **ratchet** pins today's six raw reads (and no string spelling) in the three CRM services, so a
   seventh cannot land before R2.7 and none can vanish unnoticed.

**What it cannot see.** A read through a variable holding the attribute name built at run time
(`getattr(ctx, name)`), and a `require(...)` whose permission is imported from another module
(that is itself a finding: an argument the scanner cannot resolve is never silently skipped).
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"

#: The services the full rules below apply to. Empty in R2.2; R2.7 sets it to
#: `frozenset({"customer", "deal", "lead"})` and deletes the ratchet.
OPTED_IN: frozenset[str] = frozenset()

#: The three CRM processes. The ratchet and the string-spelling rule apply to them whether or not
#: they have opted in yet.
CRM_SERVICES: tuple[str, ...] = ("customer", "deal", "lead")

#: Each way the employee link is spelled as a string, lower-cased: the attribute and dict key, the
#: token claim, and the forwarded header.
SPELLINGS: frozenset[str] = frozenset({"subject_ref", "sref", "x-subject-ref"})

#: String spellings that are not a read and may stay, matched by file (relative to `services/`)
#: **and** the module-level name that holds the string, so an entry excuses nothing else. Empty:
#: the customer 360 used to spell `"X-Subject-Ref"` in its `_IDENTITY_HEADERS`; it now forwards
#: platform-core's `FORWARDED_HEADERS` instead, so no CRM file spells the employee link at all.
ALLOWED_STRING_SPELLINGS: dict[tuple[str, str], frozenset[str]] = {}

#: THE RATCHET, until R2.7. Today's raw reads of the employee link in the three CRM services,
#: counted per (file relative to `services/`, enclosing function): six, none in `lead` (customer's
#: assignment check; deal's default owner, `owner=me` summary and activity forcing). A seventh
#: read, or one of these vanishing before the flip, fails the test that compares them. R2.7
#: replaces each with `ctx.own_ref` and deletes this constant, `KNOWN_STRING_SPELLINGS`, the two
#: counting helpers and `TestRatchetUntilTheFlip`.
KNOWN_RAW_READS: dict[tuple[str, str], int] = {
    ("customer/app/services.py", "_assert_owner_assignable"): 1,
    # The default owner for a new deal or activity. It was one read in `create_deal` and one in
    # `log_activity`; the self-serve owner has no employee link, so the default moved into one
    # helper (employee link first, then -- ORG scope only -- the membership id), and `create_deal`
    # no longer reads the link itself. Same total. R2.7 deletes the helper for `ctx.own_ref`.
    ("deal/app/api/routes.py", "default_owner"): 2,
    ("deal/app/api/routes.py", "summary"): 2,
    ("deal/app/api/routes.py", "log_activity"): 1,
}

#: The same ratchet for string spellings, counted per (file, holder): none today.
KNOWN_STRING_SPELLINGS: dict[tuple[str, str], int] = {}

#: The default subject, spelled the way a registration spells it.
DEFAULT_SUBJECT = "OwnerSubject.EMPLOYEE_LINK"


@dataclass(frozen=True)
class Site:
    line: int
    function: str  # the innermost enclosing def, or "<module>"
    text: str


@dataclass
class Scan:
    #: `<anything>.subject_ref`, on any object.
    raw_reads: list[Site] = field(default_factory=list)
    #: A string equal to one of `SPELLINGS` outside a docstring: (site, the module-level name that
    #: holds it or "", the string itself).
    strings: list[tuple[Site, str, str]] = field(default_factory=list)
    #: `Principal(..., subject_ref=...)`.
    keyword_args: list[Site] = field(default_factory=list)
    #: `ScopeContext(...)` that does not pass the literal `scope=Scope.ORG`.
    loose_contexts: list[Site] = field(default_factory=list)
    #: `require(...)`: (site, the first argument resolved to a string, or None when it cannot be).
    requires: list[tuple[Site, str | None]] = field(default_factory=list)


def _callee(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def _module_assignments(tree: ast.Module) -> list[tuple[str, ast.expr]]:
    """`NAME = value` and `NAME: T = value` at module level."""
    found: list[tuple[str, ast.expr]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            found.append((node.targets[0].id, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            found.append((node.target.id, node.value))
    return found


class _Scanner(ast.NodeVisitor):
    def __init__(self, tree: ast.Module) -> None:
        self.scan = Scan()
        self._functions: list[str] = []
        self._docstrings = _docstring_ids(tree)
        assignments = _module_assignments(tree)
        self._constants = {
            name: value.value
            for name, value in assignments
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        }
        self._holders = {
            id(constant): name
            for name, value in assignments
            for constant in ast.walk(value)
            if isinstance(constant, ast.Constant)
        }

    def _site(self, node: ast.expr) -> Site:
        function = self._functions[-1] if self._functions else "<module>"
        return Site(line=node.lineno, function=function, text=ast.unparse(node))

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._functions.append(node.name)
        self.generic_visit(node)
        self._functions.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "subject_ref":
            self.scan.raw_reads.append(self._site(node))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        value = node.value
        if (
            isinstance(value, str)
            and id(node) not in self._docstrings
            and value.lower() in SPELLINGS
        ):
            holder = self._holders.get(id(node), "")
            self.scan.strings.append((self._site(node), holder, value))

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee(node.func)
        if name == "Principal" and any(kw.arg == "subject_ref" for kw in node.keywords):
            self.scan.keyword_args.append(self._site(node))
        elif name == "ScopeContext" and not _is_org_scope(node):
            self.scan.loose_contexts.append(self._site(node))
        elif name == "require":
            self.scan.requires.append((self._site(node), self._first_argument(node)))
        self.generic_visit(node)

    def _first_argument(self, call: ast.Call) -> str | None:
        """The permission a `require(...)` call names: a string literal as written, or a bare
        name resolved through this file's module-level `NAME = "..."` assignments."""
        argument: ast.expr | None = call.args[0] if call.args else None
        if argument is None:
            argument = next((kw.value for kw in call.keywords if kw.arg == "permission"), None)
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            return argument.value
        if isinstance(argument, ast.Name):
            return self._constants.get(argument.id)
        return None


def _is_org_scope(call: ast.Call) -> bool:
    """True only for a call that passes the literal `scope=Scope.ORG`. A `**kwargs` spread, a
    variable and every other rung are "not ORG": the scanner cannot see what they hold."""
    if any(kw.arg is None for kw in call.keywords):
        return False
    return any(kw.arg == "scope" and ast.unparse(kw.value) == "Scope.ORG" for kw in call.keywords)


def scan_source(source: str, filename: str = "<memory>") -> Scan:
    tree = ast.parse(source, filename=filename)
    scanner = _Scanner(tree)
    scanner.visit(tree)
    return scanner.scan


#: (rule, message). The rules are the ones the opted-in tests below select by name.
Finding = tuple[str, str]


def opt_in_findings(scan: Scan, relpath: str, product: str) -> list[Finding]:
    """Everything a file of an opted-in service is not allowed to contain."""
    found: list[Finding] = []
    for site in scan.raw_reads:
        found.append(
            (
                "read",
                f"{relpath}:{site.line} {site.function}: reads the employee link "
                f"(`{site.text}`); use `ctx.own_ref`",
            )
        )
    for site, holder, value in scan.strings:
        if value not in ALLOWED_STRING_SPELLINGS.get((relpath, holder), frozenset()):
            found.append(
                (
                    "string",
                    f"{relpath}:{site.line} {site.function}: the string {value!r} spells the "
                    f"employee link (held by {holder or 'no module-level name'})",
                )
            )
    for site in scan.keyword_args:
        found.append(
            ("keyword", f"{relpath}:{site.line} {site.function}: `{site.text}` passes subject_ref=")
        )
    for site in scan.loose_contexts:
        found.append(
            (
                "context",
                f"{relpath}:{site.line} {site.function}: `ScopeContext(...)` without the literal "
                "`scope=Scope.ORG` would silently use the employee link",
            )
        )
    for site, permission in scan.requires:
        if permission is None:
            found.append(
                (
                    "require",
                    f"{relpath}:{site.line} {site.function}: the permission passed to "
                    f"`{site.text}` cannot be resolved to a string",
                )
            )
        elif not permission.startswith(f"{product}."):
            found.append(
                (
                    "require",
                    f"{relpath}:{site.line} {site.function}: {permission!r} does not start with "
                    f"{product + '.'!r}, so `owner_subject_for` would miss it and this route "
                    "would stay on the employee link",
                )
            )
    return found


def all_services() -> list[str]:
    return sorted(path.name for path in SERVICES_ROOT.iterdir() if (path / "app").is_dir())


def service_files(service: str) -> list[Path]:
    return sorted((SERVICES_ROOT / service / "app").rglob("*.py"))


def scan_service(service: str) -> dict[str, Scan]:
    """relpath (to `services/`) -> scan, for every file of the service's `app` package."""
    return {
        str(path.relative_to(SERVICES_ROOT)): scan_source(
            path.read_text(encoding="utf-8"), filename=str(path)
        )
        for path in service_files(service)
    }


def raw_read_counts(services: tuple[str, ...]) -> dict[tuple[str, str], int]:
    """Raw reads of the employee link per (file, enclosing function), over `services`."""
    counts: Counter[tuple[str, str]] = Counter()
    for service in services:
        for relpath, scan in scan_service(service).items():
            counts.update((relpath, site.function) for site in scan.raw_reads)
    return dict(counts)


def string_spelling_counts(services: tuple[str, ...]) -> dict[tuple[str, str], int]:
    """String spellings of the employee link per (file, holding module-level name)."""
    counts: Counter[tuple[str, str]] = Counter()
    for service in services:
        for relpath, scan in scan_service(service).items():
            counts.update((relpath, holder) for _site, holder, _value in scan.strings)
    return dict(counts)


def registered_product(service: str) -> str | None:
    """The module-level `PRODUCT = "..."` of the service's `app/scope.py`: the key its resolver
    registers under, and so the prefix `owner_subject_for` looks permissions up by."""
    path = SERVICES_ROOT / service / "app" / "scope.py"
    if not path.exists():
        return None
    for name, value in _module_assignments(ast.parse(path.read_text(encoding="utf-8"))):
        if name == "PRODUCT" and isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def registration_subjects_in(source: str, filename: str = "<memory>") -> list[str]:
    """The `owner_subject=` of every `register_scope_resolver(...)` call in `source`, spelled as
    written; a call that omits it registers the default."""
    subjects: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Call) and _callee(node.func) == "register_scope_resolver":
            keywords = {kw.arg: ast.unparse(kw.value) for kw in node.keywords if kw.arg}
            subjects.append(keywords.get("owner_subject", DEFAULT_SUBJECT))
    return subjects


def registration_subjects(service: str) -> list[str]:
    """`registration_subjects_in`, over every file of the service."""
    return [
        subject
        for path in service_files(service)
        for subject in registration_subjects_in(path.read_text(encoding="utf-8"), str(path))
    ]


def services_registering_a_non_default_subject() -> set[str]:
    """Anything but a literal `OwnerSubject.EMPLOYEE_LINK` counts, so a subject the scanner cannot
    read (a variable, a call) puts its service in this set instead of hiding from the guard."""
    return {
        service
        for service in all_services()
        if any(subject != DEFAULT_SUBJECT for subject in registration_subjects(service))
    }


# ---------------------------------------------------------------------------------------------
# The scanner's own tests, on in-test source strings. "It flags every spelling" is only worth
# saying if a test can fail it.
# ---------------------------------------------------------------------------------------------

EVERY_SPELLING = """
from platform_core.auth import Principal, Scope, ScopeContext


def on_ctx(ctx):
    return ctx.subject_ref


def on_principal(ctx):
    return ctx.principal.subject_ref


def on_alias(ctx):
    held = ctx.principal
    return held.subject_ref


def by_getattr(ctx):
    return getattr(ctx, "subject_ref")


def by_subscript(claims):
    return claims["subject_ref"]


def by_claim(claims):
    return claims.get("sref")


def by_header(request):
    return request.headers["X-Subject-Ref"]


def by_lowercase_header(request):
    return request.headers.get("x-subject-ref")


def builds(principal):
    Principal(sub=1, organization_id=2, subject_ref=3)
    return ScopeContext(principal=principal, scope=Scope.OWN, permission="crm.book.company.read")
"""


class TestScannerSelfTests:
    def test_the_scanner_flags_every_spelling(self) -> None:
        scan = scan_source(EVERY_SPELLING)
        assert [site.function for site in scan.raw_reads] == ["on_ctx", "on_principal", "on_alias"]
        assert [site.function for site, _holder, _value in scan.strings] == [
            "by_getattr",
            "by_subscript",
            "by_claim",
            "by_header",
            "by_lowercase_header",
        ]
        assert [site.function for site in scan.keyword_args] == ["builds"]
        assert [site.function for site in scan.loose_contexts] == ["builds"]

    def test_a_read_is_attributed_to_its_innermost_enclosing_function(self) -> None:
        # The ratchet counts per function, so the attribution is what it stands on.
        scan = scan_source(
            "class Routes:\n"
            "    async def mine(self, ctx):\n"
            "        def inner():\n"
            "            return ctx.subject_ref\n"
            "        return ctx.subject_ref, inner\n"
            "\n"
            "LATE = lambda ctx: ctx.subject_ref\n"
        )
        assert [site.function for site in scan.raw_reads] == ["inner", "mine", "<module>"]

    def test_the_scanner_ignores_docstrings_and_comments(self) -> None:
        scan = scan_source(
            '''
"""The rep id is subject_ref, claimed as sref, forwarded as X-Subject-Ref."""


class Resolver:
    """OWN compares `subject_ref`."""

    def own(self, ctx):
        """Uses `ctx.subject_ref`."""
        # ctx.subject_ref is only mentioned in this comment
        return 1
'''
        )
        assert scan == Scan()

    def test_the_scanner_accepts_own_ref_and_org_contexts(self) -> None:
        scan = scan_source(
            """
def mine(ctx):
    return ctx.own_ref


def synthetic(principal):
    return ScopeContext(principal=principal, scope=Scope.ORG, permission="crm.book.company.write")
"""
        )
        assert scan == Scan()

    def test_a_scope_context_the_scanner_cannot_read_is_not_org(self) -> None:
        scan = scan_source(
            """
def spread(principal, **rest):
    return ScopeContext(principal=principal, **rest)


def variable(principal, scope):
    return ScopeContext(principal=principal, scope=scope, permission="crm.x.y.z")


def omitted(principal):
    return ScopeContext(principal=principal, permission="crm.x.y.z")
"""
        )
        assert [site.function for site in scan.loose_contexts] == ["spread", "variable", "omitted"]

    def test_the_scanner_resolves_a_module_constant_passed_to_require(self) -> None:
        wrong_product = scan_source(
            'REVEAL = "lead.lead.read_sensitive"\nguard = require(REVEAL, amr={"otp", "stepup"})\n'
        )
        findings = opt_in_findings(wrong_product, "lead/app/api/routes.py", "crm")
        assert [rule for rule, _message in findings] == ["require"]
        assert "'lead.lead.read_sensitive'" in findings[0][1]

        right_product = scan_source(
            'REVEAL: Final = "crm.lead.lead.read_sensitive"\n'
            'guard = require(REVEAL, amr={"otp", "stepup"})\n'
            'literal = require("crm.lead.lead.read")\n'
            'keyword = require(permission="crm.lead.lead.write", minimum=Scope.ORG)\n'
        )
        assert [permission for _site, permission in right_product.requires] == [
            "crm.lead.lead.read_sensitive",
            "crm.lead.lead.read",
            "crm.lead.lead.write",
        ]
        assert opt_in_findings(right_product, "lead/app/api/routes.py", "crm") == []

    def test_an_unresolvable_require_argument_is_flagged(self) -> None:
        for source in (
            "guard = require(permission_for(kind))",
            "guard = require(settings.permission)",
            "guard = require(IMPORTED_FROM_ELSEWHERE)",
            'guard = require(f"crm.{kind}.read")',
            "guard = require()",
        ):
            findings = opt_in_findings(scan_source(source), "lead/app/api/routes.py", "crm")
            assert [rule for rule, _message in findings] == ["require"], source
            assert "cannot be resolved" in findings[0][1], source

    def test_an_allow_listed_string_under_another_file_or_name_is_flagged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        held = '_IDENTITY_HEADERS = ("Authorization", "X-Subject-Ref")\n'
        excused_file = "customer/app/c1_360.py"
        # The allow-list is empty today; give it one entry so the matching itself is under test.
        monkeypatch.setitem(
            ALLOWED_STRING_SPELLINGS,
            (excused_file, "_IDENTITY_HEADERS"),
            frozenset({"X-Subject-Ref"}),
        )

        # The one place it is excused: that file, held by that name, that exact spelling.
        assert opt_in_findings(scan_source(held), excused_file, "crm") == []

        elsewhere = {
            "another file": (held, "deal/app/c1_360.py"),
            "another name": (
                '_FORWARDED_HEADERS = ("Authorization", "X-Subject-Ref")\n',
                excused_file,
            ),
            "inside a function": (
                'def f():\n    return ("Authorization", "X-Subject-Ref")\n',
                excused_file,
            ),
            "another spelling": ('_IDENTITY_HEADERS = ("Authorization", "sref")\n', excused_file),
        }
        for label, (source, relpath) in elsewhere.items():
            findings = opt_in_findings(scan_source(source), relpath, "crm")
            assert [rule for rule, _message in findings] == ["string"], label

    def test_the_registration_scanner_reads_the_subject_as_written(self) -> None:
        subjects = registration_subjects_in(
            "register_scope_resolver(PRODUCT, Resolver, owner_subject=OwnerSubject.MEMBERSHIP)\n"
            "register_scope_resolver(PRODUCT, Resolver)\n"
            "auth.register_scope_resolver(PRODUCT, Resolver, owner_subject=chosen)\n"
        )
        assert subjects == ["OwnerSubject.MEMBERSHIP", DEFAULT_SUBJECT, "chosen"]

    def test_a_subject_the_scanner_cannot_read_is_not_the_default(self) -> None:
        # So a registration that hides its subject behind a call or a variable puts its service in
        # the set `OPTED_IN` must equal, instead of escaping the guard.
        subjects = registration_subjects_in("register_scope_resolver(P, R, owner_subject=f())")
        assert subjects == ["f()"]
        assert subjects != [DEFAULT_SUBJECT]


# ---------------------------------------------------------------------------------------------
# The rules, on the services that have opted in. Empty in R2.2, so these hold trivially until
# R2.7 fills `OPTED_IN`: that is what the self-tests above and the ratchet below are for.
# ---------------------------------------------------------------------------------------------


class TestOptedInServices:
    @staticmethod
    def _findings(*rules: str) -> list[str]:
        found: list[str] = []
        for service in sorted(OPTED_IN):
            product = registered_product(service)
            assert product is not None, f"{service} is opted in but app/scope.py sets no PRODUCT"
            for relpath, scan in scan_service(service).items():
                found += [m for rule, m in opt_in_findings(scan, relpath, product) if rule in rules]
        return found

    def test_every_opted_in_name_is_a_service(self) -> None:
        assert OPTED_IN.issubset(all_services())

    def test_an_opted_in_service_never_reads_the_employee_link(self) -> None:
        assert self._findings("read", "string", "keyword") == []

    def test_hand_built_contexts_in_an_opted_in_service_are_org_scope(self) -> None:
        assert self._findings("context") == []

    def test_every_permission_an_opted_in_service_requires_starts_with_its_product(self) -> None:
        assert self._findings("require") == []

    def test_the_opted_in_set_is_exactly_the_set_of_services_that_register_the_membership_subject(
        self,
    ) -> None:
        # DD-7. The guard is tied to the registrations: a service cannot opt in and escape the
        # rules above, and cannot sit in `OPTED_IN` without having opted in.
        assert services_registering_a_non_default_subject() == set(OPTED_IN)


# ---------------------------------------------------------------------------------------------
# The three CRM services today. These do not wait for `OPTED_IN`.
# ---------------------------------------------------------------------------------------------


class TestAllowList:
    """Permanent. R2.7 keeps this class when it deletes the ratchet."""

    def test_every_string_spelling_in_the_crm_services_is_allow_listed(self) -> None:
        unexcused = [
            (relpath, holder, value)
            for service in CRM_SERVICES
            for relpath, scan in scan_service(service).items()
            for _site, holder, value in scan.strings
            if value not in ALLOWED_STRING_SPELLINGS.get((relpath, holder), frozenset())
        ]
        assert unexcused == []

    def test_the_allow_listed_file_and_name_still_hold_the_string(self) -> None:
        # A renamed constant would leave the allow-list excusing nothing and the guard blind to
        # the move; fail on the stale entry instead.
        for (relpath, holder), spellings in ALLOWED_STRING_SPELLINGS.items():
            scan = scan_source((SERVICES_ROOT / relpath).read_text(encoding="utf-8"))
            held = {(name, value) for _site, name, value in scan.strings}
            assert {(holder, spelling) for spelling in spellings} <= held


class TestScansAreNotVacuous:
    def test_the_scan_is_not_vacuous(self) -> None:
        for service in sorted({*OPTED_IN, *CRM_SERVICES}):
            assert len(service_files(service)) >= 5, service

    def test_the_crm_services_register_under_the_crm_product(self) -> None:
        # The key `owner_subject_for` looks a permission's product up by.
        for service in CRM_SERVICES:
            assert registered_product(service) == "crm", service

    def test_the_registration_scan_sees_each_crm_service_register_once(self) -> None:
        # If the scan stopped finding `register_scope_resolver(...)` calls, the tie to `OPTED_IN`
        # would hold for the wrong reason: an empty set equal to an empty set.
        for service in CRM_SERVICES:
            assert len(registration_subjects(service)) == 1, service


# ---------------------------------------------------------------------------------------------
# The ratchet. R2.7 deletes this class, `KNOWN_RAW_READS`, `KNOWN_STRING_SPELLINGS`, the two
# counting helpers and the `Counter` import, and fills `OPTED_IN`; everything above stays.
# ---------------------------------------------------------------------------------------------


class TestRatchetUntilTheFlip:
    def test_the_known_raw_reads_are_exactly_the_documented_six(self) -> None:
        counts = raw_read_counts(CRM_SERVICES)
        assert counts == KNOWN_RAW_READS
        assert sum(counts.values()) == 6

    def test_lead_reads_the_employee_link_nowhere(self) -> None:
        assert raw_read_counts(("lead",)) == {}

    def test_the_crm_services_spell_the_employee_link_as_no_string(self) -> None:
        counts = string_spelling_counts(CRM_SERVICES)
        assert counts == KNOWN_STRING_SPELLINGS
        assert sum(counts.values()) == 0
