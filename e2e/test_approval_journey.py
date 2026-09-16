"""The Wave 0 exit journey: one attendance correction, through the whole platform.

The exit criterion for Wave 0, stated at the start of this build, is

    tenant provisioned, org hierarchy loaded, approval routed with delegation and SLA escalation.

This is that sentence, executed against real processes over real HTTP. Nine services are running,
seven background relays and consumers are draining real queues on a real broker, and every hop
below is a contract between two of them — which is the only kind of defect this suite exists to
catch, and the kind every service's own unit suite is structurally unable to see.

    1  a fresh tenant is provisioned                 account-service's own creation path
    2  approval 403s before the reconcile            ← ADR-0007: the gap that makes step 3 real
    3  the reconcile delivers `core.approval.*`      tools/reconcile_permissions.py, for real
    4  the owner activates HRMS                      product → relay → account, two services
    5  five colleagues join by invitation            no token, over HTTP, as a stranger would
    6  HR onboards them into a reporting line        employee-service, a real three-deep chain
    7  their tokens carry a subject reference        ← the membership↔employee link (see below)
    8  the hierarchy reaches approval                employee's events → approval's copy
    9  HR sets the SLA policy, a member may not      slice 4's policy admin, both directions
    10 a correction routes to the line manager       attendance → approval, and back again
    11 the manager delegates; the step re-points     ADR-0006 §5, proven from two inboxes
    12 the SLA clock is armed; the sweep runs        the real sweep, on the database's clock
    13 the delegate decides; attendance applies it   approval → attendance → approval
    14 a colleague who is not a party gets 404       ids cannot be probed (ADR-0006 §11)
    15 another tenant gets 404, not 403              fail closed on the binding, not the grant

Steps run in file order and share state through the `wave0` fixture, because that is what a
journey is: step 13 cannot mean anything if step 11 did not happen.

**Two things this journey does not prove. Both are stated rather than papered over, because a
step that reads as coverage and is not is worse than an absent one.**

*The membership → employee link has no writer.* `account.memberships.employee_id` is what
auth-service turns into the `sref` claim that every OWN-scoped check in the estate resolves
against, and **nothing in `services/` writes it**. Step 7 completes it through a seeder
(`_link_employee.py`, which records the search), exactly as `_seed_account.py` already completes
`is_owner`. Everything that link makes possible is then proven over HTTP: the member signs in
again, their token carries `sref`, and they punch, request, delegate and decide as themselves.

*The escalation ladder cannot be driven from outside.* Step 12 proves the SLA clock is armed off
the policy, that the sweep's cross-tenant discovery finds the step, and that its re-check under
the lock correctly refuses to act early. It stops there, and `test_12`'s docstring sets out
exactly why: `escalate_step` reads the database's real clock inside its own transaction, no seam
moves it, and the shortest deadline the product will accept is a fifteen-minute floor enforced as
a database CHECK. The escalation *action* is covered by approval's own suite, which can insert a
step whose deadline is already past; an HTTP journey has no equivalent move.

**On clocks.** Approval measures every deadline against PostgreSQL's `now()` and nothing else
(ADR-0006 §8). So does this file: the delegation window in step 11 and the sweep instant in step
12 are both derived from the database's clock, never this process's. A container clock that has
drifted from the database's has already cost this project a debugging session; nothing here can
reproduce it. There is no `sleep` for real time anywhere in this file — every wait is a poll on a
condition, with the reason for the wait named in the failure message.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from test_journey import _claims, auth_header, login

pytestmark = pytest.mark.e2e

#: The product the approval journey needs. It spans employee-service and attendance-service, and
#: both of its gateway prefixes are entitlement-gated on it.
PRODUCT = "hrms"

#: The one request type approval routes today (`approval/app/services.KNOWN_REQUEST_TYPES`).
REQUEST_TYPE = "hrms.time.regularization"

#: The SLA the journey sets, in minutes. 15 is `ApprovalPolicy`'s own floor
#: (`ck_approval_policies_sla_minutes_range`) — the shortest deadline the product will accept, so
#: the journey configures a real policy rather than leaning on the 24-hour default.
SLA_MINUTES = 15

#: How far past `now()` the sweep's discovery looks. Comfortably past `SLA_MINUTES` so the step
#: is unambiguously overdue, and short enough that it cannot reach a deadline the journey has not
#: created. See `_sweep.py` for why this is not a `sleep`.
SWEEP_MINUTES_AHEAD = SLA_MINUTES + 5

#: Long enough for a relay to drain, a broker to deliver and a consumer to commit on a loaded
#: laptop; short enough that a genuinely broken hop fails the suite rather than hanging it.
SETTLE = 45.0

MEMBER_PASSWORD = "wave0-Sufficiently-Long-Passw0rd!"

#: The cast. `reports_to` names another key in this same mapping, so the reporting line is
#: declared once and built in order: grandmanager ← manager ← requester, with the delegate and
#: the bystander hung off the top so that neither is anywhere in the requester's own chain.
#:
#: The bystander matters: they hold exactly the same roles as everyone else and are a party to
#: nothing, which is the only way to tell "this request is not visible to you" apart from "you
#: cannot read requests at all".
CAST: dict[str, dict[str, Any]] = {
    "grandmanager": {"name": "Gita Grandmanager", "reports_to": None},
    "manager": {"name": "Mohan Manager", "reports_to": "grandmanager"},
    "requester": {"name": "Rhea Requester", "reports_to": "manager"},
    "delegate": {"name": "Dev Delegate", "reports_to": "grandmanager"},
    "bystander": {"name": "Bala Bystander", "reports_to": "grandmanager"},
}


@pytest.fixture(scope="session")
def wave0() -> dict[str, Any]:
    """State carried between the ordered steps — ids, employee records, what each step proved."""
    return {}


@pytest.fixture(scope="session")
def gateway(platform: Any) -> Any:
    """One long-lived client at the gateway, shared by every persona in this journey.

    Session-scoped rather than per-test because the `Session` objects below hold access tokens
    across steps, and a token is only meaningful alongside the connection pool and the client IP
    the gateway's rate limiter counts against.
    """
    with httpx.Client(base_url=platform.gateway, timeout=20.0) as client:
        yield client


def until(
    what: str, predicate: Callable[[], Any], *, timeout: float = SETTLE, detail: str = ""
) -> Any:
    """Poll `predicate` until it returns something truthy, then return it.

    A condition, never an interval. Every wait in this file is for a specific observable fact —
    "the role exists", "the request reached the inbox" — so a failure names the fact that never
    became true instead of reporting a timeout the reader has to decode. `detail` is evaluated
    only on failure, so the message can afford to make another request.
    """
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.5)
    raise AssertionError(
        f"timed out after {timeout:g}s waiting for {what}. Last saw: {last!r}. {detail}"
    )


class Session:
    """One signed-in person, behaving the way a real client does.

    It signs in once, keeps its access token, and mints a new one **only when the platform tells
    it the old one is stale** — a 401 whose problem type is `token-stale`, which is the platform's
    own instruction to refresh (ADR-0000 §4: a grant change bumps the organization's permission
    version and every token minted before it is refused).

    An earlier version of this file minted a fresh token on every call instead, reasoning that
    almost every step here changes a grant. That was wrong twice over. It is not what a client
    does — no browser re-authenticates between two clicks — and, more concretely, the gateway
    rate-limits **anonymous** requests to 60 a minute per client IP, `/api/v1/auth/login`
    included. A journey that logs in a hundred times does not merely slow down: it spends a
    budget that `test_journey.py` is also drawing on from the same address, and the other suite
    starts failing with 429s that look like defects in code it never touched. That is the shape
    of cross-test interference this suite exists to avoid, and one test file is not entitled to
    exhaust a shared platform limit.

    The refresh-on-stale behaviour is not only cheaper, it is worth asserting: every step of this
    journey that changes a grant exercises the full invalidate-and-refresh cycle rather than
    sidestepping it.
    """

    def __init__(self, client: httpx.Client, email: str, password: str, org_id: str) -> None:
        self._client = client
        self._email = email
        self._password = password
        self._org_id = org_id
        self._token: str | None = None
        #: How many times the platform made this client refresh. Read by the journey's own
        #: assertions, so "the token went stale" is a fact a step can check rather than a guess.
        self.refreshes = 0

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = login(self._client, self._email, self._password, self._org_id)
        return self._token

    @property
    def headers(self) -> dict[str, str]:
        return auth_header(self.token)

    def expire(self) -> None:
        """Drop the held token without asserting anything — for the steps that want to *prove*
        the old one is refused before moving on."""
        self._token = None

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, headers=self.headers, **kwargs)
        if response.status_code == 401 and "token-stale" in response.text:
            self._token = None
            self.refreshes += 1
            response = self._client.request(method, path, headers=self.headers, **kwargs)
        return response

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", path, **kwargs)


def signed_in(gateway: httpx.Client, wave0: dict[str, Any], key: str) -> Session:
    """The `Session` for one of this journey's people, created on first use and kept."""
    sessions: dict[str, Session] = wave0.setdefault("sessions", {})
    if key not in sessions:
        if key == "owner":
            person, password = wave0["tenant"]["owner"], wave0["tenant"]["password"]
        else:
            person, password = wave0["cast"][key], MEMBER_PASSWORD
        sessions[key] = Session(gateway, person["email"], password, wave0["tenant"]["org_id"])
    return sessions[key]


def inbox(session: Session) -> list[dict[str, Any]]:
    response = session.get("/api/v1/approvals/inbox")
    assert response.status_code == 200, f"{response.status_code} {response.text[:300]}"
    return list(response.json()["items"])


def inbox_item(session: Session, request_id: str) -> dict[str, Any] | None:
    return next((i for i in inbox(session) if i["id"] == request_id), None)


def _candidates(report: dict[str, Any]) -> set[str]:
    """The request ids one sweep pass's discovery returned. See `_sweep.py` for why the journey
    asserts on this rather than on the pass's platform-wide counts."""
    return {c["request_id"] for c in report["candidates"]}


class TestWave0ExitJourney:
    """Ordered. Each step depends on the one before it."""

    # ------------------------------------------------------ 1-3. provision, then reconcile

    def test_01_a_new_tenants_owner_cannot_reach_approval_at_all(
        self, gateway: httpx.Client, approval_tenant: dict[str, Any], wave0: dict[str, Any]
    ) -> None:
        """**ADR-0007's gap, from the customer's chair.**

        The organization has just been created from account-service's in-code
        `CORE_REGISTRATION`, which cannot contain approval-service's slice of `core` — the
        manifest is a merge of every contributing service and the in-code copy is a strict
        subset of it (ADR-0007 §2). So `ORG_OWNER` exists, is held, is `is_system`, and carries
        none of the four `core.approval.*` grants.

        The symptom is a 403 on a platform surface every customer is supposed to have. This step
        exists so that step 3 is a proof rather than a ritual: without it, a reconcile that did
        nothing at all would be indistinguishable from one that worked.
        """
        wave0["tenant"] = approval_tenant
        owner = signed_in(gateway, wave0, "owner")

        response = owner.get("/api/v1/approvals/policies")
        assert response.status_code == 403, (
            f"a brand-new organization's owner reached {REQUEST_TYPE} policy on a tenant that "
            f"has never been reconciled ({response.status_code} {response.text[:300]}). Either "
            "ADR-0007's gap has been closed somewhere else — in which case step 3 is now "
            "testing nothing and this journey needs rewriting — or the endpoint has lost its "
            "permission dependency."
        )
        # The surface is genuinely reachable; it is authorization that refuses, not routing.
        assert gateway.get("/api/v1/approvals/info").status_code in (200, 401), (
            "approval-service is not answering behind the gateway at all — the 403 above would "
            "then prove nothing about permissions"
        )

    def test_02_a_dry_run_reconcile_reports_the_diff_and_writes_nothing(
        self, gateway: httpx.Client, wave0: dict[str, Any], reconcile: Any
    ) -> None:
        """`--dry-run` is the expected first invocation (ADR-0007 §3), and its distinguishing
        property is that it is **inert**: it computes and prints the same diff, and the tenant
        is exactly as unable to approve anything afterwards as before.

        Proven by the same request step 1 made, not by reading the database — an operator's
        confidence in `--dry-run` is worth exactly what a customer would have experienced.
        """
        org_id = wave0["tenant"]["org_id"]
        result = reconcile("core", org_id, dry_run=True)

        assert result["summary"]["dry_run"] is True
        assert result["summary"]["changed"] == 1, (
            f"a dry run against a never-reconciled organization found nothing to do: {result}. "
            "That would mean the manifest and the in-code registration agree, which ADR-0007 §2 "
            "says they cannot once approval contributes to `core`."
        )

        owner = signed_in(gateway, wave0, "owner")
        assert owner.get("/api/v1/approvals/policies").status_code == 403, (
            "a --dry-run reconcile granted something. It is documented as writing nothing — "
            "not even the roles and permission rows a real run would upsert."
        )
        assert owner.refreshes == 0, (
            "a --dry-run reconcile bumped the organization's permission version: every client "
            "in the tenant just refreshed for a change that was never written"
        )

    def test_03_the_reconcile_delivers_core_approval_to_roles_that_already_existed(
        self, gateway: httpx.Client, wave0: dict[str, Any], reconcile: Any
    ) -> None:
        """**The step that makes the rest of this journey possible.**

        `install_registration` used to skip any role code that already existed, so a permission
        added to `MEMBER` or `ORG_OWNER` reached new organizations and no existing ones, forever,
        with no error anywhere. The reconciler widens them instead (ADR-0007 §1, union only).

        Three things are asserted, in the order a customer would notice them: the run reports a
        change; the token minted before it is refused as **stale** rather than quietly continuing
        to work (§5 — the `pv` bump is the mechanism by which a grant change takes effect at all);
        and the endpoint that was 403 now answers with the organization's honest defaults.
        """
        org_id = wave0["tenant"]["org_id"]
        owner = signed_in(gateway, wave0, "owner")
        stale = owner.headers  # captured before the reconcile, and deliberately not refreshed

        result = reconcile("core", org_id)
        summary = result["summary"]
        assert summary["changed"] == 1, f"the reconcile reported no change: {result}"

        org_result = result["organizations"][0]
        assert org_result["grants_added"] > 0, (
            f"the reconcile reported a change with no grants added: {org_result}. Creating a "
            "role nobody holds is not what makes an existing member able to approve anything."
        )

        # Sent raw rather than through the session, because the session would transparently
        # refresh and hide the very behaviour being asserted.
        response = gateway.get("/api/v1/approvals/policies", headers=stale)
        assert response.status_code == 401, (
            f"the token minted before the reconcile still resolves ({response.status_code}). "
            "ADR-0007 §5 makes the `pv` bump non-optional precisely so a grant change cannot "
            "wait for token expiry."
        )
        assert response.json().get("type", "").endswith("token-stale"), response.text[:300]

        response = owner.get("/api/v1/approvals/policies")
        assert owner.refreshes == 1, (
            "the client did not have to refresh, so the assertion above was about a token this "
            "session had already replaced"
        )
        assert response.status_code == 200, (
            f"still refused after the reconcile: {response.status_code} {response.text[:400]}"
        )
        policies = {p["request_type"]: p for p in response.json()}
        assert REQUEST_TYPE in policies, (
            f"the settings surface lists {sorted(policies)}; it is specified to list every type "
            "approval knows how to route, at its defaults, even before anyone has edited one"
        )
        assert policies[REQUEST_TYPE]["sla_minutes"] == 1440
        assert policies[REQUEST_TYPE]["enabled"] is True

        # Idempotency (ADR-0007 §5): a second run computes an empty diff, writes nothing, bumps
        # nothing. It is what makes the command safe to re-run after a partial failure — and
        # what makes this suite safe to run twice in a row.
        again = reconcile("core", org_id)
        assert again["summary"]["changed"] == 0, (
            f"a second reconcile changed something again: {again}. The union is supposed to "
            "converge; one that does not would bump `pv` on every deploy forever."
        )

    # ---------------------------------------------------------------- 4. activate HRMS

    def test_04_the_owner_activates_the_product_the_journey_needs(
        self, gateway: httpx.Client, wave0: dict[str, Any]
    ) -> None:
        """Activation, and the event that carries it from product-service to account-service.

        Asserted on the *effect* rather than on the outbox: the roles HRMS ships have to exist in
        this organization before anyone can be invited into them, and the entitlement has to
        reach the token before the gateway will proxy a single HRMS request. Both are only true
        once product's relay published and account's consumer committed — the hop a stub always
        fakes.
        """
        owner = signed_in(gateway, wave0, "owner")
        response = owner.post("/api/v1/products/activations", json={"product_key": PRODUCT})
        assert response.status_code == 201, (
            f"activation failed: {response.status_code} {response.text[:400]}. A 422 usually "
            f"means the registry still has {PRODUCT!r} marked 'soon' — see _seed_product.py."
        )

        def roles_installed() -> set[str] | None:
            listing = owner.get("/api/v1/roles")
            if listing.status_code != 200:
                return None
            codes = {r["code"] for r in listing.json()}
            return codes if {"HR_ADMIN", "EMPLOYEE"} <= codes else None

        until(
            "HRMS's role templates to be installed by account-service's consumer",
            roles_installed,
            detail=(
                "product-service reported the activation. If the roles never appear, the event "
                "did not cross: check the product relay is draining and the account consumer is "
                "bound to this run's broker vhost."
            ),
        )

        # A fresh sign-in, not the session's refresh: `ent` is computed at sign-in from what
        # product-service says the organization has, and this asserts what auth *put in*.
        owner.expire()
        assert PRODUCT in _claims(owner.token)["ent"], (
            "the re-issued token does not carry the product just activated, so the gateway will "
            "refuse every HRMS request with 402 — auth is not seeing product-service's "
            "activations"
        )

    # ------------------------------------------------------- 5-7. the people, and their ids

    def test_05_five_colleagues_join_by_invitation(
        self, gateway: httpx.Client, wave0: dict[str, Any], seed_password: Any
    ) -> None:
        """Each accepts with **no Authorization header**, the way a stranger with an email must.

        Every one of them takes `MEMBER` (which, after step 3, carries the OWN-scoped
        `core.approval.*` grants) and `EMPLOYEE` (which, after step 4, carries the OWN-scoped
        attendance grants). Nobody in this cast is an administrator of anything: the journey has
        to work for ordinary employees, or ADR-0006 §6's eligibility rules are doing nothing.
        """
        tenant = wave0["tenant"]
        owner = signed_in(gateway, wave0, "owner")
        cast: dict[str, dict[str, Any]] = {}

        for key, spec in CAST.items():
            email = f"e2e-gamma-{key}-{tenant['run']}@example.com"
            # Accepting an invitation grants roles, which bumps the organization's permission
            # version and stales every token minted before it — including the owner's. The
            # session refreshes on the next 401 rather than pre-emptively, which is exactly what
            # a real client does and what keeps this loop inside the gateway's login budget.
            created = owner.post(
                "/api/v1/invitations",
                json={"email": email, "role_codes": ["MEMBER", "EMPLOYEE"], "product_codes": []},
            )
            assert created.status_code == 201, (
                f"inviting {key}: {created.status_code} {created.text[:400]}. A 422 naming a "
                "role code means step 4's activation has not landed in this organization."
            )
            accepted = gateway.post(
                "/api/v1/invitations/accept",
                json={"token": created.json()["token"], "full_name": spec["name"]},
            )
            assert accepted.status_code in (200, 201), (
                f"{key} could not accept over HTTP: {accepted.status_code} {accepted.text[:400]}"
            )
            member = accepted.json()
            assert {"MEMBER", "EMPLOYEE"} <= set(member["roles"]), member["roles"]

            seed_password(member["user_id"], MEMBER_PASSWORD)
            cast[key] = {
                "email": email,
                "name": spec["name"],
                "user_id": member["user_id"],
                "membership_id": member["id"],
            }

        wave0["cast"] = cast

    def test_06_hr_onboards_them_into_a_three_deep_reporting_line(
        self, gateway: httpx.Client, wave0: dict[str, Any]
    ) -> None:
        """employee-service's real onboarding path, building the line approval will route on.

        Created in dependency order, because `reporting_manager_id` is validated against an
        employee that already exists in this organization — naming one from another tenant reads
        as "not found here", which is the check that makes the hierarchy tenant-safe.

        The owner does this, holding `hrms.people.employee.write:ORG` from the `HR_ADMIN` role
        step 4 installed. No member of the cast could: their `EMPLOYEE` role does not carry it,
        which is the point.
        """
        run = wave0["tenant"]["run"]
        owner = signed_in(gateway, wave0, "owner")

        # Insertion order of `CAST` is dependency order — a manager is declared before anyone
        # who reports to them — so a single pass suffices and a later edit that breaks the
        # ordering fails loudly on the lookup below rather than silently building a flat tree.
        for index, (key, spec) in enumerate(CAST.items(), start=1):
            manager_key = spec["reports_to"]
            manager_employee_id = wave0["cast"][manager_key]["employee_id"] if manager_key else None
            assert manager_key is None or manager_employee_id, (
                f"{key} reports to {manager_key}, which has not been onboarded yet — CAST is no "
                "longer in dependency order"
            )
            response = owner.post(
                "/api/v1/employees",
                json={
                    "employee_no": f"W0-{run}-{index:02d}",
                    "full_name": spec["name"],
                    "membership_id": wave0["cast"][key]["membership_id"],
                    "reporting_manager_id": manager_employee_id,
                    "location_code": "PUN",
                },
            )
            assert response.status_code == 201, (
                f"onboarding {key}: {response.status_code} {response.text[:400]}"
            )
            employee = response.json()
            assert employee["reporting_manager_id"] == manager_employee_id
            wave0["cast"][key]["employee_id"] = employee["id"]

    def test_07_the_membership_employee_link_reaches_the_token(
        self, gateway: httpx.Client, wave0: dict[str, Any], link_employee: Any
    ) -> None:
        """`sref` — the claim every OWN-scoped check in the estate resolves against.

        The link itself is completed by a seeder because nothing in `services/` writes that
        column (see `_link_employee.py`; this is a real gap, reported rather than hidden). What
        is proven here is the whole chain that depends on it, over HTTP: auth-service reads the
        membership when it mints the token, the claim lands, and the gateway is willing to
        forward it.

        Asserted before anything is punched or approved, because a `None` subject reference does
        not produce an error further down — it produces an empty inbox and a silent 403, which
        would be read as a routing bug ten steps later.
        """
        link_employee(
            [(person["membership_id"], person["employee_id"]) for person in wave0["cast"].values()]
        )

        for key, person in wave0["cast"].items():
            token = signed_in(gateway, wave0, key).token
            assert _claims(token).get("sref") == person["employee_id"], (
                f"{key}'s token carries sref={_claims(token).get('sref')!r}, not their employee "
                f"id {person['employee_id']}. Every OWN-scoped check below would then resolve "
                "against nothing."
            )

    # ------------------------------------------------------------- 8. the hierarchy crosses

    def test_08_the_hierarchy_reaches_approval_through_employee_events(
        self, gateway: httpx.Client, wave0: dict[str, Any]
    ) -> None:
        """**The org hierarchy, loaded — through the real event stream, not the backfill.**

        employee-service stages `hrms.people.employee.onboarded` carrying the full hierarchy
        state of the employee after the change (ADR-0006 §2: full state, never a diff); its relay
        publishes; approval's consumer applies the version-gated upsert into its own copy. Three
        services, a broker, and two schemas that share no foreign key.

        Read back through `GET /approvals/chain/{id}` rather than out of the database, because
        the chain endpoint is what the product actually serves — and because it proves the copy
        is not merely populated but *walkable*: the manager, then the manager's manager, then the
        HR/unassigned fallback that makes an exhausted chain a well-formed list rather than an
        empty one.
        """
        cast = wave0["cast"]
        owner = signed_in(gateway, wave0, "owner")

        def chain_walkable() -> list[dict[str, Any]] | None:
            response = owner.get(f"/api/v1/approvals/chain/{cast['requester']['employee_id']}")
            if response.status_code != 200:
                return None
            rungs = response.json()["chain"]
            return rungs if len(rungs) >= 3 else None

        rungs = until(
            "the requester's reporting line to reach approval's copy",
            chain_walkable,
            detail=(
                "employee-service accepted the onboarding. If the chain never fills, the event "
                "did not cross: check the employee relay is draining and approval's consumer is "
                "bound to hrms.people.employee.* on this run's vhost."
            ),
        )

        assert rungs[0]["employee_id"] == cast["manager"]["employee_id"], (
            f"the first rung above the requester is {rungs[0]}, not their line manager"
        )
        assert rungs[0]["display_name"] == cast["manager"]["name"], (
            "the rung resolved but carries no name — the event's hierarchy payload did not "
            f"arrive intact: {rungs[0]}"
        )
        assert rungs[1]["employee_id"] == cast["grandmanager"]["employee_id"], rungs[1]
        assert rungs[-1]["is_fallback"] is True, (
            f"the chain does not end in the HR/unassigned fallback: {rungs[-1]}"
        )
        assert cast["delegate"]["employee_id"] not in {r["employee_id"] for r in rungs}, (
            "the delegate is in the requester's own chain, which would make step 11's "
            "re-pointing indistinguishable from ordinary routing"
        )

    # ---------------------------------------------------------------- 9. the policy admin

    def test_09_hr_sets_the_sla_and_an_ordinary_member_may_not(
        self, gateway: httpx.Client, wave0: dict[str, Any]
    ) -> None:
        """Slice 4's policy administration, both directions, over HTTP.

        `policy.write` is floored at ORG — there is no "own" reading of organization-wide SLA
        configuration to collapse to — so the requester, who holds `request.read:OWN` and can see
        the settings perfectly well, is refused the write. Both halves matter: a UI gate hides
        the control, it does not protect the setting.

        Set **before** the correction is raised, deliberately: `due_at` is computed when a step
        opens, off the policy that exists at that moment, so a policy written afterwards would
        not govern the step step 12 escalates.
        """
        member = signed_in(gateway, wave0, "requester")

        refused = member.put(
            f"/api/v1/approvals/policies/{REQUEST_TYPE}",
            json={"sla_minutes": SLA_MINUTES, "enabled": True},
        )
        assert refused.status_code == 403, (
            f"an ordinary member set the organization's SLA policy ({refused.status_code} "
            f"{refused.text[:300]}). policy.write is floored at ORG."
        )
        readable = member.get("/api/v1/approvals/policies")
        assert readable.status_code == 200, (
            "a member who cannot write the policy also cannot read it — the settings screen's "
            f"read-only state depends on this: {readable.status_code} {readable.text[:300]}"
        )

        response = signed_in(gateway, wave0, "owner").put(
            f"/api/v1/approvals/policies/{REQUEST_TYPE}",
            json={"sla_minutes": SLA_MINUTES, "enabled": True},
        )
        assert response.status_code == 200, f"{response.status_code} {response.text[:400]}"
        assert response.json()["sla_minutes"] == SLA_MINUTES
        assert response.json()["enabled"] is True

    # ------------------------------------------------------------ 10. the request routes

    def test_10_an_attendance_correction_routes_to_the_line_manager(
        self, gateway: httpx.Client, wave0: dict[str, Any]
    ) -> None:
        """The correction itself: punched, asked for, routed, and acknowledged back.

        Four crossings in one step, and each is asserted where a customer would see it.
        attendance-service opens the regularization and stages
        `hrms.time.regularization.requested`; approval's consumer opens a request and routes it
        by the hierarchy copy step 8 filled; approval stages `core.approval.request.opened` and
        attendance's consumer writes the approval id back onto its own row — which is how the
        employee's own screen learns there is something to wait for.

        The manager is reached through their **inbox**, not by id: the inbox is the query that
        decides who may act, and a request that routed correctly but cannot be found is not
        routed at all.
        """
        cast = wave0["cast"]
        requester = signed_in(gateway, wave0, "requester")

        punched = requester.post(
            "/api/v1/attendance/check-in",
            json={"work_mode": "office", "source": "web", "location_code": "PUN"},
        )
        assert punched.status_code == 201, (
            f"check-in failed: {punched.status_code} {punched.text[:400]}. A 403 naming a "
            "subject reference means step 7's link did not reach this token."
        )
        record = punched.json()
        assert record["status"] == "present"
        assert record["employee_id"] == cast["requester"]["employee_id"]
        wave0["attendance_id"] = record["id"]

        asked = requester.post(
            f"/api/v1/attendance/{record['id']}/regularization-requests",
            json={
                "requested_status": "regularized",
                "reason": "Client site all afternoon; forgot to punch out.",
            },
        )
        assert asked.status_code == 201, f"{asked.status_code} {asked.text[:400]}"
        regularization = asked.json()
        assert regularization["status"] == "pending"
        wave0["source_ref"] = regularization["id"]

        manager = signed_in(gateway, wave0, "manager")
        item = until(
            "the correction to reach the line manager's approval inbox",
            lambda: next(
                (i for i in inbox(manager) if i["source_ref"] == wave0["source_ref"]),
                None,
            ),
            detail=(
                "attendance accepted the request. If it never reaches the inbox, either the "
                "event did not cross (attendance relay → approval consumer) or the routing walk "
                "found nobody — step 8 proved the chain is walkable, so suspect the former."
            ),
        )

        assert item["request_type"] == REQUEST_TYPE
        assert item["requester_employee_id"] == cast["requester"]["employee_id"]
        assert item["requester_name"] == cast["requester"]["name"]
        assert item["inbox_reason"] == "assigned", (
            f"the manager sees this as {item['inbox_reason']!r}; nothing has been delegated or "
            "escalated yet"
        )
        assert item["due_at"] is not None, (
            "the step opened with no deadline, so the SLA sweep will never consider it. The "
            f"policy set in step 9 ({SLA_MINUTES} minutes, enabled) should have armed one."
        )
        wave0["request_id"] = item["id"]

        assert inbox_item(signed_in(gateway, wave0, "grandmanager"), item["id"]) is None, (
            "the request reached the manager's manager too. Routing opens one step, at the "
            "first rung — everyone above it is an escalation target, not a current approver."
        )
        assert inbox_item(requester, item["id"]) is None, (
            "the requester can see their own request in their approval inbox — the inbox "
            "excludes the caller's own requests by construction"
        )

        # The return leg of `core.approval.request.opened`: attendance learns the approval id.
        acknowledged = until(
            "attendance to record the approval request id against its own row",
            lambda: (
                (body := requester.get(f"/api/v1/attendance/{wave0['attendance_id']}").json())
                and (body.get("regularization") or {}).get("approval_request_id")
            ),
            detail="approval staged the event; check its relay and attendance's consumer.",
        )
        assert acknowledged == item["id"], (
            f"attendance recorded approval request {acknowledged}, not {item['id']}"
        )

    # ------------------------------------------------------------------ 11. delegation

    def test_11_the_manager_delegates_and_the_open_step_re_points(
        self, gateway: httpx.Client, wave0: dict[str, Any], db_clock: Any
    ) -> None:
        """ADR-0006 §5: a delegation created now re-points the steps already waiting on it.

        The window is anchored to **PostgreSQL's** clock, because that is what
        `services.create_delegation` compares `starts_at <= now` against. Anchoring it to this
        process's clock would make the re-pointing depend on two machines agreeing, and a
        container whose clock had drifted would produce a delegation that is created, is
        reported active, and quietly re-points nothing.

        Proven from **both** inboxes. A re-point that only adds the delegate leaves two people
        holding the same step; one that only removes the manager loses it. The pair is the
        assertion; neither half alone is.
        """
        cast = wave0["cast"]
        now = db_clock()
        manager = signed_in(gateway, wave0, "manager")

        created = manager.post(
            "/api/v1/approvals/delegations",
            json={
                "delegate_employee_id": cast["delegate"]["employee_id"],
                "starts_at": (now - timedelta(minutes=1)).isoformat(),
                "ends_at": (now + timedelta(days=7)).isoformat(),
            },
        )
        assert created.status_code == 201, f"{created.status_code} {created.text[:400]}"
        delegation = created.json()
        assert delegation["status"] == "active", delegation
        assert delegation["repointed_count"] == 1, (
            f"the delegation re-pointed {delegation['repointed_count']} open steps; exactly one "
            "was waiting on this manager. Zero means the window was not active when it was "
            "created — check the clock the window was anchored to."
        )

        delegate = signed_in(gateway, wave0, "delegate")
        item = until(
            "the re-pointed request to appear in the delegate's inbox",
            lambda: inbox_item(delegate, wave0["request_id"]),
        )
        assert item["inbox_reason"] == "delegated_to_me", item
        assert item["from_name"] == cast["manager"]["name"], (
            f"the delegate is not told who this came from: {item}"
        )

        handed_over = inbox_item(manager, wave0["request_id"])
        assert handed_over is not None, (
            "the manager lost sight of a request they delegated rather than handed over — they "
            "remain a party to it and it stays visible, marked as delegated away"
        )
        assert handed_over["inbox_reason"] == "delegated_away", handed_over

        detail = manager.get(f"/api/v1/approvals/requests/{wave0['request_id']}")
        assert detail.status_code == 200, detail.text[:400]
        body = detail.json()
        assert body["approver_employee_id"] == cast["delegate"]["employee_id"], (
            f"the open step still names {body['approver_employee_id']}; re-pointing moves it to "
            "the delegate"
        )
        assert body["acting_for_employee_id"] == cast["manager"]["employee_id"], (
            "the step does not record who the delegate is acting for, so the escalation in step "
            "12 would walk up the delegate's line instead of the requester's"
        )
        wave0["due_at"] = body["steps"][-1]["due_at"]
        assert wave0["due_at"] is not None, (
            "re-pointing dropped the deadline. ADR-0006 §5 carries the predecessor's `due_at` "
            "through unchanged — otherwise creating and revoking delegations is a way to never "
            "escalate."
        )

    # ------------------------------------------------------------------ 12. the SLA clock

    def test_12_the_sla_clock_is_armed_and_the_sweep_refuses_to_act_early(
        self, gateway: httpx.Client, wave0: dict[str, Any], sweep: Any
    ) -> None:
        """**The SLA sweep, for real — and an honest statement of where it stops.**

        `_sweep.py` runs approval's own `EscalationSweep` with its production session factories:
        discovery on the BYPASSRLS outbox role (as the app role it would match zero rows, and
        every overdue step on the platform would sit unescalated forever, looking exactly like a
        healthy idle sweep — the same silent stall that killed the outbox relay), and every write
        on the NOBYPASSRLS app role under the tenant policy. Nothing is patched.

        Three real properties are asserted.

        **The policy governs the clock, and re-pointing does not restart it.** The step routing
        opened carries a deadline of exactly its opening instant plus the `sla_minutes` step 9
        wrote over HTTP — both instants from the API, both stamped by PostgreSQL, so this is the
        product's own clock end to end. The step the delegation re-pointed to then carries that
        *same* deadline verbatim, despite having opened later: ADR-0006 §5's rule, and the reason
        it exists is that otherwise creating and revoking delegations is a way to never escalate.

        **Discovery is real, and finds this row.** Asked to look `SLA_MINUTES + 5` minutes past
        the database's own `now()`, the cross-tenant discovery query returns this step. Asked to
        look at `now()` itself it does not — the step is genuinely not yet due, so the first
        result is discovery working rather than discovery matching everything in sight.

        **The re-check under the lock refuses a stale candidate.** ADR-0006 §8 requires the action
        transaction to ask whether the step is *still* due under its own, later clock read, because
        discovery's read can be arbitrarily stale on a slow tick and two replicas can race. Handed
        a candidate that is not yet due, the sweep closes nothing and writes nothing: the request
        comes back from the API with the same status, the same approver and the same number of
        steps it had before the pass. That guard is what makes concurrent sweepers idempotent, and
        this is the only place in the estate it runs against real rows, real locks and a real
        BYPASSRLS/NOBYPASSRLS split.

        **What this does not prove, stated rather than dressed up.** The journey does not drive an
        escalation to completion, and it cannot:

        * `escalate_step` re-reads `services.db_now` inside its own transaction, and no seam moves
          that instant. `EscalationSweep.tick(now=…)` reaches **only** discovery, so a synthetic
          instant in the future produces exactly the outcome asserted below — the step is found,
          and then left alone. `app/cron.py` documents that parameter as letting a test "exercise
          an SLA-minutes-long ladder in milliseconds against real rows and real locks"; for the
          *action* half that claim does not hold, and this test is the evidence.
        * The shortest deadline the product accepts is 15 minutes, enforced as a database CHECK
          precisely so no path can manufacture overdue steps faster than the sweep drains them. No
          policy this journey may write elapses inside a test run, and waiting one out is a
          fifteen-minute `sleep` by another name.
        * approval's own suite covers the ladder by *inserting* a step whose `due_at` is already
          `db_now() - n` (`tests/helpers.py`, `tests/test_escalation.py`) — an INSERT, which the
          append-only guard permits. An HTTP journey has no such move: `due_at` is frozen by
          `trg_approval_steps_append_only` the moment routing writes it.

        The consequence is specific and belongs in a report, not a comment: `escalated_to_me` and
        `escalated_away` are the only two inbox reasons this suite never observes, and the
        escalation walk's "up from the original approver, never the delegate" rule is covered by
        unit tests alone.
        """
        cast = wave0["cast"]
        owner = signed_in(gateway, wave0, "owner")

        detail = owner.get(f"/api/v1/approvals/requests/{wave0['request_id']}").json()
        routed = next(s for s in detail["steps"] if s["outcome"] == "delegated")
        open_step = next(s for s in detail["steps"] if s["outcome"] == "open")

        armed = datetime.fromisoformat(routed["due_at"]) - datetime.fromisoformat(
            routed["opened_at"]
        )
        assert armed == timedelta(minutes=SLA_MINUTES), (
            f"the routed step's deadline is {armed} after it opened; step 9 set the policy to "
            f"{SLA_MINUTES} minutes. The SLA clock is not being armed off the policy."
        )
        assert open_step["due_at"] == routed["due_at"], (
            "re-pointing moved the deadline. ADR-0006 §5 carries the predecessor's `due_at` "
            "through unchanged, or delegating and revoking becomes a way to never escalate: "
            f"{routed['due_at']} became {open_step['due_at']}"
        )
        assert open_step["opened_at"] > routed["opened_at"], (
            "the re-pointed step did not open after the one it replaced, so the carry-through "
            "above is trivially true rather than a property"
        )

        # Every assertion below names *this run's* request. Discovery is platform-wide, so its
        # counts also include requests earlier runs of this suite left pending — whose fifteen
        # minute deadlines have genuinely elapsed by the time the suite is run again. Asserting
        # on `considered` or `escalated` would make this test pass or fail according to how
        # recently it last ran, which is how a suite acquires a flake that nobody can reproduce.
        mine = wave0["request_id"]

        not_yet = sweep(0)
        assert mine not in _candidates(not_yet), (
            f"discovery considers this step due against the database's own now(): {not_yet}. Its "
            "deadline is minutes away, so either the deadline is wrong or discovery is not "
            "comparing against it."
        )

        report = sweep(SWEEP_MINUTES_AHEAD)
        assert mine in _candidates(report), (
            f"the sweep did not find this step {SWEEP_MINUTES_AHEAD} minutes past the database's "
            f"own now(): {report}. Its deadline is {SLA_MINUTES} minutes out, well inside that "
            "window, so cross-tenant discovery is not seeing it — which is exactly what an inert "
            "sweep running on the app role instead of the outbox role looks like."
        )
        assert report["failed"] == 0, f"the sweep raised on a candidate: {report}"

        unchanged = owner.get(f"/api/v1/approvals/requests/{mine}").json()
        assert unchanged["status"] == "pending", (
            "the sweep decided the request. Escalation only ever widens who may answer it."
        )
        assert unchanged["approver_employee_id"] == cast["delegate"]["employee_id"], (
            f"the sweep escalated a step that is not due under the database's own clock: the "
            f"open step moved to {unchanged['approver_employee_id']}. The re-check under the "
            "lock (ADR-0006 §8) exists to refuse exactly this; without it a stale discovery read "
            "or a losing replica escalates a step twice.\n\nIf this failed because `escalate_step` "
            "gained a seam for `now`, that is good news, and this journey should be extended to "
            "drive the escalation to completion rather than relaxed to accommodate it."
        )
        assert len(unchanged["steps"]) == len(detail["steps"]), (
            f"the sweep wrote a step for a request it was not due to escalate: {report}"
        )

    # -------------------------------------------------------- 13. decided, and applied back

    def test_13_the_delegate_decides_and_attendance_applies_it(
        self, gateway: httpx.Client, wave0: dict[str, Any]
    ) -> None:
        """The round trip closes: approval decides, attendance applies, approval records it.

        The decider is the **delegate**, and that is the point. They hold
        `core.approval.request.decide:OWN` and nothing else — the same grant every member of this
        organization holds — so the permission cannot be what admits them. What admits them is
        that step 11's re-pointing put them on the open step, which is ADR-0006 §6's eligibility
        check rather than a scope check: "holding decide is not enough". A member who is not on
        the step is refused first, so that the 200 below means something.

        Then the product's own leg: `core.approval.request.decided` crosses to
        attendance-service, which re-validates the transition against the record's *current*
        status before writing it, and stages `hrms.time.regularization.applied` back. Asserted on
        the attendance record the employee can see, and on approval's own `application_state` —
        because "approved" and "applied" are different facts and the platform keeps them apart.
        """
        # The control: an ordinary member with the identical grant, not on the step, is refused.
        # Without it, the 200 below would be consistent with no eligibility check at all.
        refused = signed_in(gateway, wave0, "bystander").post(
            f"/api/v1/approvals/requests/{wave0['request_id']}/decision",
            json={"outcome": "approve"},
        )
        assert refused.status_code in (403, 404), (
            f"a colleague who is not on this request decided it ({refused.status_code}): "
            f"{refused.text[:300]}. Every member holds decide:OWN; eligibility is the gate."
        )

        delegate = signed_in(gateway, wave0, "delegate")
        decided = delegate.post(
            f"/api/v1/approvals/requests/{wave0['request_id']}/decision",
            json={"outcome": "approve", "comment": "Confirmed with the client site."},
        )
        assert decided.status_code == 200, (
            f"the delegate could not decide the request they were handed: {decided.status_code} "
            f"{decided.text[:400]}. A 403 means eligibility did not follow the delegation."
        )
        body = decided.json()
        assert body["status"] == "approved", body
        assert body["decided_by_employee_id"] == wave0["cast"]["delegate"]["employee_id"]
        assert body["acting_for_employee_id"] == wave0["cast"]["manager"]["employee_id"], (
            "the decision does not record whose authority it was taken under — an audit of who "
            f"approved this cannot reach the line manager: {body}"
        )

        requester = signed_in(gateway, wave0, "requester")
        applied = until(
            "attendance to apply the approved correction to the record itself",
            lambda: (
                (record := requester.get(f"/api/v1/attendance/{wave0['attendance_id']}").json())
                and record["status"] == "regularized"
                and record
            ),
            detail=(
                "approval recorded the decision. If the record never moves, the decision event "
                "did not cross (approval relay → attendance consumer), or the transition was no "
                "longer legal from the record's current status."
            ),
        )
        assert applied["regularization"]["status"] == "approved", applied["regularization"]

        settled = until(
            "approval to record the product's return leg",
            lambda: (
                (
                    detail := requester.get(
                        f"/api/v1/approvals/requests/{wave0['request_id']}"
                    ).json()
                )
                and detail.get("application_state") == "applied"
                and detail
            ),
            detail=(
                "attendance applied the correction and stages "
                "`hrms.time.regularization.applied`; check attendance's relay and approval's "
                "consumer. Until it lands, approval honestly reports the request as decided but "
                "not yet applied."
            ),
        )
        assert settled["application_failure_reason"] is None, settled

    # -------------------------------------------------------------- 14-15. the denied paths

    def test_14_a_colleague_who_is_not_a_party_cannot_read_the_request(
        self, gateway: httpx.Client, wave0: dict[str, Any]
    ) -> None:
        """The bystander holds exactly the roles everyone else in this cast holds.

        They are simply not the requester and appear on no step, so the request is not theirs to
        see — and the refusal is **404, not 403**, so that request ids cannot be probed for
        existence (ADR-0006 §11). A 403 would answer "yes, that id is real" to anyone who asked.
        """
        bystander = signed_in(gateway, wave0, "bystander")

        response = bystander.get(f"/api/v1/approvals/requests/{wave0['request_id']}")
        assert response.status_code == 404, (
            f"an uninvolved colleague read the request ({response.status_code}): "
            f"{response.text[:300]}"
        )
        # The control: they can reach the surface perfectly well, so the 404 above is about this
        # row and not about the caller being unable to use the endpoint at all.
        assert bystander.get("/api/v1/approvals/requests").status_code == 200
        assert inbox_item(bystander, wave0["request_id"]) is None

    def test_15_another_tenant_gets_404_and_not_403(
        self, gateway: httpx.Client, wave0: dict[str, Any], tenants: dict[str, Any], reconcile: Any
    ) -> None:
        """Cross-tenant, and deliberately arranged so a 403 would not save us.

        Beta's owner is reconciled first, so they genuinely hold `core.approval.request.read:ORG`
        — organization-wide read, the widest grant the catalogue allows. The only thing standing
        between them and this request is the tenant binding. A platform that leaked would answer
        200; one that refused for the wrong reason would answer 403; fail-closed is 404, the same
        answer an id that never existed gets.
        """
        beta = tenants["beta"]
        reconcile("core", beta["org_id"])

        outsider = Session(gateway, beta["owner"]["email"], tenants["password"], beta["org_id"])
        readable = outsider.get("/api/v1/approvals/requests")
        assert readable.status_code == 200, (
            "beta's owner cannot read approval requests at all, so the 404 below would prove "
            f"nothing: {readable.status_code} {readable.text[:300]}"
        )
        assert readable.json()["total"] == 0, (
            f"beta can see gamma's requests in a list: {readable.json()}"
        )

        response = outsider.get(f"/api/v1/approvals/requests/{wave0['request_id']}")
        assert response.status_code == 404, (
            f"beta read gamma's approval request by id ({response.status_code}): "
            f"{response.text[:300]}. That is a cross-tenant leak."
        )

        # And the same id, from the same tenant, still resolves — so the 404 is about the
        # binding and not about the request having vanished.
        assert (
            signed_in(gateway, wave0, "owner")
            .get(f"/api/v1/approvals/requests/{wave0['request_id']}")
            .status_code
            == 200
        )


class TestApprovalIsAPlatformSurface:
    """Independent of the journey: what every tenant gets, bought or not."""

    def test_approval_needs_no_entitlement(
        self, gateway: httpx.Client, tenants: dict[str, Any]
    ) -> None:
        """Approval carries no `product` key in the route table (ADR-0006 §1, §14): it is a
        platform surface, the same shape as `/api/v1/members`, and must work for a customer who
        has bought nothing.

        Alpha has never activated HRMS — the CRM journey depends on that — so a 402 here would
        mean approval had been mis-filed as a product. It answers 403 instead, because alpha has
        not been reconciled either: refused on authorization, which is a different and correct
        refusal.
        """
        alpha = tenants["alpha"]
        outsider = Session(gateway, alpha["owner"]["email"], tenants["password"], alpha["org_id"])
        response = outsider.get("/api/v1/approvals/requests")
        assert response.status_code != 402, (
            "approval asked for payment. It is a platform surface with no entitlement gate; a "
            "402 means it acquired a `product` key in routes.json."
        )
        assert response.status_code == 403, (
            f"expected an authorization refusal for an unreconciled tenant, got "
            f"{response.status_code} {response.text[:300]}"
        )

    def test_the_approval_inbox_needs_a_token(self, gateway: httpx.Client) -> None:
        assert gateway.get("/api/v1/approvals/inbox").status_code == 401

    def test_a_malformed_request_type_is_refused_at_the_boundary(
        self, gateway: httpx.Client, wave0: dict[str, Any]
    ) -> None:
        """`request_type` has a shape, and a path segment that does not match it is the client's
        own mistake named as one — a 422 at the boundary rather than the database CHECK's
        generic 409."""
        response = signed_in(gateway, wave0, "owner").put(
            "/api/v1/approvals/policies/Not-A-Request-Type",
            json={"sla_minutes": 60, "enabled": True},
        )
        assert response.status_code == 422, f"{response.status_code} {response.text[:300]}"

    def test_an_sla_below_the_floor_is_a_field_level_refusal(
        self, gateway: httpx.Client, wave0: dict[str, Any]
    ) -> None:
        """The floor is a database CHECK, not merely an API rule — `ApprovalPolicy`'s own comment
        is explicit that no path may write an SLA short enough to manufacture overdue steps
        faster than the sweep drains them. It is also the reason `test_12` cannot drive an
        escalation to completion inside a test run."""
        response = signed_in(gateway, wave0, "owner").put(
            f"/api/v1/approvals/policies/{REQUEST_TYPE}",
            json={"sla_minutes": 1, "enabled": True},
        )
        assert response.status_code == 422, f"{response.status_code} {response.text[:300]}"
        assert any(e.get("field") == "sla_minutes" for e in response.json().get("errors", [])), (
            "refused, but without naming the field — the settings screen has nowhere to attach "
            f"the message: {response.text[:300]}"
        )


def _unused_uuid() -> str:
    """A syntactically valid id that has never existed. Used to keep the probing assertions
    honest: a 404 for an unknown id and a 404 for another tenant's id must be the same answer."""
    return str(uuid.uuid4())


class TestAnUnknownRequestIdIsIndistinguishable:
    """The other half of "ids cannot be probed": a real id the caller may not see and an id that
    never existed must produce the same response, or the difference is the oracle."""

    def test_an_unknown_id_and_a_forbidden_id_answer_alike(
        self, gateway: httpx.Client, wave0: dict[str, Any]
    ) -> None:
        bystander = signed_in(gateway, wave0, "bystander")
        unknown = bystander.get(f"/api/v1/approvals/requests/{_unused_uuid()}")
        forbidden = bystander.get(f"/api/v1/approvals/requests/{wave0['request_id']}")
        assert unknown.status_code == forbidden.status_code == 404, (
            f"unknown id answered {unknown.status_code}, a real one the caller may not see "
            f"answered {forbidden.status_code} — the difference tells a prober which ids exist"
        )
