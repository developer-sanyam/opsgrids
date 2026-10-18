"""Every outbound-HTTP call site in the platform is classified (ADR-0016, Q1).

ADR-0016 makes `EgressGuard` the one door for fetching a URL a tenant typed in, but "did this URL
come from a tenant?" was a review point and nothing more. This test turns it into a checked list: it
walks every service's `app/` and every lib's `src/` for code that builds an outbound HTTP client or
request (`httpx.AsyncClient(`, `httpx.Client(`, `httpx.request(`/`get(`/`post(`..., a name imported
from `httpx`, and any `requests.` call) and fails unless the call site is in `CLASSIFIED` below,
tagged either

* `platform-configured` -- the URL is built from this platform's own settings (a sibling service,
  Meta's or a payment provider's fixed host), never from a value an organization typed in, or
* `egress-guard` -- the site is the guard itself, which resolves, vets and pins the address.

So a new call site fails CI until someone answers the question and writes the answer down. A
tenant-supplied URL is never `platform-configured`: it goes through `EgressGuard.request` and so
never appears here as a bare client. A stale entry (the site moved or was deleted) fails too, so the
list cannot rot into a rubber stamp.

A site is identified by file and enclosing function, not line number, so reformatting does not
churn it. `tools/` is operator tooling that is never deployed in a service container and is out of
scope, as are tests.
"""

# ruff: noqa: E501  (the table below is one site per line on purpose)
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PLATFORM = "platform-configured"
GUARD = "egress-guard"

#: httpx module-level helpers and constructors that open a connection.
HTTPX_CALLS = {
    "AsyncClient", "Client", "request", "get", "post", "put", "patch", "delete", "head",
    "options", "stream",
}  # fmt: skip

#: (file relative to the repo, enclosing function) -> (tag, why).
CLASSIFIED: dict[tuple[str, str], tuple[str, str]] = {
    # --- the guard itself -----------------------------------------------------------------
    ("libs/platform-core/src/platform_core/egress.py", "request"): (
        GUARD,
        "EgressGuard.request: the vetted, address-pinned fetch every tenant URL must use",
    ),
    # --- platform-internal service-to-service calls (a docker-network URL from settings) ----
    ("libs/platform-core/src/platform_core/gateway.py", "http_client"): (PLATFORM, "the gateway's own upstream proxy pool"),
    ("libs/platform-core/src/platform_core/gateway.py", "__init__"): (PLATFORM, "account-service URL from settings"),
    ("libs/platform-core/src/platform_core/audit_read_testing.py", "get"): (PLATFORM, "in-process ASGITransport test helper; no network"),
    ("libs/platform-core/src/platform_core/ops_monitor/main.py", "poll_loop"): (PLATFORM, "probes this platform's own services"),
    ("services/auth/app/provisioning.py", "__init__"): (PLATFORM, "account-service URL from settings"),
    ("services/auth/app/entitlements.py", "__init__"): (PLATFORM, "subscription-service URL from settings"),
    ("services/auth/app/directory.py", "__init__"): (PLATFORM, "account-service URL from settings"),
    ("services/catalog/app/registry.py", "__init__"): (PLATFORM, "product registry URL from settings"),
    ("services/subscription/app/registry.py", "__init__"): (PLATFORM, "product registry URL from settings"),
    ("services/subscription/app/catalog.py", "__init__"): (PLATFORM, "catalog-service URL from settings"),
    ("services/subscription/app/billing_run.py", "can_collect"): (PLATFORM, "billing-service URL from settings"),
    ("services/subscription/app/billing_run.py", "_post"): (PLATFORM, "billing-service URL from settings"),
    ("services/billing/app/billing_run.py", "billable_usage"): (PLATFORM, "subscription-service URL from settings"),
    ("services/billing/app/billing_run.py", "get_plan_version"): (PLATFORM, "subscription-service URL from settings"),
    ("services/approval/app/employee_client.py", "__init__"): (PLATFORM, "employee-service URL from settings"),
    ("services/customer/app/c1_360.py", "_default_client"): (PLATFORM, "sibling services' URLs from settings"),
    ("services/customer/app/clients.py", "deal_references"): (PLATFORM, "deal-service URL from settings"),
    ("services/lead/app/clients.py", "invalid_owner_ids"): (PLATFORM, "customer-service URL from settings"),
    ("services/lead/app/clients.py", "ensure_deal"): (PLATFORM, "deal-service URL from settings"),
    ("services/lead/app/clients.py", "ensure_company_and_contact"): (PLATFORM, "customer-service URL from settings"),
    ("services/finance/app/clients.py", "fetch_crm_contact"): (PLATFORM, "customer-service URL from settings"),
    ("services/operations/app/orders/finance_client.py", "resolve_finance_customer"): (PLATFORM, "finance-service URL from settings"),
    ("services/operations/app/orders/payment_client.py", "_request"): (PLATFORM, "payment-service URL from settings"),
    ("services/notification/app/contacts.py", "resolve_contact_by_phone"): (PLATFORM, "customer-service URL from settings"),
    ("services/notification/app/targets.py", "resolve_contact_target"): (PLATFORM, "sibling services' URLs from settings"),
    ("services/notification/app/memberships.py", "membership_is_active"): (PLATFORM, "account-service URL from settings"),
    ("services/notification/app/storefront_optin.py", "fetch_storefront_opt_in"): (PLATFORM, "storefront-service URL from settings"),
    ("services/notification/app/crm_contacts.py", "_export"): (PLATFORM, "customer-service URL from settings"),
    ("services/notification/app/crm_leads.py", "fetch_leads"): (PLATFORM, "lead-service URL from settings"),
    ("services/console/app/api/routes.py", "get_delegation_client"): (PLATFORM, "this platform's own admin endpoints"),
    ("services/console/app/backfill.py", "main"): (PLATFORM, "this platform's own services"),
    ("services/console/app/price.py", "resolve_plan_price"): (PLATFORM, "catalog-service URL from settings"),
    ("services/console/app/delegation.py", "deliver"): (PLATFORM, "auth-service URL from settings"),
    # --- fixed third-party hosts (Meta, the payment providers) -------------------------------
    ("services/notification/app/whatsapp_provider.py", "verify_ownership"): (PLATFORM, "Meta Graph API, host fixed in settings"),
    ("services/notification/app/w1_graph.py", "_client"): (PLATFORM, "Meta Graph API, host fixed in settings"),
    ("services/notification/app/w2_graph.py", "_client"): (PLATFORM, "Meta Graph API, host fixed in settings"),
    ("services/notification/app/w1_setup.py", "_step_number"): (PLATFORM, "Meta Graph API, host fixed in settings"),
    ("services/notification/app/w1_services.py", "verify_default_candidate"): (PLATFORM, "Meta Graph API, host fixed in settings"),
    ("services/notification/app/transports.py", "send"): (PLATFORM, "Meta Graph API, host fixed in settings"),
    ("services/payment/app/providers/wire.py", "request"): (PLATFORM, "Razorpay / Cashfree hosts are constants of the adapter"),
    # `Channel.api_base_url` could hold a tenant-typed domain for a future provider; the mock
    # sandbox leaves it null (platform-configured). A real provider that fills it must use
    # EgressGuard and drops this entry.
    ("services/operations/app/channels/delhivery.py", "__init__"): (PLATFORM, "Delhivery API host from settings (delhivery_api_base); never a tenant value"),
    ("services/operations/app/channels/shiprocket.py", "__init__"): (PLATFORM, "Shiprocket API host from settings (shiprocket_api_base); never a tenant value"),
    ("services/operations/app/channels/mock_provider.py", "__init__"): (PLATFORM, "mock sandbox base from settings; Channel.api_base_url stays null for it"),
}  # fmt: skip


def _scan_roots() -> list[Path]:
    return [*sorted(REPO_ROOT.glob("services/*/app")), *sorted(REPO_ROOT.glob("libs/*/src"))]


def _is_http_call(node: ast.Call, imported: set[str], has_requests: bool) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "httpx" and func.attr in HTTPX_CALLS:
            return True
        if has_requests and func.value.id == "requests":
            return True
    return isinstance(func, ast.Name) and func.id in imported


class _Visitor(ast.NodeVisitor):
    def __init__(self, imported: set[str], has_requests: bool) -> None:
        self.imported = imported
        self.has_requests = has_requests
        self.stack: list[str] = []
        self.sites: set[str] = set()

    def _scope(self, node: ast.AST) -> None:
        self.stack.append(getattr(node, "name", "<module>"))
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _scope

    def visit_Call(self, node: ast.Call) -> None:
        if _is_http_call(node, self.imported, self.has_requests):
            self.sites.add(self.stack[-1] if self.stack else "<module>")
        self.generic_visit(node)


def _sites() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in _scan_roots():
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            imported = {
                alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "httpx"
                for alias in node.names
                if alias.name in HTTPX_CALLS
            }
            has_requests = any(
                isinstance(node, ast.Import) and any(a.name == "requests" for a in node.names)
                for node in ast.walk(tree)
            )
            visitor = _Visitor(imported, has_requests)
            visitor.visit(tree)
            rel = path.relative_to(REPO_ROOT).as_posix()
            found |= {(rel, fn) for fn in visitor.sites}
    return found


def test_the_scan_finds_the_call_sites_it_is_meant_to_classify() -> None:
    """A scanner that silently matches nothing would make the next two tests pass."""
    sites = _sites()
    assert len(sites) >= 30, sorted(sites)
    assert ("libs/platform-core/src/platform_core/egress.py", "request") in sites


def test_every_outbound_http_call_site_is_classified() -> None:
    unclassified = sorted(_sites() - set(CLASSIFIED))
    assert not unclassified, (
        "outbound HTTP call sites with no classification (ADR-0016 Q1). If the URL can come from "
        "a tenant, fetch it through EgressGuard.request; otherwise add the site to CLASSIFIED as "
        f"platform-configured: {unclassified}"
    )


def test_no_classification_is_stale() -> None:
    stale = sorted(set(CLASSIFIED) - _sites())
    assert not stale, f"classified sites that no longer exist: {stale}"


def test_every_classification_is_a_known_tag_with_a_reason() -> None:
    for site, (tag, why) in CLASSIFIED.items():
        assert tag in {PLATFORM, GUARD}, site
        assert why.strip(), site
