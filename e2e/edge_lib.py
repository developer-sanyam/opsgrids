"""Shared plumbing for the hand-run edge scripts (`edge_selfserve_journey.py`,
`edge_product_smoke.py`). Neither is part of the pytest suite: both create real organisations
on the production box and read the rendered email off it over ssh. Nothing in here is safe to
import for its side effects — every function is called explicitly by the script that wants it.
"""

# ruff: noqa: E501, S608 — the SQL runs against the production box over ssh with values these
# scripts generated themselves (never user input), and the compose command reads best unwrapped.
from __future__ import annotations

import contextlib
import re
import shlex
import subprocess
import time

import httpx

API = "https://api.opsgrids.com"
BOX = "deploy@20.219.18.84"
COMPOSE = "cd /srv/opsgrids/repo/infrastructure/edge && docker compose -f compose.prod.yml --env-file .env"


class StepLog:
    """One script's running tally of PASS/FAIL steps, printed as each one happens.

    `edge_selfserve_journey.py` keeps its own historical `results`/`step()` pair rather than
    this class — it works, and there is no reason to disturb it. `edge_product_smoke.py` runs
    the same shape of journey once per product, so it needs one of these per product plus a
    combined table at the end; that is what this class is for.
    """

    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def step(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
        return ok

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [r for r in self.results if not r[1]]

    @property
    def passed_count(self) -> int:
        return len(self.results) - len(self.failed)

    @property
    def ok(self) -> bool:
        return not self.failed


class PatientClient(httpx.Client):
    """An `httpx.Client` that waits out the gateway's rate limit instead of failing on it.

    The gateway allows 300 requests a minute signed in and 60 anonymous per address. A
    create-read-update-delete walk across eleven products stays under both, but only just on a
    fast link, and a throttled request is not a product failure — reporting it as one would make
    the first real run of these scripts lie. A 429 is retried after the server's `Retry-After`
    (or a growing pause), a few times, and then returned as it is so a genuine lockout still
    shows up.
    """

    MAX_WAITS = 4

    def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().request(*args, **kwargs)
        for attempt in range(self.MAX_WAITS):
            if response.status_code != 429:
                return response
            header = response.headers.get("retry-after", "")
            pause = float(header) if header.replace(".", "", 1).isdigit() else 5.0 * (attempt + 1)
            time.sleep(min(pause, 60.0))
            response = super().request(*args, **kwargs)
        return response


#: What an address may look like before it is allowed inside a SQL literal.
_SAFE_EMAIL = re.compile(r"[A-Za-z0-9._+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def box(sql: str, *, host: str = BOX, compose: str = COMPOSE) -> str:
    """Run one SQL statement on the production box over ssh.

    This is the only way either script reads its "inbox": the notification transport is `log`
    until SMTP exists, so a sent email is a row in `notification.messages` rather than
    something that reaches a real mailbox.
    """
    cmd = f"{compose} exec -T postgres psql -U opsgrids -d opsgrids -Atc {shlex.quote(sql)}"
    out = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, cmd], capture_output=True, text=True, timeout=60
    )
    return out.stdout.strip()


def read_reset_token(email: str, *, attempts: int = 30, interval: float = 1.0) -> tuple[str, str]:
    """Poll the box's notification outbox for the set-password email and pull the token out of
    the link it carries. Returns `(token, raw_body)` — `token` is `""` if the email never
    showed up within `attempts * interval` seconds.
    """
    # The address lands inside a SQL literal run as the database superuser on production. Every
    # caller generates it today; a shared helper does not get to assume the next one will.
    if not _SAFE_EMAIL.fullmatch(email):
        raise ValueError(
            f"refusing to query for an address that is not plainly an email: {email!r}"
        )
    body = ""
    for _ in range(attempts):
        body = box(
            f"select body from notification.messages where to_address = '{email}' and state = 'sent' order by created_at desc limit 1"
        )
        if "reset-password?token=" in body:
            break
        time.sleep(interval)
    match = re.search(r"reset-password\?token=([A-Za-z0-9_\-]+)", body)
    return (match.group(1) if match else "", body)


def login(c: httpx.Client, email: str, password: str) -> str:
    """Log in, following the pre-auth -> select-org hop a fresh single-org signup takes.

    Returns the access token on success. Raises `RuntimeError` on failure — this function does
    not print or exit; callers decide how a failed login should be reported.
    """
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code} {r.text[:160]}")
    body = r.json()
    if body.get("access_token"):
        return str(body["access_token"])
    r2 = c.post(
        "/api/v1/auth/select-org",
        json={
            "pre_auth_token": body["pre_auth_token"],
            "organization_id": body["organizations"][0]["organization_id"],
        },
    )
    if r2.status_code != 200:
        raise RuntimeError(f"select-org {r2.status_code} {r2.text[:160]}")
    return str(r2.json()["access_token"])


def find_flat_offer(plans: list[dict]) -> tuple[dict, dict] | None:
    """The one price a marketplace card shows: the first published version with a flat price.

    Mirrors `trialOffer()` in `frontend/opsgrids-dashboard/src/routes/Marketplace.tsx` exactly —
    that function (not "the first published version", which need not be the priced one) is what
    decides whether a card's "Start 14-day trial" button exists at all. The customer read no
    longer carries amounts (pricing is set per client), so a flat price is recognised by its kind.
    """
    for plan in plans:
        for version in plan.get("versions", []):
            if version.get("state") != "published":
                continue
            price = next(
                (p for p in version.get("prices", []) if p.get("kind") == "flat"),
                None,
            )
            if price is not None:
                return version, price
    return None


def bootstrap_owner(
    c: httpx.Client,
    log: StepLog,
    *,
    key: str | None,
    slug: str,
    email: str,
    password: str,
    org_name: str,
) -> dict[str, str] | None:
    """Sign up a throwaway org, set its password from the reset link, log in — and, when `key`
    names a product, start its 14-day trial and wait for the subscription event to entitle it
    and install its roles, exactly as `edge_product_smoke.py`'s steps 1-3 do (re-logging in on
    the gateway's `401 token-stale`, ADR-0007). Logs one `StepLog` line per step and returns the
    resulting bearer-header dict, or `None` once a step has failed (already logged as FAIL —
    callers do not need to log the failure again, only stop).

    `key=None` skips the marketplace/subscribe/entitlement dance entirely: the core organisation
    surfaces (`/organization`, `/members`, `/roles`, `/org-units`, `/locations`, `/invitations`)
    carry no `product` key at the gateway (`services/app-gateway/routes.json`), so the
    freshly-signed-up owner can reach them immediately.
    """

    def require(name: str, ok: bool, detail: str = "") -> bool:
        log.step(name, ok, detail)
        return ok

    r = c.post(
        "/api/v1/account/signup",
        json={
            "organization_name": org_name,
            "slug": slug,
            "full_name": "Crud Test Owner",
            "email": email,
        },
    )
    if not require("sign up (public)", r.status_code == 201, f"{r.status_code} {r.text[:120]}"):
        return None

    r = c.post("/api/v1/auth/password/forgot", json={"email": email})
    if not require("request the set-password link", r.status_code == 202, str(r.status_code)):
        return None

    token, body = read_reset_token(email)
    if not require("the set-password email carries the link", token != "", f"{len(body)} bytes"):
        return None

    r = c.post("/api/v1/auth/password/reset", json={"token": token, "new_password": password})
    if not require("set the password from the link", r.status_code == 204, str(r.status_code)):
        return None

    try:
        tok = login(c, email, password)
    except RuntimeError as exc:
        require("log in", False, str(exc))
        return None
    require("log in", True, "")
    h = {"Authorization": f"Bearer {tok}"}

    if key is None:
        return h

    r = c.get("/api/v1/products", headers=h)
    product = next((p for p in r.json() if p["key"] == key), None) if r.status_code == 200 else None
    if not require(
        "marketplace lists the product as available",
        product is not None and product.get("status") == "available",
        f"{r.status_code} {product and (product.get('name'), product.get('status'))}",
    ):
        return None

    r = c.get("/api/v1/plans", params={"product_key": key}, headers=h)
    plans = r.json() if r.status_code == 200 else []
    offer = find_flat_offer(plans) if r.status_code == 200 else None
    if not require(
        "a published flat price exists (the card's 'Start 14-day trial' condition)",
        offer is not None,
        f"{r.status_code}",
    ):
        return None
    version, _price = offer

    r = c.post(
        "/api/v1/subscriptions",
        headers=h,
        json={"product_key": key, "plan_version_id": version["id"], "trial_days": 14},
    )
    if not require(
        "start the 14-day trial (POST /subscriptions)",
        r.status_code == 201 and r.json().get("state") == "trial",
        f"{r.status_code} {r.text[:120]}",
    ):
        return None

    entitled = False
    stale_seen = False
    t0 = time.time()
    last = ""
    for _ in range(40):  # every 3 s, <=120 s: the gateway rate-limits a caller per minute
        acts = c.get("/api/v1/products/activations", headers=h)
        last = f"{acts.status_code} {acts.text[:100]}"
        if acts.status_code == 401 and "token-stale" in acts.text:
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
    if not require(
        "subscription event -> product activation entitles (re-login on token-stale, ADR-0007)",
        entitled,
        f"after {time.time() - t0:.0f}s; stale_seen={stale_seen}; last={last}",
    ):
        return None

    try:
        tok = login(c, email, password)  # a fresh token carries the new entitlement and grants
    except RuntimeError as exc:
        require("re-login for a fresh token", False, str(exc))
        return None
    require("re-login for a fresh token", True, "")
    return {"Authorization": f"Bearer {tok}"}
