"""A repo-wide static guard for D3: a network call must never run while a database transaction
is open (it holds a pool connection — and often a row lock — for however long a slow or
unreachable provider takes, which is how a handful of stuck requests exhaust a 3+2 pool).

**What this scans for.** Every service speaks to the outside world through a small, named seam —
a `Protocol` a route or a sweep holds and calls (`IrpClient`, `EwayClient`, `ImsClient`,
`PaymentProvider`, `ProductRegistry`, `ObjectStore`, `Resolver`, `ProbeSet`, `HierarchySource`;
`CLIENT_TYPE_NAMES` below). A function is **flagged** when it is called — directly, or through
one named function that calls it, one hop at a time, transitively — from somewhere lexically
inside a transaction: `async with session.begin():`, `async with session_scope(...):`, or a
sweep's own `self._work_scope(...)` / `work_scope(...)`.

**How the resolution works, and its real limits — read this before trusting a clean run.**

1. Every function whose signature carries a parameter annotated with one of `CLIENT_TYPE_NAMES`
   is checked for a call on that parameter (`client.register(...)`, `probes.ownership.check(...)`
   — matched by the call's unparsed source starting with `"{param}."`, so a chain through an
   attribute of the parameter is still caught). Such a function is a **direct** network caller.
2. Every function's body is also scanned for every attribute or bare-name call it makes
   (`services.cancel_filing(...)` records `cancel_filing`; `advance_domain_claim(...)` records
   `advance_domain_claim`) — a deliberately coarse over-approximation of "what this function
   calls", resolved **by bare name only, not by module** (`services.py` in two different
   directories sharing a function name is one real, accepted false-positive/negative risk;
   services do not currently collide on the names this matters for, and `ALLOWLIST` is the place
   to name it if one ever does).
3. Fixed-point closure: a function that calls a name known (by step 1 or a previous iteration of
   this step) to reach the network is itself marked as reaching the network. Six iterations is
   generously more than this codebase's deepest call chain from a route to a provider.
4. The scan then walks every `with`/`async with` block whose context expression's unparsed source
   contains one of `TRANSACTION_MARKERS`, and flags every call inside it whose resolved name
   reaches the network.

**What this cannot see, honestly:** a provider called through a variable holding a function
reference rather than a name (`fn = services.cancel_filing; fn(...)`), a genuinely new provider
seam whose `Protocol` name is not yet in `CLIENT_TYPE_NAMES`, or two functions of the same name in
different services where one is a network caller and the other merely shadows its name (call-graph
resolution here is by bare name, not by module — see point 2). It is a best-effort net, not a
type checker; keep `ALLOWLIST` reviewable rather than trying to make the scanner perfect.

**The allow-list is not a rubber stamp.** Every entry names an instance this sweep (D3) found and
did not fix, with the shape of the fix it needs — see the comments beside each. New entries need
the same.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_APP_ROOTS = sorted((REPO_ROOT / "services").glob("*/app"))
#: `get_tenant_session` and `session_scope` — the chokepoints most routes take their committing
#: dependency *from* — are defined centrally, not per service. Parsed for the call-graph and the
#: committing-dependency scan alongside every service; not itself a source of findings, since
#: nothing here holds a `Protocol`-typed provider parameter.
PLATFORM_CORE_ROOT = REPO_ROOT / "libs" / "platform-core" / "src"

#: The Protocol/class names this platform's provider seams are typed as. A function taking a
#: parameter annotated with one of these is assumed to be able to reach the network through it.
CLIENT_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "IrpClient",
        "EwayClient",
        "ImsClient",
        "PaymentProvider",
        "ProductRegistry",
        "ObjectStore",
        "Resolver",
        "ProbeSet",
        "HierarchySource",
        # auth's OIDC discovery / token exchange seam (app/oidc.py).
        "IdpClient",
    }
)

#: Substrings of a `with`/`async with` context expression's unparsed source that mark it as
#: transaction-opening. Deliberately substrings, not exact matches: `session.begin()`,
#: `session_scope(organization_id=org)`, `self._work_scope(organization_id)` and
#: `admin.begin()` (a differently-named session variable) all contain one of these.
TRANSACTION_MARKERS: tuple[str, ...] = (".begin(", "session_scope(", "_work_scope(")

#: `(relative/path/to/file.py, enclosing_function_name)` → why this instance is known, found by
#: this sweep, and deliberately not fixed here. Every entry must correspond to a real finding in
#: the report this test's own module docstring points at; an entry that no longer reproduces
#: (the function was fixed, renamed, or removed) fails `test_the_allowlist_has_no_stale_entries`.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("services/finance/app/api/internal.py", "cancel_filing"): (
        "D3 sweep: calls services.cancel_filing (client.cancel(...)) inside one "
        "session.begin(). Same claim/call/record split as file_invoice in the same file — "
        "stage the cancellation intent, call with no transaction, record the withdrawal row."
    ),
    ("services/finance/app/api/internal.py", "generate_eway_bill"): (
        "D3 sweep: calls services.generate_eway_bill (client.generate(...)) inside one "
        "session.begin(). Same split as file_invoice."
    ),
    ("services/finance/app/api/internal.py", "cancel_eway_bill"): (
        "D3 sweep: calls services.cancel_eway_bill (client.cancel(...)) inside one "
        "session.begin(). Same split as file_invoice."
    ),
    ("services/finance/app/api/internal.py", "sync_ims"): (
        "D3 sweep: calls services.ingest_ims_documents (client.fetch(...)) inside one "
        "session.begin(). Same split as file_invoice: fetch the period with no transaction "
        "open, then ingest and reconcile in a second transaction."
    ),
    ("services/catalog/app/api/admin.py", "create_version"): (
        "D3 sweep: calls services.create_version, which calls registry.products() (an httpx "
        "request to product-service) inside get_operator_session's request-long transaction. "
        "Fix: resolve the registry read in its own dependency with no session at all, before "
        "the tenant session is opened, and pass the already-fetched product map into "
        "create_version instead of the registry client — there is nothing in the bundle "
        "validation that needs a transaction open around the read."
    ),
    ("services/document/app/api/routes.py", "upload_asset"): (
        "D3 sweep: calls services.create_asset, which calls store.put(...) (an S3 PUT, via "
        "anyio.to_thread) inside get_tenant_session's request-long transaction. Fix: check the "
        "quota (a read) before opening the transaction, PUT the object with no session held, "
        "then open the transaction to insert the Asset row and audit."
    ),
    ("services/document/app/api/routes.py", "delete_asset"): (
        "D3 sweep: the `purge=true` branch calls services.remove_asset, which calls "
        "store.delete(...) (an S3 DELETE) inside get_tenant_session's transaction. Same fix "
        "shape as upload_asset: delete the object with no session held, then open the "
        "transaction to mark the row purged and audit."
    ),
    ("services/document/app/cron.py", "_purge"): (
        "D3 sweep: the retention sweep's own per-asset step calls services.purge_asset, which "
        "calls store.delete(...) inside its own session_scope — the sweep's counterpart to "
        "delete_asset's purge branch above, same fix shape."
    ),
    ("services/catalog/app/bundle_audit.py", "main"): (
        "D3 sweep: a standalone operator CLI (not a live request path) opens one "
        "session.begin() and calls audit(), which calls registry.products() inside it. Lower "
        "severity than the request paths above — nothing else contends for this connection — "
        "but the same fix applies: fetch the registry first, open the transaction after."
    ),
    ("services/storefront/app/api/routes.py", "check_domain"): (
        "D3 sweep: found, not fixed — already identified and deliberately deferred by an "
        "earlier review (see services.run_domain_check's own docstring: 'Splitting the probes "
        "out of the transaction entirely ... is the fuller fix'). Fix: lock and re-verify the "
        "claim, release the lock, probe with no transaction, re-lock to record."
    ),
    ("services/storefront/app/cron.py", "_advance_one"): (
        "D3 sweep: the daily sweep's own per-organization step calls advance_domain_claim "
        "(the DNS/TLS probes) inside session_scope, the same instance check_domain's docstring "
        "names for the synchronous 'Check now' path. Same fix shape."
    ),
    # auth is owned by another slice (excluded from this sweep's fixes per the task brief) —
    # found and reported, not touched.
    ("services/auth/app/api/routes.py", "save_sso_config"): (
        "D3 sweep, routed to the auth slice: services.save_sso_config takes an "
        "oidc.IdpClient and performs OIDC discovery over HTTP inside the request's "
        "tenant-bound transaction. Fix: run discovery (and validate the returned endpoints) "
        "in an untransacted step first, then open the transaction only to seal the secret, "
        "write the config row, audit and stage the event."
    ),
    # subscription is owned by another slice (excluded from this sweep's fixes per the task
    # brief) — found and reported, not touched. Both routes resolve a plan_version_id against
    # catalog's service-token internal API (app/config.py's own docstring: "a subscribe/
    # change-plan resolves its plan_version_id against catalog ... before pinning it") inside
    # the request's tenant-bound transaction. Fix: resolve the pin before opening the
    # transaction (or in its own untransacted step), then open it only to write the
    # subscription/change.
    ("services/subscription/app/api/routes.py", "create_subscription"): (
        "D3 sweep, routed to the subscription slice: calls services.subscribe, which pins the "
        "plan version against catalog over HTTP inside the request's transaction."
    ),
    ("services/subscription/app/api/routes.py", "change_plan"): (
        "D3 sweep, routed to the subscription slice: calls services.change_plan, same pin "
        "lookup as create_subscription."
    ),
    ("services/subscription/app/api/internal.py", "subscribe_as_operator"): (
        "D3 sweep, routed to the subscription slice: the operator-console path to the same "
        "subscribe/pin call, same instance."
    ),
    ("services/subscription/app/api/internal.py", "change_plan_as_operator"): (
        "D3 sweep, routed to the subscription slice: the operator-console path to the same "
        "change-plan/pin call, same instance."
    ),
}


@dataclass
class FunctionInfo:
    name: str
    file: Path
    node: ast.FunctionDef | ast.AsyncFunctionDef
    param_types: dict[str, str] = field(default_factory=dict)


def _python_files() -> list[Path]:
    files: list[Path] = []
    for app_root in [*SERVICE_APP_ROOTS, PLATFORM_CORE_ROOT]:
        for path in app_root.rglob("*.py"):
            parts = set(path.parts)
            if "tests" in parts or "migrations" in parts or ".venv" in parts:
                continue
            files.append(path)
    return files


def _parse_all() -> dict[Path, ast.Module]:
    trees = {}
    for path in _python_files():
        try:
            trees[path] = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:  # pragma: no cover - defensive; every file here must parse
            continue
    return trees


def _collect_functions(trees: dict[Path, ast.Module]) -> list[FunctionInfo]:
    functions: list[FunctionInfo] = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            param_types: dict[str, str] = {}
            for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                if arg.annotation is None:
                    continue
                annotation = ast.unparse(arg.annotation)
                # Word-boundary match, not substring: `notification.app.targets.TargetResolver`
                # must not match `Resolver` (`dns_check.Resolver`) just because it ends with the
                # same six letters.
                if any(
                    re.search(rf"\b{re.escape(name)}\b", annotation) for name in CLIENT_TYPE_NAMES
                ):
                    param_types[arg.arg] = annotation
            functions.append(
                FunctionInfo(name=node.name, file=path, node=node, param_types=param_types)
            )
    return functions


def _safe_unparse(node: ast.AST) -> str | None:
    """`ast.unparse` on a node built from a handful of well-formed service files should never
    fail, but this scan is a best-effort net (see the module docstring), not a compiler — a
    node shaped in a way `unparse` cannot render is skipped rather than allowed to crash the
    whole scan over one file."""
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _calls_in(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _opens_a_transaction_and_yields(node: ast.AsyncFunctionDef) -> bool:
    """The same predicate `tests/infra/test_session_dependency_scope.py` uses to recognise
    `get_tenant_session` and its per-service siblings (`get_operator_session`, the internal
    routes' own `_bind`-and-`session.begin()` idiom's dependency-shaped cousins): an
    async-generator FastAPI dependency that opens a transaction and yields inside it. Every
    route that takes one of these has its **entire body** run inside that one transaction — no
    explicit `with` block appears in the route's own source, because FastAPI's dependency
    injection is what opens it. Missing this pattern would miss most of the real route-level D3
    instances this scan exists to find."""
    yields = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(node))
    begins = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "begin"
        for n in ast.walk(node)
    )
    return yields and begins


def _committing_dependency_names(trees: dict[Path, ast.Module]) -> set[str]:
    names: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and _opens_a_transaction_and_yields(node):
                names.add(node.name)
    return names


def _di_wrapped_aliases(
    trees: dict[Path, ast.Module], committing: set[str]
) -> dict[Path, set[str]]:
    """Every name — a `Depends(...)`-carrying `Annotated` alias (`SessionDep = Annotated[...,
    Depends(get_tenant_session, ...)]`), or a committing dependency's own name used directly —
    that marks a parameter annotation as "this parameter's presence means the whole function
    runs inside a transaction opened elsewhere". **Scoped per file, deliberately**: `SessionDep`
    is the near-universal name for this alias across services, and a service whose own
    `SessionDep` wraps the *non*-committing `get_session` (`app/api/internal.py` in payment and
    finance, both restructured for D3) must not inherit another service's `SessionDep` meaning
    just because the bare name matches — the same name-collision risk the module docstring
    names for call-graph resolution, closed here properly because doing so is cheap.
    """
    aliases: dict[Path, set[str]] = {}
    for path, tree in trees.items():
        file_aliases = set(committing)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            source = _safe_unparse(node.value)
            if source is None:
                continue
            if not any(f"Depends({name}" in source for name in committing):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    file_aliases.add(target.id)
        aliases[path] = file_aliases
    return aliases


def _is_di_transaction_wrapped(func: FunctionInfo, aliases: dict[Path, set[str]]) -> bool:
    file_aliases = aliases.get(func.file, set())
    if not file_aliases:
        return False
    for arg in func.node.args.args + func.node.args.kwonlyargs + func.node.args.posonlyargs:
        if arg.annotation is None:
            continue
        annotation = _safe_unparse(arg.annotation)
        if annotation is None:
            continue
        if any(alias in annotation for alias in file_aliases):
            return True
    return False


#: Methods on an otherwise network-facing seam that are documented, pure, in-process
#: computation — a provider's HMAC signature check and its JSON parsing, an object store
#: building a URL string from a key it already has, never a socket. Direct calls to these must
#: not seed `_is_direct_network_call`: `payment.services.ingest_webhook` takes a `provider:
#: PaymentProvider` and calls exactly the first two, and `document`'s `_to_out` takes a `store:
#: ObjectStore` only to read `store.public_url(...)` back into the response body it renders for
#: every read route — treating either as a network caller would be wrong about the two
#: functions in this platform whose own docstrings (or, for `public_url`, plain signature —
#: `def`, not `async def`) most clearly say the call they make is not one.
PURE_SEAM_METHODS: frozenset[str] = frozenset({"verify_webhook", "parse_webhook", "public_url"})


def _is_direct_network_call(func: FunctionInfo) -> bool:
    if not func.param_types:
        return False
    for call in _calls_in(func.node):
        source = _safe_unparse(call.func)
        if source is None:
            continue
        for p in func.param_types:
            if source == p and isinstance(call.func, ast.Name):
                return True
            if source.startswith(f"{p}."):
                method = source[len(p) + 1 :]
                if method not in PURE_SEAM_METHODS:
                    return True
    return False


def _called_names(func: FunctionInfo) -> set[str]:
    names: set[str] = set()
    for call in _calls_in(func.node):
        if isinstance(call.func, ast.Attribute):
            names.add(call.func.attr)
        elif isinstance(call.func, ast.Name):
            names.add(call.func.id)
    return names


def _service_of(path: Path) -> str:
    """Which service a file belongs to, or `"platform-core"` for the shared library.

    The unit call-graph resolution is scoped to, below — **not** the whole repo. `create_asset`,
    `get_asset`, `update_asset`, `delete_asset` and `list_assets` each name a function in
    *document* (real assets, in object storage) **and** an unrelated one in *itsm* (ticket
    attachments, no object store at all); resolving "does this bare name reach the network" once
    across the entire repo made every itsm asset route a false positive the moment document's
    same-named function genuinely reached it. Per-service scoping is what the module docstring's
    "services do not currently collide on the names this matters for" was supposed to mean, made
    true rather than merely asserted.
    """
    parts = path.relative_to(REPO_ROOT).parts
    if parts[0] == "services":
        return parts[1]
    return "platform-core"


def _reaches_network(functions: list[FunctionInfo]) -> dict[str, set[str]]:
    """Every bare function name known to reach the network, directly or transitively —
    **per service** (see `_service_of`). Within one service, `create_version`, `cancel_filing`,
    `generate_eway_bill` each still name **two** functions (the route and the `services.py`
    function it delegates to), so every function sharing a name within that service must have
    its called-names **union**ed, never one overwriting another's entry.
    """
    by_service: dict[str, list[FunctionInfo]] = {}
    for f in functions:
        by_service.setdefault(_service_of(f.file), []).append(f)

    result: dict[str, set[str]] = {}
    for service, service_functions in by_service.items():
        reaches = {f.name for f in service_functions if _is_direct_network_call(f)}
        called_names: dict[str, set[str]] = {}
        for f in service_functions:
            called_names.setdefault(f.name, set()).update(_called_names(f))
        for _ in range(6):
            changed = False
            for name, callees in called_names.items():
                if name in reaches:
                    continue
                if callees & reaches:
                    reaches.add(name)
                    changed = True
            if not changed:
                break
        result[service] = reaches
    return result


def _is_transaction_context(context_expr: ast.expr) -> bool:
    source = _safe_unparse(context_expr)
    if source is None:
        return False
    return any(marker in source for marker in TRANSACTION_MARKERS)


@dataclass(frozen=True, slots=True)
class Finding:
    file: str
    function: str
    line: int
    callee: str


def _callee_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _find_violations(
    functions: list[FunctionInfo],
    reaches: dict[str, set[str]],
    di_aliases: dict[Path, set[str]],
) -> list[Finding]:
    findings: dict[tuple[str, str, int, str], Finding] = {}

    def _record(func: FunctionInfo, call: ast.Call) -> None:
        callee = _callee_name(call)
        if callee is None or callee not in reaches.get(_service_of(func.file), set()):
            return
        finding = Finding(
            file=str(func.file.relative_to(REPO_ROOT)),
            function=func.name,
            line=call.lineno,
            callee=callee,
        )
        findings[(finding.file, finding.function, finding.line, finding.callee)] = finding

    for func in functions:
        # FastAPI's dependency-injection chokepoint: no explicit `with` appears in the route's
        # own source, but a parameter typed through a committing dependency (`SessionDep`,
        # `get_operator_session`'s alias, ...) means the whole body runs inside a transaction
        # that dependency opened before the handler ran and commits after it returns.
        if _is_di_transaction_wrapped(func, di_aliases):
            for call in _calls_in(func.node):
                _record(func, call)
            continue

        # Everything else: an explicit `async with session.begin():` / `session_scope(...)` /
        # `self._work_scope(...)` written directly in the function's own source.
        for node in ast.walk(func.node):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            if not any(_is_transaction_context(item.context_expr) for item in node.items):
                continue
            for call in _calls_in(node):
                _record(func, call)

    return sorted(findings.values(), key=lambda f: (f.file, f.line))


def _run_scan() -> list[Finding]:
    trees = _parse_all()
    functions = _collect_functions(trees)
    reaches = _reaches_network(functions)
    committing = _committing_dependency_names(trees)
    di_aliases = _di_wrapped_aliases(trees, committing)
    return _find_violations(functions, reaches, di_aliases)


def test_the_scanner_recognises_a_known_direct_network_caller() -> None:
    """Guards the guard: if the type-annotation match stopped working, every assertion below
    would pass vacuously. `stage_registration_attempt` builds the payload but never calls the
    client — `call_irp_register` is the one function in finance that actually does."""
    trees = _parse_all()
    functions = _collect_functions(trees)
    names = {f.name for f in functions if _is_direct_network_call(f)}
    assert "call_irp_register" in names
    assert "call_provider_charge" in names


def test_no_network_call_reachable_from_inside_a_transaction_is_unlisted() -> None:
    findings = _run_scan()
    offenders = [f for f in findings if (f.file, f.function) not in ALLOWLIST]
    assert not offenders, (
        "a function reachable from inside a transaction calls a known provider seam:\n"
        + "\n".join(f"{f.file}:{f.line} in {f.function}() -> {f.callee}(...)" for f in offenders)
        + "\n\nEither restructure it (claim / call with no transaction / record — see D3), or "
        "add it to ALLOWLIST in this file with the exact fix it needs."
    )


def test_the_allowlist_has_no_stale_entries() -> None:
    """An allow-list entry for an instance that was fixed (or removed) is a false sense of
    coverage — the scan would no longer flag it even without the entry, so its presence hides
    nothing real. Every entry must still reproduce."""
    findings = {(f.file, f.function) for f in _run_scan()}
    stale = sorted(key for key in ALLOWLIST if key not in findings)
    assert not stale, (
        f"these ALLOWLIST entries no longer reproduce a finding — remove them: {stale}"
    )
