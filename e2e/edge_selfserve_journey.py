"""ADR-0013 end to end on the LIVE EDGE: a stranger buys Inventory, and nobody wires anything.

Not part of the pytest suite: it creates a real organisation on the production box and reads
the rendered email off it over ssh. Run by hand after a deploy:

    uv run python e2e/edge_selfserve_journey.py

Every step asserts and prints one line. The only thing read off the box is the reset email —
the transport is `log` until SMTP exists, so the inbox is the notification.messages table.
"""

# ruff: noqa: E501, S608 — a one-line-per-step script: the SQL runs against the production box
# over ssh with values this script generated itself, and each step reads best as one line.
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import httpx
from edge_lib import API, box, read_reset_token
from edge_lib import login as _login

RUN = uuid.uuid4().hex[:6]
EMAIL = f"buyer-{RUN}@example.com"
SLUG = f"buyer-{RUN}"
PASSWORD = f"Trial-{uuid.uuid4().hex[:14]}!"

results: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def login(c: httpx.Client) -> str:
    try:
        return _login(c, EMAIL, PASSWORD)
    except RuntimeError as exc:
        step("login", False, str(exc))
        sys.exit(1)


def main() -> None:
    c = httpx.Client(base_url=API, timeout=30)

    # ---------------------------------------------------------------- 1. sign up
    r = c.post(
        "/api/v1/account/signup",
        json={
            "organization_name": f"Buyer {RUN}",
            "slug": SLUG,
            "full_name": "Buyer Owner",
            "email": EMAIL,
        },
    )
    step(
        "1 sign up (public)",
        r.status_code == 201,
        f"{r.status_code} org={r.json().get('organization_id', '?')[:8]}",
    )
    org_id = r.json().get("organization_id")

    # ---------------------------------------------------------------- 2. the set-password link, from the "inbox"
    r = c.post("/api/v1/auth/password/forgot", json={"email": EMAIL})
    step("2a request the set-password link", r.status_code == 202, str(r.status_code))
    token, body = read_reset_token(EMAIL)
    step(
        "2b the email rendered by the platform rule carries the link",
        token != "",
        f"{len(body)} bytes",
    )
    r = c.post("/api/v1/auth/password/reset", json={"token": token, "new_password": PASSWORD})
    step("2c set the password from the link", r.status_code == 204, str(r.status_code))
    r = c.post("/api/v1/auth/password/reset", json={"token": token, "new_password": PASSWORD + "x"})
    step("2d the link works once", r.status_code == 404, str(r.status_code))

    # ---------------------------------------------------------------- 3. sign in
    tok = login(c)
    h = {"Authorization": f"Bearer {tok}"}
    me = c.get("/api/v1/auth/me", headers=h).json()
    step(
        "3 sign in; owner holds ORG_OWNER",
        "ORG_OWNER" in me.get("roles", []),
        f"roles={me.get('roles')}",
    )

    # ---------------------------------------------------------------- 4. the marketplace
    r = c.get("/api/v1/products", headers=h)
    inv = (
        next((p for p in r.json() if p["key"] == "operations"), None)
        if r.status_code == 200
        else None
    )
    step(
        "4a marketplace lists Inventory as available",
        inv is not None and inv["status"] == "available" and inv["name"] == "OpsGrids Inventory",
        f"{r.status_code} {inv and (inv['name'], inv['status'])}",
    )
    r = c.get("/api/v1/plans", params={"product_key": "operations"}, headers=h)
    versions = [
        v
        for p in (r.json() if r.status_code == 200 else [])
        for v in p["versions"]
        if v["state"] == "published"
    ]
    flat = next((pr for v in versions for pr in v["prices"] if pr["kind"] == "flat"), None)
    step(
        "4b a published flat monthly price",
        r.status_code == 200 and flat is not None,
        f"{r.status_code} {flat and (flat['amount_minor'], flat['currency'])}",
    )
    r = c.get("/api/v1/operations/suppliers", headers=h)
    step(
        "4c Inventory's API is 402 before a subscription (no entitlement)",
        r.status_code == 402,
        str(r.status_code),
    )

    # ---------------------------------------------------------------- 5. start the trial = subscribe
    r = c.post(
        "/api/v1/subscriptions",
        headers=h,
        json={"product_key": "operations", "plan_version_id": versions[0]["id"], "trial_days": 14},
    )
    step(
        "5 start the 14-day trial (POST /subscriptions)",
        r.status_code == 201 and r.json().get("state") == "trial",
        f"{r.status_code} {r.text[:120]}",
    )

    # ---------------------------------------------------------------- 6. the events do the rest
    entitled = False
    t0 = time.time()
    last = ""
    stale_seen = False
    for _ in range(40):  # every 3 s: the gateway limits a caller per minute, and a 1 s poll hits it
        acts = c.get("/api/v1/products/activations", headers=h)
        last = f"{acts.status_code} {acts.text[:100]}"
        if acts.status_code == 401 and "token-stale" in acts.text:
            # The roles landed and the permission version moved: the gateway refuses the old
            # token by design (ADR-0007). A real client re-authenticates; so does this one.
            stale_seen = True
            h = {"Authorization": f"Bearer {login(c)}"}
            continue
        if acts.status_code == 200 and any(
            a["product_key"] == "operations" and a["entitles"] for a in acts.json()
        ):
            entitled = True
            break
        time.sleep(3)
    step(
        "6a' the old token went stale when grants changed (ADR-0007), and re-login fixed it",
        stale_seen,
        "",
    )
    step(
        "6a subscription event → product activation (by event, no human)",
        entitled,
        f"after {time.time() - t0:.0f}s; last={last}",
    )
    roles_ok = False
    for _ in range(40):
        rows = box(
            f"select count(*) from account.roles where organization_id = '{org_id}' and code like 'OPERATIONS_%'"
        )
        if rows.strip() not in ("", "0"):
            roles_ok = True
            break
        time.sleep(1)
    step(
        "6b activation event → Inventory's roles installed in the organisation (no wire_product)",
        roles_ok,
        f"roles={rows.strip()}",
    )
    owner_has_admin = box(
        f"select count(*) from account.membership_roles mr join account.roles r on r.id = mr.role_id join account.memberships m on m.id = mr.membership_id where m.organization_id = '{org_id}' and m.is_owner and r.code = 'OPERATIONS_ADMIN'"
    )
    step(
        "6c the owner was granted OPERATIONS_ADMIN",
        owner_has_admin.strip() == "1",
        f"count={owner_has_admin.strip()}",
    )

    # ---------------------------------------------------------------- 7. use it
    tok = login(c)  # a fresh token carries the new entitlement and grants
    h = {"Authorization": f"Bearer {tok}"}
    r = c.get("/api/v1/operations/suppliers", headers=h)
    step(
        "7a Inventory's API answers 200 after the trial starts",
        r.status_code == 200,
        f"{r.status_code} {r.text[:80]}",
    )
    r = c.get("/api/v1/operations/inventory", headers=h)
    step(
        "7b the owner can read inventory on hand",
        r.status_code == 200,
        f"{r.status_code} {r.text[:80]}",
    )
    r = c.get("/api/v1/operations/overview", headers=h)
    step("7c the Inventory overview answers", r.status_code == 200, str(r.status_code))

    # ---------------------------------------------------------------- 8. money is readable
    r = c.get("/api/v1/subscriptions", headers=h)
    step(
        "8a subscriptions list the trial with its end date",
        r.status_code == 200
        and any(s["product_key"] == "operations" and s["trial_ends_at"] for s in r.json()),
        f"{r.status_code}",
    )
    r = c.get("/api/v1/invoices", headers=h)
    step(
        "8b invoices are readable (billing permissions exist now)",
        r.status_code == 200,
        f"{r.status_code} {len(r.json()) if r.status_code == 200 else r.text[:80]}",
    )
    r = c.get("/api/v1/credit-balance", headers=h, params={"currency": "INR"})
    step("8c credit balance is readable", r.status_code == 200, str(r.status_code))

    # ---------------------------------------------------------------- 9. negatives that must hold
    r = c.get("/api/v1/hrms/employees", headers=h)
    step(
        "9a a product not started is still refused", r.status_code in (402, 404), str(r.status_code)
    )
    r = c.get("/api/v1/operations/suppliers")
    step("9b anonymous is refused", r.status_code == 401, str(r.status_code))

    failed = [x for x in results if not x[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} steps passed  (org {SLUG}, {EMAIL})")
    Path("edge_selfserve_tenant.json").write_text(
        json.dumps({"org_id": org_id, "email": EMAIL, "slug": SLUG})
    )
    sys.exit(1 if failed else 0)


main()
