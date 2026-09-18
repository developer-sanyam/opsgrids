"""One organization's whole life, in order, across six real services.

This is the journey the platform exists to serve, and until now nothing tested it end to end.
Every component had passing tests; the *contracts between them* had none, which is exactly
where the defects were. The steps run in file order and share state, because that is what a
journey is — step 5 cannot mean anything if step 3 did not happen.

    1  the owner signs in                     gateway → auth → account → product
    2  an administrator may not activate      the owner/administrator split, over HTTP
    3  the owner activates CRM                product-service's real activation path
    4  the relay publishes the event          ← defect (a): the silent stall
    5  account-service consumes it            ← the hop a stub always fakes
    6  the manifest is installed              permissions and role templates land
    7  every admin role is granted            ← defect (c): a product spanning two services
    8  an entitled endpoint returns data      200, with rows this suite seeded
    9  the second service answers too         the other admin role, proven by data
    10 an unentitled product is refused       402, not 403
    11 another tenant's row is not readable   row-level security, through the whole stack
    12 a list returns only this tenant's rows
    13 every service resolves grants          ← defect (b): the version-skew guard

Steps 4, 5, 7 and 13 are the three defects that reached `main` in a single day, each invisible
to a unit test and each obvious on the first real request.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.e2e

#: The product this journey activates: the only one in the catalogue spanning two services,
#: and therefore the only one shipping two admin role templates.
PRODUCT = "crm"

#: What activating it must install — one admin role per contributing service.
ADMIN_ROLES = {"CRM_BOOK_ADMIN", "CRM_PIPELINE_ADMIN"}

#: A product that exists, ships a manifest, and is deliberately *not* activated.
UNENTITLED_PATH = "/api/v1/employees"


def _claims(token: str) -> dict[str, Any]:
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


def seed_id(org_id: str, key: str) -> str:
    """The same deterministic id `_seed_crm.py` wrote the row under."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opsgrids-e2e-crm:{org_id}:{key}"))


class TestTheJourney:
    """Ordered. Each step depends on the one before it."""

    # ------------------------------------------------------------------ 1. sign in

    def test_01_the_owner_signs_in_through_the_gateway(
        self, client: httpx.Client, tenants: dict, journey: dict
    ) -> None:
        """Four services have to agree before this returns a token.

        The gateway routes a public path; auth verifies the password; auth asks
        account-service *who this is* over real HTTP with a minted service token; and it asks
        product-service what the organization has, which becomes the `ent` claim the gateway
        gates on from here on. auth's own tests fake the last two — this is the only place
        the real ones run.
        """
        alpha = tenants["alpha"]
        token = login(client, alpha["owner"]["email"], tenants["password"], alpha["org_id"])
        claims = _claims(token)

        assert claims["act_org"] == alpha["org_id"], (
            "the token names a different organization than the one signed in to"
        )
        assert "core" in claims.get("ent", []), (
            f"the entitlement claim is {claims.get('ent')!r}. Empty means auth could not reach "
            "product-service — sign-in degrades rather than failing, so nothing else notices."
        )
        assert PRODUCT not in claims.get("ent", []), (
            f"{PRODUCT} is entitled before it was ever activated — the journey's later steps "
            "would all pass for the wrong reason"
        )
        journey["owner_token"] = token

    # ------------------------------------------------------------------ 2-3. activate

    def test_02_an_administrator_cannot_activate_a_product(
        self, client: httpx.Client, tenants: dict
    ) -> None:
        """Activation spends money, and this role is defined as the one that does not.

        The owner/administrator split holding through a real request rather than only in a
        unit test — and the control that makes the next step meaningful.
        """
        admin = tenants["alpha"]["admin"]
        token = login(client, admin["email"], tenants["password"], tenants["alpha"]["org_id"])
        response = client.post(
            "/api/v1/products/activations",
            headers=auth_header(token),
            json={"product_key": PRODUCT},
        )
        assert response.status_code == 403, (
            f"an administrator activated a product ({response.status_code} "
            f"{response.text[:200]}). Activation is owner-only."
        )

    def test_03_the_owner_activates_the_multi_service_product(
        self, client: httpx.Client, tenants: dict, journey: dict
    ) -> None:
        """product-service's real activation path, through the gateway."""
        response = client.post(
            "/api/v1/products/activations",
            headers=auth_header(journey["owner_token"]),
            json={"product_key": PRODUCT},
        )
        assert response.status_code == 201, (
            f"activation failed: {response.status_code} {response.text[:400]}. A 422 usually "
            f"means the registry still has {PRODUCT!r} marked 'soon' — see _seed_product.py."
        )
        assert response.json()["product_key"] == PRODUCT
        assert response.json()["status"] == "active"

    # ------------------------------------------------- 4-5. the event actually crosses

    def test_04_the_relay_publishes_the_activation_event(
        self, tenants: dict, journey: dict, probe: Any
    ) -> None:
        """**Defect (a).** The relay ran as the RLS-bound app role, so with no tenant bound
        its drain query matched zero rows: it published nothing, logged nothing, and looped
        while the outbox filled. Activation reported success and no permission was installed.

        Nothing above this line would have failed. This is the line that fails.
        """
        org_id = tenants["alpha"]["org_id"]
        deadline = time.monotonic() + 45.0
        state: dict[str, Any] = {}
        while time.monotonic() < deadline:
            state = probe("product", org_id)
            if state.get("published", 0) >= 1:
                break
            time.sleep(0.5)

        assert state.get("staged", 0) >= 1, (
            f"product-service staged no {'core.product.activated'!r} row for this organization "
            f"— activation returned 201 without writing to the outbox. Probe said: {state}"
        )
        assert state.get("published", 0) >= 1, (
            "the activation event was staged but never published: the outbox relay is not "
            f"draining. Probe said: {state}. This is the silent stall — check the relay is "
            "running as opsgrids_outbox (BYPASSRLS), not the service's app role."
        )
        journey["published_event_ids"] = [r["id"] for r in state["rows"] if r["published"]]

    def test_05_account_service_consumes_what_product_service_wrote(
        self, tenants: dict, journey: dict, probe: Any
    ) -> None:
        """An event written by one service, consumed by another. No stub in between."""
        deadline = time.monotonic() + 45.0
        state: dict[str, Any] = {}
        while time.monotonic() < deadline:
            state = probe("account", tenants["alpha"]["org_id"])
            if state.get("processed", 0) >= 1:
                break
            time.sleep(0.5)

        assert state.get("processed", 0) >= 1, (
            "account-service never recorded processing the activation event. The relay "
            f"published it (ids {journey['published_event_ids']}), so the broker hop or the "
            f"handler is the problem. Probe said: {state}"
        )
        assert set(journey["published_event_ids"]) & set(state["event_ids"]), (
            "account-service processed an activation event, but not the one this run "
            f"published — published {journey['published_event_ids']}, processed "
            f"{state['event_ids']}. A consumer elsewhere is eating this suite's events."
        )

    # ------------------------------------------------------- 6-7. the manifest lands

    def test_06_the_grant_change_makes_the_old_token_stale(
        self, client: httpx.Client, journey: dict
    ) -> None:
        """Installing the manifest granted roles, which bumps the organization's permission
        version — so the token minted before it is refused with 401 `token-stale` rather than
        quietly continuing to work. A revoked role that stayed usable until token expiry is
        the behaviour ADR-0000 committed to preventing.
        """
        response = client.get("/api/v1/organization", headers=auth_header(journey["owner_token"]))
        assert response.status_code == 401, (
            f"the pre-activation token still works ({response.status_code}). Granting a role "
            "must bump the permission version, or a revocation waits for token expiry."
        )
        assert response.json().get("type", "").endswith("token-stale"), (
            f"refused, but not as stale: {response.text[:300]}"
        )

    def test_07_every_admin_role_the_product_ships_is_granted_to_the_owner(
        self, client: httpx.Client, tenants: dict, journey: dict
    ) -> None:
        """**Defect (c).** A product is not a service. CRM spans customer-service and
        deal-service, and each ships the admin role for its own slice of the catalogue. The
        manifest carried a single `admin_role_code`, so parsing kept only the last one and
        activation granted only that — leaving CRM's owners able to read contacts and not
        deals.

        Asserted on the membership rather than in the database, because "granted" means the
        owner's next request carries it.
        """
        alpha = tenants["alpha"]
        token = login(client, alpha["owner"]["email"], tenants["password"], alpha["org_id"])
        journey["owner_token"] = token

        assert PRODUCT in _claims(token)["ent"], (
            "the re-issued token does not carry the product just activated — auth is not "
            "seeing product-service's activations"
        )

        response = client.get(
            f"/api/v1/members/{alpha['owner']['membership_id']}", headers=auth_header(token)
        )
        assert response.status_code == 200, response.text[:400]
        held = set(response.json()["roles"])

        assert held >= ADMIN_ROLES, (
            f"the owner holds {sorted(held)}; the manifest ships {sorted(ADMIN_ROLES)}. Missing "
            f"{sorted(ADMIN_ROLES - held)} — activation granted only some of the product's "
            "admin roles, so its owners can reach one of its services and not the other."
        )

    def test_08_the_products_permissions_joined_the_catalogue(
        self, client: httpx.Client, journey: dict
    ) -> None:
        """Roles are only half of it: the permission codes have to exist to be granted."""
        response = client.get("/api/v1/permissions", headers=auth_header(journey["owner_token"]))
        assert response.status_code == 200, response.text[:400]
        codes = {p["code"] for p in response.json()}
        expected = {"crm.book.company.read", "crm.pipeline.deal.read"}
        assert expected <= codes, (
            f"missing {sorted(expected - codes)} from the catalogue — the manifest's "
            "permissions were not installed on activation"
        )

    # ------------------------------------------------------- 8-9. entitled, with data

    def test_09_an_entitled_endpoint_returns_this_tenants_real_data(
        self, client: httpx.Client, tenants: dict, journey: dict
    ) -> None:
        """The whole platform in one call: the gateway verifies the token, resolves
        permissions from account-service, mints a forwarding token, rewrites identity
        headers and proxies; customer-service binds the transaction to the organization and
        reads rows row-level security is enforcing.
        """
        response = client.get("/api/v1/customers", headers=auth_header(journey["owner_token"]))
        assert response.status_code == 200, (
            f"{response.status_code} {response.text[:400]}. A 403 means grants did not resolve; "
            "a 402 means the entitlement claim did not survive; a 404 means the tenant binding "
            "did not reach the query."
        )
        names = {c["name"] for c in response.json()["items"]}
        assert "Alpha Anvils Ltd" in names, f"seeded company missing from {names}"

    def test_10_the_second_admin_role_reaches_the_second_service(
        self, client: httpx.Client, journey: dict
    ) -> None:
        """The other half of defect (c), proven by data rather than by a role name.

        Same product, same token, a different service. Before the fix this returned 403 while
        the step above returned 200 — the product was half-usable and nothing said why.
        """
        response = client.get("/api/v1/deals", headers=auth_header(journey["owner_token"]))
        assert response.status_code == 200, (
            f"the account book answered 200 and the pipeline answered {response.status_code}: "
            f"{response.text[:300]}. That is a product granting one of its two admin roles."
        )
        names = {d["name"] for d in response.json()["items"]}
        assert "Alpha forge retooling" in names, f"seeded deal missing from {names}"

    # ------------------------------------------------------------ 10. unentitled

    def test_11_an_unentitled_product_is_payment_required(
        self, client: httpx.Client, journey: dict
    ) -> None:
        """402 rather than 403 — the distinction the whole entitlement model rests on.

        The caller's permissions are fine; the subscription is not, so a client can offer an
        upgrade instead of an error it cannot explain. Refused at the gateway before it
        proxies, which is why no service needs to be listening behind this route.
        """
        response = client.get(UNENTITLED_PATH, headers=auth_header(journey["owner_token"]))
        assert response.status_code == 402, (
            f"expected payment-required for an unactivated product, got {response.status_code} "
            f"{response.text[:300]}"
        )

    # ------------------------------------------------------------ 11-12. other tenants

    def test_12_another_tenants_row_is_not_readable_by_id(
        self, client: httpx.Client, tenants: dict, journey: dict
    ) -> None:
        """The row exists, is the same shape, and is one organization away.

        Asked for by primary key, so nothing is filtering it out by accident — only the
        tenant binding and the policy stand between this request and beta's data.
        """
        victim = seed_id(tenants["beta"]["org_id"], "company")
        response = client.get(
            f"/api/v1/customers/{victim}", headers=auth_header(journey["owner_token"])
        )
        assert response.status_code == 404, (
            f"alpha read beta's company by id ({response.status_code}): "
            f"{response.text[:300]}. That is a cross-tenant leak."
        )

    def test_13_a_list_endpoint_returns_only_this_tenants_rows(
        self, client: httpx.Client, journey: dict
    ) -> None:
        """The leak a per-id check misses: a list that forgot its `organization_id` filter."""
        response = client.get("/api/v1/customers", headers=auth_header(journey["owner_token"]))
        assert response.status_code == 200, response.text[:400]
        names = {c["name"] for c in response.json()["items"]}
        assert "Beta Bearings Ltd" not in names, f"beta's company appeared in alpha's list: {names}"
        assert names == {"Alpha Anvils Ltd"}, f"unexpected rows in alpha's account book: {names}"

    # ------------------------------------------------------------ 13. version skew

    def test_14_every_service_resolves_grants_through_the_current_token_scheme(
        self, client: httpx.Client, tenants: dict, journey: dict
    ) -> None:
        """**Defect (b).** The gateway began minting signed, audience-bound service tokens
        while some services still expected the old static shared secret. Every permission
        check behind those services answered 403, on every request, for every caller.

        The guard is deliberately broad: one authenticated request per service in this suite,
        each requiring a permission the caller genuinely holds. A service on the old scheme
        cannot answer 200 here — it never sees a principal it trusts, so it refuses. Reported
        together rather than one assertion per service, so a skew shows as "these three are
        behind" instead of failing on whichever happened to be first.
        """
        headers = auth_header(journey["owner_token"])
        surfaces = {
            "account": f"/api/v1/members/{tenants['alpha']['owner']['membership_id']}",
            "product": "/api/v1/products/activations",
            "customer": "/api/v1/customers",
            "deal": "/api/v1/deals",
        }

        refused = {
            service: (r.status_code, r.text[:160])
            for service, path in surfaces.items()
            if (r := client.get(path, headers=headers)).status_code != 200
        }
        assert not refused, (
            "these services did not resolve the caller's grants from a request the gateway "
            f"signed: {refused}. A 401 or 403 across several of them at once is version skew "
            "— the gateway forwards a signed, audience-bound service token and a service "
            "still verifying the old static secret refuses every principal."
        )

    def test_15_the_gateway_is_the_only_door_to_the_internal_api(
        self, client: httpx.Client, tenants: dict
    ) -> None:
        """It reads across every tenant and carries no per-user authorization.

        Routing it through a public gateway would let any authenticated caller read every
        user and organization on the platform. The operator route table once contained
        exactly that mapping, inert only because of a typo.
        """
        for path in (
            "/internal/v1/users/by-email",
            f"/internal/v1/memberships/{tenants['alpha']['owner']['membership_id']}/grants",
        ):
            response = client.get(path, params={"email": tenants["alpha"]["owner"]["email"]})
            assert response.status_code in (401, 404), (
                f"{path} answered {response.status_code} through the public gateway"
            )


class TestSignInIsNotAnOracle:
    """Independent of the journey: the negative paths, which no ordering affects."""

    def test_a_wrong_password_is_refused(self, client: httpx.Client, tenants: dict) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": tenants["alpha"]["owner"]["email"], "password": "not-the-password"},
        )
        assert response.status_code in (400, 401)

    def test_an_unknown_email_is_refused_identically(
        self, client: httpx.Client, tenants: dict
    ) -> None:
        """Otherwise the login form becomes a way to discover who has an account."""
        wrong_password = client.post(
            "/api/v1/auth/login",
            json={"email": tenants["alpha"]["owner"]["email"], "password": "not-the-password"},
        )
        unknown_user = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody-at-all@example.com", "password": "not-the-password"},
        )
        assert wrong_password.status_code == unknown_user.status_code

    def test_no_token_is_refused(self, client: httpx.Client) -> None:
        assert client.get("/api/v1/organization").status_code == 401

    def test_a_garbage_token_is_refused(self, client: httpx.Client) -> None:
        response = client.get(
            "/api/v1/organization", headers={"Authorization": "Bearer not.a.token"}
        )
        assert response.status_code == 401


class TestAnInvitationIsAcceptedOverHttp:
    """A second person joins entirely over HTTP — the path `main` could not serve.

    This route once depended on the tenant chokepoint, which refuses a caller with no
    principal before the body runs. The invitee — who by definition has no account and no
    token — got a 401 and could never accept. account-service's own suite missed it because
    it drove acceptance around the chokepoint.
    """

    def test_an_invitee_accepts_without_a_token_then_signs_in(
        self, client: httpx.Client, tenants: dict, seed_password: Any
    ) -> None:
        alpha = tenants["alpha"]
        owner_token = login(client, alpha["owner"]["email"], tenants["password"], alpha["org_id"])

        invitee_email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
        created = client.post(
            "/api/v1/invitations",
            headers=auth_header(owner_token),
            json={"email": invitee_email, "role_codes": ["ORG_ADMIN"], "product_codes": []},
        )
        assert created.status_code == 201, created.text[:400]

        # The step that was impossible: no Authorization header at all.
        accepted = client.post(
            "/api/v1/invitations/accept",
            json={"token": created.json()["token"], "full_name": "Ivy Invitee"},
        )
        assert accepted.status_code in (200, 201), (
            f"accept over HTTP failed: {accepted.status_code} {accepted.text[:400]}. A 401 here "
            "is the original defect — the public path routed through the tenant chokepoint."
        )
        member = accepted.json()
        assert member["email"] == invitee_email
        assert "ORG_ADMIN" in member["roles"]

        # Credentials live in auth, not account, and accepting does not set a password.
        invitee_password = "invitee-Sufficiently-Long-Passw0rd!"
        seed_password(member["user_id"], invitee_password)

        token = login(client, invitee_email, invitee_password, alpha["org_id"])
        response = client.get("/api/v1/organization", headers=auth_header(token))
        assert response.status_code == 200, (
            f"the accepted invitee could not reach their organization: {response.status_code} "
            f"{response.text[:400]}"
        )
        assert response.json()["slug"] == alpha["org_slug"]
