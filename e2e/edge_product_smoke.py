"""ADR-0013 end to end on the LIVE EDGE, generalised to any product: a stranger buys one, and
nobody wires anything.

The same journey `edge_selfserve_journey.py` runs by hand for Inventory (`operations`), run
once per product: sign up a throwaway org, set its password from the link the box "sent", start
the 14-day trial on the published flat plan, wait for the subscription event to entitle the
product and install its roles, then read one endpoint through the gateway and one app host
directly. Not part of the pytest suite — it creates real organisations on the production box.

    uv run python e2e/edge_product_smoke.py --product hrms
    uv run python e2e/edge_product_smoke.py --all

`--all` runs the ten products one after another, never in parallel: the gateway rate-limits a
caller per minute (see the 3 s poll below), and two products racing for it would just make both
slower to diagnose. Each product seeds its own org and prints one line per step, then the run
ends with a PASS/FAIL table; the process exits non-zero if any product failed.
"""

from __future__ import annotations

# read best unwrapped.
import argparse
import contextlib
import sys
import time
import uuid

import httpx
from edge_lib import API, StepLog, find_flat_offer, login, read_reset_token

# key -> (one read endpoint through the gateway, the permission it needs, the product's own
# app host). The endpoint is the same one the org owner's admin role — built from the
# product's own manifest, `libs/contracts/permissions/<key>.json` — can always reach; no
# service segment sits in front of it, the gateway mounts every product router at /api/v1
# directly.
PRODUCTS: dict[str, dict[str, str]] = {
    "hrms": {
        "list_path": "/api/v1/employees",
        "permission": "hrms.people.employee.read",
        "host": "https://people.opsgrids.com",
    },
    "crm": {
        "list_path": "/api/v1/customers",
        "permission": "crm.book.company.read",
        "host": "https://revenue.opsgrids.com",
    },
    "pulseboard": {
        "list_path": "/api/v1/projects",
        "permission": "pulseboard.engagements.project.read",
        "host": "https://pulse.opsgrids.com",
    },
    "projects": {
        "list_path": "/api/v1/projects-workspace/projects",
        "permission": "projects.portfolio.project.read",
        "host": "https://projects.opsgrids.com",
    },
    "support": {
        "list_path": "/api/v1/support/tickets",
        "permission": "support.desk.ticket.read",
        "host": "https://support.opsgrids.com",
    },
    "marketing": {
        "list_path": "/api/v1/marketing/campaigns",
        "permission": "marketing.analytics.campaign.read",
        "host": "https://marketing.opsgrids.com",
    },
    "finance": {
        "list_path": "/api/v1/finance/invoices",
        "permission": "finance.billing.invoice.read",
        "host": "https://finance.opsgrids.com",
    },
    "itsm": {
        "list_path": "/api/v1/itsm/incidents",
        "permission": "itsm.service.incident.read",
        "host": "https://it.opsgrids.com",
    },
    "operations": {
        "list_path": "/api/v1/operations/suppliers",
        "permission": "operations.procurement.supplier.read",
        "host": "https://ops.opsgrids.com",
    },
    "storefront": {
        "list_path": "/api/v1/storefront/delivery/zones",
        "permission": "storefront.delivery.zone.read",
        "host": "https://store.opsgrids.com",
    },
}


class Abort(Exception):
    """Raised when a step this product's later steps depend on failed outright. Caught once,
    per product, in `run_product` — the step itself is already logged as FAIL by the time this
    is raised; this only stops the dependent steps that would otherwise fail confusingly."""


def run_product(key: str) -> StepLog:
    cfg = PRODUCTS[key]
    log = StepLog()
    run_id = time.strftime("%H%M%S")
    slug = f"smoke-{key}-{run_id}"
    email = f"{slug}@example.com"
    password = f"Smoke-{uuid.uuid4().hex[:14]}!"
    print(f"\n=== {key} (org {slug}, {email}) ===")

    c = httpx.Client(base_url=API, timeout=30)
    try:
        _api_steps(c, log, key, cfg, slug, email, password)
    except Abort:
        pass
    except httpx.HTTPError as exc:
        log.step("unexpected transport error talking to the gateway", False, str(exc))
    finally:
        c.close()

    # Step 5 is deliberately outside the try/except above and never raises Abort itself: DNS
    # for a freshly deployed host can lag behind the API being reachable, and that must not be
    # allowed to look like the API steps failed.
    _host_step(log, key, cfg)

    passed = log.passed_count
    total = len(log.results)
    print(f"{passed}/{total} steps passed for {key}")
    return log


def _require(log: StepLog, name: str, ok: bool, detail: str = "") -> None:
    """Log a step; abort this product's remaining API steps if it failed."""
    log.step(name, ok, detail)
    if not ok:
        raise Abort(name)


def _api_steps(
    c: httpx.Client,
    log: StepLog,
    key: str,
    cfg: dict[str, str],
    slug: str,
    email: str,
    password: str,
) -> None:
    # ---------------------------------------------------------------- 1. sign up, set a
    # password from the link the box "sent", log in
    r = c.post(
        "/api/v1/account/signup",
        json={
            "organization_name": f"Smoke {key} {slug}",
            "slug": slug,
            "full_name": "Smoke Test Owner",
            "email": email,
        },
    )
    org_id = (
        r.json().get("organization_id")
        if r.headers.get("content-type", "").startswith("application/json")
        else None
    )
    _require(
        log,
        "1a sign up (public)",
        r.status_code == 201,
        f"{r.status_code} org={(org_id or '?')[:8]}",
    )

    r = c.post("/api/v1/auth/password/forgot", json={"email": email})
    _require(log, "1b request the set-password link", r.status_code == 202, str(r.status_code))

    token, body = read_reset_token(email)
    _require(
        log,
        "1c the email rendered by the platform rule carries the link",
        token != "",
        f"{len(body)} bytes",
    )

    r = c.post("/api/v1/auth/password/reset", json={"token": token, "new_password": password})
    _require(log, "1d set the password from the link", r.status_code == 204, str(r.status_code))

    try:
        tok = login(c, email, password)
    except RuntimeError as exc:
        _require(log, "1e log in", False, str(exc))
        return
    log.step("1e log in", True, "")
    h = {"Authorization": f"Bearer {tok}"}

    # ---------------------------------------------------------------- 2. the marketplace: the
    # product is available, a published flat price exists, start the trial
    r = c.get("/api/v1/products", headers=h)
    product = next((p for p in r.json() if p["key"] == key), None) if r.status_code == 200 else None
    _require(
        log,
        "2a marketplace lists the product as available",
        product is not None and product.get("status") == "available",
        f"{r.status_code} {product and (product.get('name'), product.get('status'))}",
    )

    r = c.get("/api/v1/plans", params={"product_key": key}, headers=h)
    plans = r.json() if r.status_code == 200 else []
    offer = find_flat_offer(plans) if r.status_code == 200 else None
    _require(
        log,
        "2b a published flat price exists (the card's 'Start 14-day trial' condition)",
        offer is not None,
        f"{r.status_code} {offer and (offer[1].get('amount_minor'), offer[1].get('currency'))}",
    )
    assert offer is not None  # _require already aborted otherwise
    version, _price = offer

    r = c.post(
        "/api/v1/subscriptions",
        headers=h,
        json={"product_key": key, "plan_version_id": version["id"], "trial_days": 14},
    )
    _require(
        log,
        "2c start the 14-day trial (POST /subscriptions)",
        r.status_code == 201 and r.json().get("state") == "trial",
        f"{r.status_code} {r.text[:120]}",
    )

    # ---------------------------------------------------------------- 3. the events do the
    # rest: wait for entitlement and the product's roles, re-login for a fresh token
    entitled = False
    stale_seen = False
    t0 = time.time()
    last = ""
    for _ in range(40):  # every 3 s, <=120 s: the gateway rate-limits a caller per minute
        acts = c.get("/api/v1/products/activations", headers=h)
        last = f"{acts.status_code} {acts.text[:100]}"
        if acts.status_code == 401 and "token-stale" in acts.text:
            # The roles landed and the permission version moved: the gateway refuses the old
            # token by design (ADR-0007). A real client re-authenticates; so does this one.
            stale_seen = True
            with contextlib.suppress(RuntimeError):
                h = {"Authorization": f"Bearer {login(c, email, password)}"}
            continue
        if acts.status_code == 200 and any(
            a["product_key"] == key and a["entitles"] for a in acts.json()
        ):
            entitled = True
            break
        time.sleep(3)
    _require(
        log,
        "3a subscription event -> product activation entitles (re-login on token-stale, ADR-0007)",
        entitled,
        f"after {time.time() - t0:.0f}s; stale_seen={stale_seen}; last={last}",
    )

    r = c.get("/api/v1/roles", headers=h)
    roles = (
        [role for role in r.json() if role.get("product") == key] if r.status_code == 200 else []
    )
    _require(
        log,
        "3b the product's roles are installed in the organisation",
        r.status_code == 200 and len(roles) > 0,
        f"{r.status_code} roles={[role.get('code') for role in roles]}",
    )

    try:
        tok = login(c, email, password)  # a fresh token carries the new entitlement and grants
    except RuntimeError as exc:
        _require(log, "3c re-login for a fresh token", False, str(exc))
        return
    log.step("3c re-login for a fresh token", True, "")
    h = {"Authorization": f"Bearer {tok}"}

    # ---------------------------------------------------------------- 4. use it: one read
    # endpoint, through the gateway, needing the permission the org owner's admin role holds
    r = c.get(cfg["list_path"], headers=h)
    log.step(
        f"4a {cfg['list_path']} answers 200 (needs {cfg['permission']})",
        r.status_code == 200,
        f"{r.status_code} {r.text[:100]}",
    )


def _host_step(log: StepLog, key: str, cfg: dict[str, str]) -> None:
    """The product's own app host over HTTPS, serving the SPA shell. Kept out of the API
    steps' try/except: DNS for a freshly deployed host can lag well behind the API answering,
    and that must be visible as its own failure rather than aborting or hiding the API result."""
    host = cfg["host"]
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as hc:
            r = hc.get(host)
        ok = r.status_code == 200 and '<div id="root">' in r.text
        log.step(f"5 {host} answers 200 with the SPA shell", ok, f"{r.status_code}")
    except httpx.HTTPError as exc:
        log.step(f"5 {host} answers 200 with the SPA shell", False, f"unreachable: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--product", choices=sorted(PRODUCTS), help="run one product's journey")
    group.add_argument("--all", action="store_true", help="run every product's journey, in order")
    args = parser.parse_args()

    keys = sorted(PRODUCTS) if args.all else [args.product]

    logs: dict[str, StepLog] = {}
    for key in keys:
        logs[key] = run_product(key)

    print("\n=== summary ===")
    width = max(len(k) for k in keys)
    failed_products = []
    for key in keys:
        log = logs[key]
        status = "PASS" if log.ok else "FAIL"
        print(f"{status}  {key:<{width}}  {log.passed_count}/{len(log.results)} steps")
        if not log.ok:
            failed_products.append(key)
            for name, _ok, detail in log.failed:
                print(f"       - {name}  {detail}")

    sys.exit(1 if failed_products else 0)


if __name__ == "__main__":
    main()
