"""HTTP helpers every e2e module shares: sign-in, bearer headers, token claims, and the
abort-on-failed-step pair the edge scripts use.

Imported as a sibling (`from _http import ...`): the journeys run with `e2e/` on `sys.path`
(pytest's rootdir), and the `edge_*` scripts with `e2e/` as `sys.path[0]`. Nothing here talks to
anything until a function is called.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from edge_lib import StepLog


def token_claims(token: str) -> dict[str, Any]:
    """The token's payload, unverified.

    The gateway verifies; this asserts what auth *put in*, which is a different question
    from whether the signature is good.
    """
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return dict(json.loads(base64.urlsafe_b64decode(payload)))


def login(client: httpx.Client, email: str, password: str, org_id: str) -> str:
    """Sign in through the gateway and return a usable access token.

    Belonging to several organizations makes sign-in two steps: the first returns a pre-auth
    token and the memberships, the second exchanges it for a real one. Handled here rather
    than assumed away, because assuming one organization is precisely the bug this platform's
    identity model exists to avoid.
    """
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, (
        f"sign-in failed: {response.status_code} {response.text[:400]}. If this is a 401, check "
        "auth-service can reach account-service's /internal/v1 — a directory it cannot read "
        "looks exactly like a wrong password, by design."
    )
    body = response.json()
    if body.get("access_token"):
        return str(body["access_token"])

    assert body.get("status") == "org_selection_required", (
        f"sign-in returned neither a token nor an organization picker: {body}"
    )
    chosen = client.post(
        "/api/v1/auth/select-org",
        json={"pre_auth_token": body["pre_auth_token"], "organization_id": org_id},
    )
    assert chosen.status_code == 200, chosen.text[:400]
    return str(chosen.json()["access_token"])


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def owner_token(client: httpx.Client, tenant: dict[str, Any]) -> str:
    """A fresh access token for a fixture tenant's owner."""
    return login(client, tenant["owner"]["email"], tenant["password"], tenant["org_id"])


class Abort(Exception):
    """Raised when a step this product's later steps depend on failed outright. Caught once,
    per product, in `run_product` — the step itself is already logged as FAIL by the time this
    is raised; this only stops the dependent steps that would otherwise fail confusingly."""


def require(log: StepLog, name: str, ok: bool, detail: str = "") -> None:
    """Log a step; abort this product's remaining steps if it failed."""
    log.step(name, ok, detail)
    if not ok:
        raise Abort(name)
