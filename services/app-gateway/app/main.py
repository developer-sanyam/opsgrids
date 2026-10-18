"""The product gateway — the single public entry point for every product UI.

Responsibilities, and deliberately nothing more:

* terminate the public API surface and route to the service that owns the path
* verify the token once, and forward identity as signed internal headers
* refuse a product the organization has not activated
* resolve permissions, and rate limit
* propagate the correlation id so one request is traceable across services

It contains no business logic and owns no tables. Fine-grained permission and scope
filtering stay in the services, where the data is — the gateway knows *who* is calling,
not which rows they may see.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from fastapi import FastAPI

from app.config import settings
from platform_core.app import create_app
from platform_core.gateway import (
    DEFAULT_PUBLIC_PATHS,
    GatewayPolicy,
    PermissionResolver,
    RouteTable,
    build_gateway,
    http_client,
)

cfg = settings()

#: The storefront's buyer surface (ADR-0010 §4, ADR-0012 §2). Lives **here, in the gateway
#: service**, and is
#: unioned into the shared default below rather than being added to `DEFAULT_PUBLIC_PATHS` in
#: `platform_core`: editing the library's frozenset would put one product's buyer surface into
#: the one module both gateways read, and `admin-gateway` — which builds its own explicit set —
#: would then be carrying a product concern it has no use for. A two-line change in the gateway
#: *service* is the right blast radius.
#:
#: **Exact match, and that is a constraint on the whole storefront buyer API rather than a
#: quirk of this route.** `build_gateway` tests `full_path in policy.public_paths` (`full_path`
#: is the normalised `f"/api/{path}"` from the `{path:path}` catch-all, excluding the query
#: string), so a path with a variable segment is not expressible here — a route that needs one,
#: like the per-recipient Engage unsubscribe link below, uses `public_path_prefixes` instead.
#: Every anonymous storefront endpoint carries its variables in the query string or the request
#: body instead; the store slug travels in the body here for exactly that reason.
#:
#: Being on this list means: no token required, limited by client IP rather than by caller, and
#: **no entitlement check** — the public branch of the proxy returns before that gate. It does
#: not mean the path is unauthenticated *by accident*; adding one is a deliberate decision about
#: a route that has no principal to authorize.
#:
#: **Two entries, and the second is what a buyer's browser asks first.** `store-context` turns
#: the hostname in the address bar into the store's slug, so a merchant on a domain they own
#: gets served without the bundle carrying baked-in configuration (ADR-0012 §2). It is on the
#: same terms as the first: an exact path, no path variable, the variable in the body, and the
#: anonymous per-IP limit in front of it. Nothing on a custom domain is authenticated — the
#: refresh cookie is scoped to `.opsgrids.com` and is simply never sent there — so this list is
#: the whole of what a stranger on `merchant.com` may reach.
STOREFRONT_PUBLIC_PATHS = frozenset(
    {
        "/api/v1/storefront/public/serviceability",
        "/api/v1/storefront/public/store-context",
        # What a buyer's shop is made of, and the one thing a buyer may send. Both resolve a
        # PUBLISHED store from the body and serve nothing else: the catalogue returns only
        # active rows, the enquiry is a write-only receipt. Exact paths, no path variable, the
        # anonymous per-IP limit in front of them — the same terms as the pair above.
        "/api/v1/storefront/public/catalogue",
        "/api/v1/storefront/public/enquiry",
    }
)

#: Sign-up (ADR-0013 §3): the one write a stranger may make, on the same terms as the storefront
#: pair — an exact path, no path variable, the anonymous per-IP limit in front of it. It creates
#: an organisation and its owner and nothing else; the password comes later, by link, through
#: `password/forgot`, which is already public above.
ACCOUNT_PUBLIC_PATHS = frozenset({"/api/v1/account/signup"})

#: The one Engage surface with no principal: the recipient of a campaign clicking the
#: unsubscribe link in it. That link is `.../unsubscribe/{token}` — a path variable, which is
#: exactly what `public_paths`'s exact match cannot express — so it is a *prefix* instead,
#: checked against the normalised path (`GatewayPolicy.public_path_prefixes`'s own docstring),
#: never the raw one: a request dressed up as `.../unsubscribe/x/../../senders` is judged by
#: where it actually routes, not by what it merely starts with as text.
#:
#: The token is the whole of its authority — notification-service verifies the HMAC over it
#: and refuses anything that does not check out — so this prefix carries exactly the same
#: "reachable, but authorized by its own signed credential rather than a bearer token" shape
#: the storefront's public paths above already establish, just for a route the exact-match list
#: cannot name. Anonymous, IP-rate-limited, no identity header, same as every other entry here.
ENGAGE_PUBLIC_PATH_PREFIXES = frozenset({"/api/v1/engage/unsubscribe/", "/api/v1/engage/t/"})

#: The open pixel and click redirect (`GET /engage/t/o|c/{token}`), one of the two prefixes above.
#: Mailbox providers' image proxies and corporate NATs put many recipients behind one address, so
#: this prefix is counted in a bucket of its own with a much higher per-address limit
#: (`GatewayPolicy.public_prefix_rate_limits`) rather than the shared anonymous one, where a click
#: would answer 429 instead of redirecting. The unsubscribe prefix keeps the anonymous limit: a
#: person opens it once. `GatewayPolicy` refuses a limit on a prefix that is not public.
ENGAGE_TRACKING_PREFIX = "/api/v1/engage/t/"

#: The provider webhooks that reach this gateway, and the HTTP methods each accepts without a
#: bearer token: **exact path -> methods**, never a prefix. One entry: Meta's WhatsApp callback.
#:
#: Public because a provider cannot hold one of our tokens — the authentication is an HMAC
#: signature over the raw body, verified by notification-service. Until this was declared here
#: the gateway answered every delivery and every subscription handshake with `401 Missing bearer
#: token`, so inbound WhatsApp never reached the service that could have verified it.
#:
#: **Kept out of `public_paths` on purpose** (it is `GatewayPolicy.webhook_paths`). A member of
#: `public_paths` shares the anonymous per-address bucket, is public for every method and passes
#: a browser's cookie and `Origin` through. A webhook needs the opposite on each count: its own
#: rate-limit buckets, `GET` (the subscription handshake) and `POST` (the deliveries) only — a
#: `PUT`/`DELETE`/`PATCH` is a 401 like any other unlisted request — and no cookie, credential or
#: CORS surface. The route sets its own `max_body_bytes` in both route tables, and
#: `tests/infra/test_route_table_parity.py` pins that this set, both tables and that cap agree.
#:
#: **Not here, deliberately:** Razorpay's `/api/v1/webhooks/razorpay` — the *platform's* own
#: billing webhook. It is routed in production but declaring it public is a separate decision that
#: has not been taken.
#:
#: **Here:** `/api/v1/webhooks/razorpay/collections` — a *tenant's* Razorpay webhook (tenant payment
#: collection, ADR-0024 §8). POST only. Each tenant registers it in their own Razorpay dashboard
#: with `?account=<their merchant account id>` appended (a query, not a path variable, which is what
#: keeps it an exact path here — Razorpay's own WooCommerce plugin registers a query-string URL the
#: same way), and payment-service verifies every body against **that merchant's** sealed webhook
#: secret before it trusts a byte of it. An unknown account and a bad signature are the same 400.
#:
#: **And** `/api/v1/webhooks/collections` — the provider-neutral path for the same thing
#: (ADR-0033): a Cashfree merchant registers it (and a Cashfree Order names it as its
#: `notify_url`). POST only, the same `?account=` query, the same handler behind it; payment takes
#: the provider from the account row, never from the path, so the Razorpay path is kept forever as
#: an alias and neither path can be used to have a body judged by the other provider's rules.
#:
#: **And** `/api/v1/pay/return` — not a webhook but the page a redirect-checkout
#: provider (PhonePe; PayU and Paytm POST their form to it) sends the paying customer's browser
#: back to. `GET` and `POST`; payment answers a static "you can close this tab" page that reads
#: nothing from the request, so it can neither be steered nor trusted. The staff screen that
#: opened the checkout asks the provider what happened.
#:
#: The three `/whatsapp/deauthorize`, `/whatsapp/data-deletion` and
#: `/whatsapp/data-deletion-status` entries are Facebook Login's deauthorize and data-deletion
#: callbacks — a different Meta trust scheme (`signed_request`, verified by notification's
#: `app/meta_signed_request.py`) from the WhatsApp Business Platform callback above
#: (`X-Hub-Signature-256`), grouped in this same mapping because the shape they need from the
#: gateway is identical: no bearer token, their own rate-limit buckets, and exactly the methods
#: named here. `data-deletion-status` is `GET`-only and carries no signature at all — it is the
#: public status page Meta's own response to `data-deletion` links to, named by an opaque
#: confirmation code in the query string rather than a path variable, which is what lets it sit in
#: this exact-match mapping instead of needing a prefix (`GatewayPolicy.public_path_prefixes`, the
#: unsubscribe link's own shape) for a variable segment.
WEBHOOK_PUBLIC_PATHS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "/api/v1/webhooks/whatsapp": frozenset({"GET", "POST"}),
        "/api/v1/webhooks/whatsapp/deauthorize": frozenset({"POST"}),
        "/api/v1/webhooks/whatsapp/data-deletion": frozenset({"POST"}),
        "/api/v1/webhooks/whatsapp/data-deletion-status": frozenset({"GET"}),
        "/api/v1/webhooks/razorpay/collections": frozenset({"POST"}),
        "/api/v1/webhooks/collections": frozenset({"POST"}),
        # A customer's own Shiprocket account pushing tracking (operations verifies the
        # per-connection `x-api-key` token). Shiprocket forbids "shiprocket"/"sr"/"kr" in a webhook
        # URL, hence the neutral slug. Body cap 256 KiB in both route tables.
        "/api/v1/webhooks/operations/parcel-track": frozenset({"POST"}),
        # A customer's own Delhivery account pushing scans (operations verifies the per-connection
        # `Authorization` token). Same 256 KiB cap in both route tables.
        "/api/v1/webhooks/operations/delhivery": frozenset({"POST"}),
        "/api/v1/webhooks/operations/shopify": frozenset({"POST"}),
        "/api/v1/pay/return": frozenset({"GET", "POST"}),
    }
)

# Resolved relative to the service directory so `make run` and the container image agree.
_routes_path = Path(cfg.routes_file)
if not _routes_path.is_absolute():
    _routes_path = Path(__file__).resolve().parent.parent / cfg.routes_file

routes = RouteTable.from_file(_routes_path)

policy = GatewayPolicy(
    audience=cfg.audience,
    public_paths=DEFAULT_PUBLIC_PATHS | STOREFRONT_PUBLIC_PATHS | ACCOUNT_PUBLIC_PATHS,
    public_path_prefixes=ENGAGE_PUBLIC_PATH_PREFIXES,
    public_prefix_rate_limits={ENGAGE_TRACKING_PREFIX: cfg.engage_tracking_rate_limit_per_minute},
    webhook_paths=WEBHOOK_PUBLIC_PATHS,
    rate_limit=cfg.rate_limit_per_minute,
    anonymous_rate_limit=cfg.anonymous_rate_limit_per_minute,
    webhook_rate_limit=cfg.webhook_rate_limit_per_minute,
    webhook_global_rate_limit=cfg.webhook_global_rate_limit_per_minute,
    upstream_timeout=cfg.upstream_timeout_seconds,
)

resolver = PermissionResolver(cfg.account_service_url, cfg)


async def _startup(app: FastAPI) -> None:
    # One pooled client for the process. Building one per request leaks sockets under any
    # real load.
    app.state.http = http_client(policy)


async def _shutdown(app: FastAPI) -> None:
    await app.state.http.aclose()
    await resolver.aclose()


app = create_app(
    cfg,
    title="Product gateway",
    description=(
        "The public entry point for OpsGrids's product interfaces. Verifies once, resolves "
        "the organization, and refuses anything the customer has not activated."
    ),
    on_startup=_startup,
    on_shutdown=_shutdown,
    # The gateway owns no tables, so it opens no engine — an earlier version did,
    # contradicting this service's own configuration comment and giving readiness a
    # dependency it never uses. Redis it genuinely needs: the rate limiter and the
    # permission-version check both read it, and both fail closed.
    database=False,
    redis=True,
    # M2(b) of the 2026-09-24 SSO review: Caddy is the only thing in front of this process in
    # production, so `request.client.host` — what the anonymous rate limiter keys on — is
    # otherwise always Caddy's own address, one shared bucket for every visitor on the box.
    trust_forwarded_for=True,
)

build_gateway(app, settings=cfg, routes=routes, policy=policy, resolver=resolver)
