"""Shared plumbing for the hand-run edge scripts (`edge_selfserve_journey.py`,
`edge_product_smoke.py`). Neither is part of the pytest suite: both create real organisations
on the production box and read the rendered email off it over ssh. Nothing in here is safe to
import for its side effects — every function is called explicitly by the script that wants it.
"""

# ruff: noqa: E501, S608 — the SQL runs against the production box over ssh with values these
# scripts generated themselves (never user input), and the compose command reads best unwrapped.
from __future__ import annotations

import re
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


def box(sql: str, *, host: str = BOX, compose: str = COMPOSE) -> str:
    """Run one SQL statement on the production box over ssh.

    This is the only way either script reads its "inbox": the notification transport is `log`
    until SMTP exists, so a sent email is a row in `notification.messages` rather than
    something that reaches a real mailbox.
    """
    cmd = f'{compose} exec -T postgres psql -U opsgrids -d opsgrids -Atc "{sql}"'
    out = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, cmd], capture_output=True, text=True, timeout=60
    )
    return out.stdout.strip()


def read_reset_token(email: str, *, attempts: int = 30, interval: float = 1.0) -> tuple[str, str]:
    """Poll the box's notification outbox for the set-password email and pull the token out of
    the link it carries. Returns `(token, raw_body)` — `token` is `""` if the email never
    showed up within `attempts * interval` seconds.
    """
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

    Mirrors `flatPrice()` in `frontend/opsgrids-dashboard/src/routes/Marketplace.tsx` exactly —
    that function (not "the first published version", which need not be the priced one) is what
    decides whether a card's "Start 14-day trial" button exists at all.
    """
    for plan in plans:
        for version in plan.get("versions", []):
            if version.get("state") != "published":
                continue
            price = next(
                (
                    p
                    for p in version.get("prices", [])
                    if p.get("kind") == "flat" and p.get("amount_minor") is not None
                ),
                None,
            )
            if price is not None:
                return version, price
    return None
