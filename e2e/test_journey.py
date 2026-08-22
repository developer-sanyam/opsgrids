"""One person signs in and reads their organization, across three real services.

This is the journey the platform exists to serve, and until now nothing tested it. Every
component had passing tests; the *contracts between them* had none, which is exactly where the
defects were.

What each step proves, and why it is worth a separate assertion:

1. **Sign in** — the gateway routes a public path, auth-service verifies the password, and auth
   resolves the person by calling account-service's internal API over real HTTP with a real
   service token. That last hop is the one that was missing entirely: account served none of
   those endpoints, and nothing noticed because auth's tests used a fake directory.

2. **Read the organization** — the gateway verifies the token it was just handed, resolves the
   caller's permissions from account-service, forwards identity headers, and account-service
   binds the transaction to that organization before reading a row that row-level security is
   now enforcing. Six things have to agree. None of them is stubbed.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.e2e


class TestSignIn:
    def test_a_person_can_sign_in(self, client: httpx.Client, account: dict) -> None:
        """The cross-service contract that was broken and invisible.

        auth-service cannot answer this without account-service answering *it* first.
        """
        response = client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": account["password"]},
        )
        assert response.status_code == 200, (
            f"sign-in failed: {response.status_code} {response.text[:400]}. If this is a 401, "
            "check auth-service can reach account-service's /internal/v1 — a directory it "
            "cannot read looks exactly like a wrong password, by design."
        )

    def test_a_wrong_password_is_refused(self, client: httpx.Client, account: dict) -> None:
        """And refused the same way an unknown email is, so the form cannot enumerate users."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": "not-the-password"},
        )
        assert response.status_code in (400, 401)

    def test_an_unknown_email_is_refused_identically(
        self, client: httpx.Client, account: dict
    ) -> None:
        wrong_password = client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": "not-the-password"},
        )
        unknown_user = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody-at-all@example.com", "password": "not-the-password"},
        )
        assert wrong_password.status_code == unknown_user.status_code, (
            "a wrong password and an unknown email must be indistinguishable, or the login "
            "form becomes a way to discover who has an account"
        )


class TestTheSignedInJourney:
    def _token(self, client: httpx.Client, account: dict) -> str:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": account["password"]},
        )
        assert response.status_code == 200, response.text[:400]
        body = response.json()

        token = body.get("access_token")
        if token:
            return str(token)

        # Belonging to several organizations makes sign-in two steps: the first returns a
        # pre-auth token and the memberships, the second exchanges it for a real one. Handled
        # here rather than assumed away, because assuming one organization is precisely the
        # bug this platform's identity model exists to avoid.
        pre_auth = body.get("pre_auth_token") or body.get("token")
        assert pre_auth, f"no token and no organization choice in {body}"
        chosen = client.post(
            "/api/v1/auth/select-org",
            json={"token": pre_auth, "organization_id": account["org_id"]},
        )
        assert chosen.status_code == 200, chosen.text[:400]
        return str(chosen.json()["access_token"])

    def test_the_token_opens_the_organization(self, client: httpx.Client, account: dict) -> None:
        """The whole platform in one call.

        Gateway verifies the token, resolves permissions from account-service, strips and
        rewrites identity headers, forwards; account-service binds the transaction to the
        caller's organization and reads a row that row-level security is enforcing.
        """
        token = self._token(client, account)
        response = client.get("/api/v1/organization", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200, (
            f"{response.status_code} {response.text[:400]}. A 403 here usually means the "
            "gateway could not resolve permissions from account-service; a 404 means the "
            "tenant binding did not reach the query."
        )
        assert response.json()["slug"] == account["org_slug"]

    def test_no_token_is_refused(self, client: httpx.Client, account: dict) -> None:
        assert client.get("/api/v1/organization").status_code == 401

    def test_a_garbage_token_is_refused(self, client: httpx.Client, account: dict) -> None:
        response = client.get(
            "/api/v1/organization", headers={"Authorization": "Bearer not.a.token"}
        )
        assert response.status_code == 401

    def test_an_unactivated_product_is_payment_required(
        self, client: httpx.Client, account: dict
    ) -> None:
        """402 rather than 403 — the distinction the whole entitlement model rests on.

        The caller's permissions are fine; the subscription is not, so a client can offer an
        upgrade instead of showing an error it cannot explain.
        """
        token = self._token(client, account)
        response = client.get("/api/v1/customers", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 402, (
            f"expected payment-required for an unactivated product, got {response.status_code}"
        )


class TestTheGatewayIsTheOnlyDoor:
    def test_the_internal_api_is_not_reachable_through_the_gateway(
        self, client: httpx.Client, account: dict
    ) -> None:
        """It reads across every tenant and carries no per-user authorization.

        Routing it through a public gateway would let any authenticated caller read every user
        and organization on the platform. The operator route table used to contain exactly that
        mapping, inert only because of a typo.
        """
        for path in ("/internal/v1/users/by-email", "/api/v1/internal/accounts"):
            response = client.get(path, params={"email": account["email"]})
            assert response.status_code in (401, 404), (
                f"{path} answered {response.status_code} through the public gateway"
            )
