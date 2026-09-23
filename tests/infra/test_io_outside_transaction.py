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

Round 3 (D3) closed the gaps a review demonstrated: calls through `from x import f as g`
aliases are resolved; a function reference **passed as an argument** (`asyncio.to_thread(fn, …)`,
`off_loop(fn, …)`) counts as a call to it; a local bound to a seam parameter, to an `or` of one,
or to a no-argument `get_*()` / `_default_*()` factory (`r = resolver or _default_resolver()`) is
a seam; annotations are matched by exact identifier (`SessionDep` is not `RawSessionDep`); and an
`async with factory() as session:` block is a transaction from its first query on (SQLAlchemy's
autobegin), not only a block that says `.begin()`.

**What this still cannot see, honestly:** a provider called through a variable holding a
function reference assigned elsewhere (`fn = services.cancel_filing; fn(...)`), a genuinely new
provider seam whose type name is not yet in `CLIENT_TYPE_NAMES`, or two functions of the same name
in one service where one is a network caller and the other merely shadows its name (call-graph
resolution is by bare name within a service — see point 2). It is a best-effort net, not a type
checker; keep `ALLOWLIST` reviewable rather than trying to make the scanner perfect.

**The allow-list is not a rubber stamp.** Every entry names an instance this sweep (D3) found and
did not fix, with the shape of the fix it needs — see the comments beside each. New entries need
the same. The `#N` in each reason is the instance's row in `docs/io-outside-transactions.md`,
which also lists what was fixed (and so is deliberately absent here: the stale-entry test fails
the moment a fixed instance is left on the list).
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
        # notification's outbound message port (app/transports.py: SMTP, WhatsApp Cloud).
        "Transport",
        # Any raw httpx client handed to a function: notification's WhatsApp ownership check
        # (app/whatsapp_provider.py:verify_ownership) and its membership client
        # (app/memberships.py:membership_is_active) take one, and so does anything else that
        # speaks HTTP to another service without a Protocol of its own.
        "AsyncClient",
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
    ("services/catalog/app/api/admin.py", "create_version"): (
        "D3 #10: calls services.create_version, which calls registry.products() (an httpx "
        "request to product-service) inside get_operator_session's request-long transaction. "
        "Fix: resolve the registry read in its own dependency with no session at all, before "
        "the tenant session is opened, and pass the already-fetched product map into "
        "create_version instead of the registry client — there is nothing in the bundle "
        "validation that needs a transaction open around the read."
    ),
    ("services/document/app/api/routes.py", "upload_asset"): (
        "D3 #12: calls services.create_asset, which calls store.put(...) (an S3 PUT, via "
        "anyio.to_thread) inside get_tenant_session's request-long transaction. Fix: check the "
        "quota (a read) before opening the transaction, PUT the object with no session held, "
        "then open the transaction to insert the Asset row and audit."
    ),
    ("services/document/app/api/routes.py", "delete_asset"): (
        "D3 #13: the `purge=true` branch calls services.remove_asset, which calls "
        "store.delete(...) (an S3 DELETE) inside get_tenant_session's transaction. Same fix "
        "shape as upload_asset: delete the object with no session held, then open the "
        "transaction to mark the row purged and audit."
    ),
    ("services/document/app/cron.py", "_purge"): (
        "D3 #14: the retention sweep's own per-asset step calls services.purge_asset, which "
        "calls store.delete(...) inside its own session_scope — the sweep's counterpart to "
        "delete_asset's purge branch above, same fix shape."
    ),
    ("services/catalog/app/bundle_audit.py", "main"): (
        "D3 #11: a standalone operator CLI (not a live request path) opens one "
        "session.begin() and calls audit(), which calls registry.products() inside it. Lower "
        "severity than the request paths above — nothing else contends for this connection — "
        "but the same fix applies: fetch the registry first, open the transaction after."
    ),
    ("services/storefront/app/api/routes.py", "check_domain"): (
        "D3 #15: found, not fixed — already identified and deliberately deferred by an "
        "earlier review (see services.run_domain_check's own docstring: 'Splitting the probes "
        "out of the transaction entirely ... is the fuller fix'). Fix: lock and re-verify the "
        "claim, release the lock, probe with no transaction, re-lock to record."
    ),
    ("services/storefront/app/cron.py", "_advance_one"): (
        "D3 #16: the daily sweep's own per-organization step calls advance_domain_claim "
        "(the DNS/TLS probes) inside session_scope, the same instance check_domain's docstring "
        "names for the synchronous 'Check now' path. Same fix shape."
    ),
    # auth is owned by another slice (excluded from this sweep's fixes per the task brief) —
    # found and reported, not touched.
    ("services/auth/app/api/routes.py", "save_sso_config"): (
        "D3 #18, routed to the auth slice: services.save_sso_config takes an "
        "oidc.IdpClient and performs OIDC discovery over HTTP inside the request's "
        "tenant-bound transaction. Fix: run discovery (and validate the returned endpoints) "
        "in an untransacted step first, then open the transaction only to seal the secret, "
        "write the config row, audit and stage the event."
    ),
    # notification's Engage and WhatsApp code is owned by already-merged slices — found by the
    # round-3 scanner (the `Transport` / `AsyncClient` seams, seam locals, import aliases), routed
    # there, not changed here.
    ("services/notification/app/api/engage.py", "send_test_message"): (
        "D3 #19, routed to notification: services.send_test sends a real message through the "
        "sender's Transport (SMTP) inside get_tenant_session's request-long transaction, and the "
        "caller waits on the provider's answer by design. Fix: claim (rate limit, load sender "
        "and connection, commit), send with no transaction open, record the receipt and flip "
        "the sender usable in a second short transaction."
    ),
    ("services/notification/app/api/engage.py", "connect_whatsapp"): (
        "D3 #20, routed to notification (WhatsApp slice): create_whatsapp_connection calls "
        "whatsapp_provider.verify_ownership (two Graph API GETs over httpx) inside the request's "
        "transaction. Fix: verify ownership before the transaction opens (it reads nothing from "
        "the database), then open it only to seal the token and write the connection."
    ),
    ("services/notification/app/api/engage.py", "replace_whatsapp"): (
        "D3 #20, routed to notification (WhatsApp slice): replace_whatsapp_connection, same "
        "verify_ownership call and same fix as connect_whatsapp."
    ),
    ("services/notification/app/api/engage.py", "assign_conversation"): (
        "D3 #20, routed to notification (Engage inbox): services.assign_conversation asks "
        "account over HTTP (memberships.membership_is_active) inside the request's "
        "transaction. Fix: resolve the membership before the transaction opens, then open it to "
        "lock the conversation and write the assignment."
    ),
    ("services/notification/app/api/engage.py", "connect_provider"): (
        "D3 #24, routed to notification: create_connection's dial guard "
        "(dns_check.assert_dialable -> resolve_public_addresses) resolves the customer's SMTP "
        "host over DNS inside the request's transaction. Fix: resolve and vet the host before "
        "the transaction opens; the transaction only seals the credentials and writes the row."
    ),
    ("services/notification/app/api/engage.py", "replace_provider"): (
        "D3 #24, routed to notification: replace_connection, same dial-guard DNS resolution "
        "and same fix as connect_provider."
    ),
    # account's domain verification (P1, merged) and lead (CRM, merged) — found by the round-3
    # scanner, routed to their owners.
    ("services/account/app/api/routes.py", "verify_domain"): (
        "D3 #22, routed to account (domain verification): services.verify_domain -> "
        "dns_check.txt_contains -> check_txt resolves the TXT record inside "
        "get_tenant_session's transaction. Fix: read the claim and its token in one short "
        "transaction, resolve with none open, re-lock and record the outcome."
    ),
    ("services/account/app/cron.py", "_recheck_one"): (
        "D3 #22, routed to account (domain verification): the periodic recheck calls "
        "recheck_domain (the same TXT lookup) inside its own session_scope. Same fix shape as "
        "verify_domain, per domain."
    ),
    ("services/lead/app/api/routes.py", "put_assignment_rule"): (
        "D3 #23, routed to lead (CRM): set_assignment_rule -> _ensure_owners_active -> "
        "clients.invalid_owner_ids validates owners against customer over HTTP inside "
        "get_tenant_session's request-long transaction (the rotation lock is not held across "
        "it, but the transaction is). Fix: validate the owner ids before the transaction opens "
        "(they are request input, not rows), then write."
    ),
    ("services/lead/app/api/routes.py", "create_lead"): (
        "D3 #23, routed to lead (CRM): create_lead's owner validation, same HTTP call and same "
        "fix as put_assignment_rule."
    ),
    ("services/lead/app/api/routes.py", "update_lead"): (
        "D3 #23, routed to lead (CRM): update_lead's owner validation, same HTTP call and same "
        "fix as put_assignment_rule."
    ),
    # subscription is owned by another slice (excluded from this sweep's fixes per the task
    # brief) — found and reported, not touched. Both routes resolve a plan_version_id against
    # catalog's service-token internal API (app/config.py's own docstring: "a subscribe/
    # change-plan resolves its plan_version_id against catalog ... before pinning it") inside
    # the request's tenant-bound transaction. Fix: resolve the pin before opening the
    # transaction (or in its own untransacted step), then open it only to write the
    # subscription/change.
    ("services/subscription/app/api/routes.py", "create_subscription"): (
        "D3 #17, routed to the subscription slice: calls services.subscribe, which pins the "
        "plan version against catalog over HTTP inside the request's transaction."
    ),
    ("services/subscription/app/api/routes.py", "change_plan"): (
        "D3 #17, routed to the subscription slice: calls services.change_plan, same pin "
        "lookup as create_subscription."
    ),
    ("services/subscription/app/api/internal.py", "subscribe_as_operator"): (
        "D3 #17, routed to the subscription slice: the operator-console path to the same "
        "subscribe/pin call, same instance."
    ),
    ("services/subscription/app/api/internal.py", "change_plan_as_operator"): (
        "D3 #17, routed to the subscription slice: the operator-console path to the same "
        "change-plan/pin call, same instance."
    ),
}


@dataclass
class FunctionInfo:
    name: str
    file: Path
    node: ast.FunctionDef | ast.AsyncFunctionDef
    param_types: dict[str, str] = field(default_factory=dict)
    #: `from x import real as alias` in this function's file: alias -> real. So a call through
    #: an import alias (`cir(...)` for `call_irp_register`) resolves to the real name.
    import_aliases: dict[str, str] = field(default_factory=dict)


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


def _identifiers(source: str) -> set[str]:
    """Every identifier in an annotation's source — so a name is matched **exactly**, never as a
    substring (`SessionDep` must not match `RawSessionDep`, `Resolver` must not match
    `TargetResolver`)."""
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", source))


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname and alias.asname != alias.name:
                    aliases[alias.asname] = alias.name
    return aliases


def _collect_functions(trees: dict[Path, ast.Module]) -> list[FunctionInfo]:
    functions: list[FunctionInfo] = []
    for path, tree in trees.items():
        file_aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            param_types: dict[str, str] = {}
            for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                if arg.annotation is None:
                    continue
                annotation = ast.unparse(arg.annotation)
                if _identifiers(annotation) & CLIENT_TYPE_NAMES:
                    param_types[arg.arg] = annotation
            functions.append(
                FunctionInfo(
                    name=node.name,
                    file=path,
                    node=node,
                    param_types=param_types,
                    import_aliases=file_aliases,
                )
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
    just because the bare name matches.
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
            if not any(
                isinstance(call.func, ast.Name)
                and call.func.id == "Depends"
                and call.args
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id in committing
                for call in _calls_in(node.value)
            ):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    file_aliases.add(target.id)
        aliases[path] = file_aliases
    return aliases


def _is_di_transaction_wrapped(func: FunctionInfo, aliases: dict[Path, set[str]]) -> bool:
    """Exact-name match on the annotation's identifiers (`_identifiers`): `RawSessionDep` is
    not `SessionDep`."""
    file_aliases = aliases.get(func.file, set())
    if not file_aliases:
        return False
    for arg in func.node.args.args + func.node.args.kwonlyargs + func.node.args.posonlyargs:
        if arg.annotation is None:
            continue
        annotation = _safe_unparse(arg.annotation)
        if annotation is None:
            continue
        if _identifiers(annotation) & file_aliases:
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
PURE_SEAM_METHODS: frozenset[str] = frozenset(
    {"verify_webhook", "parse_webhook", "public_url", "sign", "aclose"}
)

#: A no-argument call to a bare module-level function named like this is a **seam factory**: the
#: dependency providers (`get_irp_client()`, `get_provider()`) and the module-level defaults
#: (`_default_resolver()`). A local bound to one is a seam as much as a typed parameter is. The
#: noun at the end is what keeps `get_sessionmaker()` or `get_settings()` out.
_SEAM_FACTORY = re.compile(
    r"^(get|_default)_\w*(client|provider|resolver|transport|store|registry|probes|source)$"
)


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_seam_source(value: ast.expr, seams: set[str]) -> bool:
    """Does assigning `value` to a local make that local a seam? A seam name itself
    (`c = client`), a seam factory call (`get_irp_client()`, awaited or not), or an `or` of
    either (`r = resolver or _default_resolver()`)."""
    if isinstance(value, ast.Await):
        value = value.value
    if isinstance(value, ast.Name):
        return value.id in seams
    if isinstance(value, ast.BoolOp):
        return any(_is_seam_source(operand, seams) for operand in value.values)
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and not value.args
        and not value.keywords
    ):
        # A bare module-level function, never a method: `secret.get_secret_value()` is not a
        # factory for anything that opens a socket.
        return bool(_SEAM_FACTORY.match(value.func.id))
    return False


def _seam_names(func: FunctionInfo) -> set[str]:
    """The names inside `func` that hold a provider seam: its seam-typed parameters, and every
    local assigned from one of them or from a seam factory — to a fixed point, so an alias of an
    alias counts too."""
    seams = set(func.param_types)
    assigns = [n for n in ast.walk(func.node) if isinstance(n, (ast.Assign, ast.AnnAssign))]
    changed = True
    while changed:
        changed = False
        for node in assigns:
            value = node.value
            if value is None or not _is_seam_source(value, seams):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in seams:
                    seams.add(target.id)
                    changed = True
    return seams


def _seam_call(call: ast.Call, seams: set[str]) -> str | None:
    """The call's source if it is a call *on* a seam — `client.register(...)`,
    `probes.ownership.check(...)`, or the seam itself called — else None. Pure methods
    (`PURE_SEAM_METHODS`) are not network calls."""
    source = _safe_unparse(call.func)
    if source is None:
        return None
    for name in seams:
        if source == name and isinstance(call.func, ast.Name):
            return source
        if source.startswith(f"{name}."):
            method = source[len(name) + 1 :]
            if method not in PURE_SEAM_METHODS:
                return source
    return None


def _is_direct_network_call(func: FunctionInfo) -> bool:
    seams = _seam_names(func)
    return bool(seams) and any(_seam_call(call, seams) for call in _calls_in(func.node))


def _resolve(name: str, func: FunctionInfo) -> str:
    return func.import_aliases.get(name, name)


def _referenced_names(call: ast.Call, func: FunctionInfo) -> set[str]:
    """What calling `call` may call: its own callee, and every function *reference* passed to it
    as an argument — `asyncio.to_thread(call_irp_cancel, …)`, `off_loop(call_x, …)`,
    `partial(fn, …)`. Resolved through the file's import aliases."""
    names: set[str] = set()
    callee = _call_name(call.func)
    if callee is not None:
        names.add(_resolve(callee, func))
    for arg in call.args:
        ref = _call_name(arg) if isinstance(arg, (ast.Name, ast.Attribute)) else None
        if ref is not None:
            names.add(_resolve(ref, func))
    return names


def _called_names(func: FunctionInfo) -> set[str]:
    names: set[str] = set()
    for call in _calls_in(func.node):
        names |= _referenced_names(call, func)
    return names


def _service_of(path: Path) -> str:
    """Which service a file belongs to, or `"platform-core"` for the shared library.

    The unit call-graph resolution is scoped to, below — **not** the whole repo. `create_asset`,
    `get_asset`, `update_asset`, `delete_asset` and `list_assets` each name a function in
    *document* (real assets, in object storage) **and** an unrelated one in *itsm* (ticket
    attachments, no object store at all); resolving "does this bare name reach the network" once
    across the entire repo made every itsm asset route a false positive the moment document's
    same-named function genuinely reached it.
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
        for _ in range(8):
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


#: Session methods that **autobegin** a transaction (SQLAlchemy 2.0): after the first of these on a
#: session opened by `async with factory() as session:` — with no `.begin()` in sight — a
#: transaction is open until the block ends.
_AUTOBEGIN_METHODS: frozenset[str] = frozenset(
    {"execute", "scalar", "scalars", "get", "flush", "stream", "stream_scalars", "refresh"}
)


def _autobegin_line(block: ast.With | ast.AsyncWith) -> int | None:
    """The line of the first autobeginning call on a session this `with` binds, or None."""
    bound = {
        item.optional_vars.id for item in block.items if isinstance(item.optional_vars, ast.Name)
    }
    if not bound:
        return None
    lines = [
        call.lineno
        for call in _calls_in(block)
        if isinstance(call.func, ast.Attribute)
        and call.func.attr in _AUTOBEGIN_METHODS
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in bound
    ]
    return min(lines) if lines else None


@dataclass(frozen=True, slots=True)
class Finding:
    file: str
    function: str
    line: int
    callee: str


def _find_violations(
    functions: list[FunctionInfo],
    reaches: dict[str, set[str]],
    di_aliases: dict[Path, set[str]],
) -> list[Finding]:
    findings: dict[tuple[str, str, int, str], Finding] = {}

    def _record(func: FunctionInfo, call: ast.Call) -> None:
        reached = reaches.get(_service_of(func.file), set())
        # A seam called **inline** inside the transaction — `client.register(...)` written in the
        # `async with session.begin():` block itself, not behind a helper.
        inline = _seam_call(call, _seam_names(func))
        callees = set(_referenced_names(call, func) & reached)
        if inline is not None:
            callees.add(inline)
        for callee in sorted(callees):
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

        for node in ast.walk(func.node):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            # An explicit `async with session.begin():` / `session_scope(...)` /
            # `self._work_scope(...)` written directly in the function's own source.
            if any(_is_transaction_context(item.context_expr) for item in node.items):
                for call in _calls_in(node):
                    _record(func, call)
                continue
            # Autobegin: `async with factory() as session:` and a query on it — every call
            # after that first query runs inside the transaction it opened.
            first = _autobegin_line(node)
            if first is not None:
                for call in _calls_in(node):
                    if call.lineno > first:
                        _record(func, call)

    return sorted(findings.values(), key=lambda f: (f.file, f.line))


def _scan(trees: dict[Path, ast.Module]) -> list[Finding]:
    functions = _collect_functions(trees)
    reaches = _reaches_network(functions)
    committing = _committing_dependency_names(trees)
    di_aliases = _di_wrapped_aliases(trees, committing)
    return _find_violations(functions, reaches, di_aliases)


def _run_scan() -> list[Finding]:
    return _scan(_parse_all())


def test_the_scanner_recognises_a_known_direct_network_caller() -> None:
    """Guards the guard: if the type-annotation match stopped working, every assertion below
    would pass vacuously. `stage_registration_attempt` builds the payload but never calls the
    client — `call_irp_register` is the one function in finance that actually does."""
    trees = _parse_all()
    functions = _collect_functions(trees)
    names = {f.name for f in functions if _is_direct_network_call(f)}
    assert "call_irp_register" in names
    assert "call_provider_charge" in names
    # D3 round 2: the four finance calls that were allow-listed until their split landed. If any
    # of these stopped being recognised, the stale-entry test below would pass vacuously for
    # them rather than proving they moved outside the transaction.
    assert {
        "call_irp_cancel",
        "call_eway_generate",
        "call_eway_cancel",
        "fetch_ims_period",
    } <= names


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


# ----------------------------------------------------------------- the scanner's own blind spots
# Each of these was a way round the round-2 scanner a review demonstrated (D3 round 3): a
# synthetic module is added to the parsed tree and must be flagged — or, for the near-miss
# annotation, must not be.


def _findings_in(service: str, source: str) -> list[Finding]:
    trees = _parse_all()
    probe = REPO_ROOT / f"services/{service}/app/_scanner_probe.py"
    trees[probe] = ast.parse(source)
    return [f for f in _scan(trees) if f.file.endswith("_scanner_probe.py")]


def test_a_call_through_an_import_alias_is_followed() -> None:
    found = _findings_in(
        "finance",
        "from app.services import call_irp_register as cir\n"
        "async def probe(session, client, payload):\n"
        "    async with session.begin():\n"
        "        cir(client, payload)\n",
    )
    assert [f.callee for f in found] == ["call_irp_register"]


def test_a_local_bound_to_a_seam_factory_is_a_seam() -> None:
    found = _findings_in(
        "finance",
        "from app.deps import get_irp_client\n"
        "def fetch_it(payload):\n"
        "    client = get_irp_client()\n"
        "    return client.register(payload)\n"
        "async def probe(session, payload):\n"
        "    async with session.begin():\n"
        "        fetch_it(payload)\n",
    )
    assert [f.callee for f in found] == ["fetch_it"]


def test_a_seam_called_inline_inside_the_transaction_is_flagged() -> None:
    """Not behind a helper at all: the client fetched from its factory and called right there in
    the `session.begin()` block."""
    found = _findings_in(
        "finance",
        "from app.deps import get_irp_client\n"
        "async def probe(session, payload):\n"
        "    async with session.begin():\n"
        "        client = get_irp_client()\n"
        "        client.register(payload)\n",
    )
    assert [f.callee for f in found] == ["client.register"]


def test_a_non_seam_factory_is_not_a_seam() -> None:
    """`get_sessionmaker()` is a no-argument `get_*` call too; it opens no socket."""
    assert (
        _findings_in(
            "finance",
            "async def probe():\n"
            "    maker = get_sessionmaker()\n"
            "    async with maker() as session, session.begin():\n"
            "        maker()\n",
        )
        == []
    )


def test_a_function_reference_handed_to_a_thread_is_followed() -> None:
    found = _findings_in(
        "finance",
        "import asyncio\n"
        "async def probe(session, client, facts):\n"
        "    async with session.begin():\n"
        "        await asyncio.to_thread(call_irp_cancel, client, facts)\n",
    )
    assert "call_irp_cancel" in {f.callee for f in found}


def test_an_autobegun_transaction_is_a_transaction() -> None:
    found = _findings_in(
        "finance",
        "async def probe(factory, client, facts):\n"
        "    async with factory() as session:\n"
        "        await session.execute(text('select 1'))\n"
        "        call_irp_cancel(client, facts)\n",
    )
    assert [f.callee for f in found] == ["call_irp_cancel"]


def test_a_call_before_the_session_is_first_used_is_not_inside_it() -> None:
    assert (
        _findings_in(
            "finance",
            "async def probe(factory, client, facts):\n"
            "    async with factory() as session:\n"
            "        call_irp_cancel(client, facts)\n"
            "        await session.execute(text('select 1'))\n",
        )
        == []
    )


def test_notifications_dns_check_is_recognised_through_its_default_resolver() -> None:
    """`check_records` binds `r = resolver or _default_resolver()` and resolves through `r` —
    invisible to a scan that only looked at typed parameters."""
    reaches = _reaches_network(_collect_functions(_parse_all()))
    assert "check_records" in reaches["notification"]
    found = _findings_in(
        "notification",
        "async def probe(session, ctx, domain_id):\n"
        "    async with session.begin():\n"
        "        await services.verify_domain(session, ctx, domain_id=domain_id)\n",
    )
    assert [f.callee for f in found] == ["verify_domain"]


def test_a_near_miss_annotation_is_not_a_committing_dependency() -> None:
    """`RawSessionDep` wraps the non-committing `get_session` (lead's `:convert`); matching
    `SessionDep` as a substring would call its whole body a transaction."""
    trees = _parse_all()
    probe = REPO_ROOT / "services/finance/app/_scanner_probe.py"
    trees[probe] = ast.parse(
        "SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]\n"
        "RawSessionDep = Annotated[AsyncSession, Depends(get_session)]\n"
        "async def raw(session: RawSessionDep, client: IrpClient, payload):\n"
        "    client.register(payload)\n"
        "async def wrapped(session: SessionDep, client: IrpClient, payload):\n"
        "    client.register(payload)\n"
    )
    functions = [f for f in _collect_functions(trees) if f.file == probe]
    aliases = _di_wrapped_aliases(trees, _committing_dependency_names(trees))
    wrapped = {f.name: _is_di_transaction_wrapped(f, aliases) for f in functions}
    assert wrapped == {"raw": False, "wrapped": True}


def test_the_round_three_seams_find_the_notification_instances() -> None:
    """The `Transport` and `AsyncClient` seams, and seam locals, are what make these visible at
    all; they are allow-listed (routed to notification), and this pins that they are found."""
    found = {(f.file, f.function) for f in _run_scan()}
    for function in ("send_test_message", "connect_whatsapp", "replace_whatsapp"):
        assert ("services/notification/app/api/engage.py", function) in found
    assert ("services/notification/app/api/engage.py", "assign_conversation") in found
