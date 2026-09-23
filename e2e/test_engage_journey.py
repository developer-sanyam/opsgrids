"""Engage's sending identity, proven by a real token, a real request and a real mutation.

Engage is deployed to production, and until this file the only evidence anyone had that it
works was that its endpoints return 401 without a token. That proves the routes exist and
require authentication. It proves nothing about whether an authenticated owner can connect a
provider, add a domain, or be refused a sender on a domain nobody has proven — and it proves
nothing about whether another organization can reach in and read or edit what was just created.
This file is that proof, as a permanent journey rather than a one-off script.

    1  no token is refused                       401 — the fact this file exists to say, not proof
    2  the owner buys Engage                     402 before, a real activation event after
    3  an authenticated read of nothing           200 with an empty page, not 401, not a stack trace
    4  a connection is created                    2xx, and the secret sent is never once echoed back
    5  a domain is added                          real DNS records, a real DKIM public key
    6  the domain is verified against real DNS    honestly still pending — nothing here is faked
    7  a sender is refused on that unverified domain   422, naming the reason
    8  a second organization cannot reach the first's row by id   the IDOR/BOLA check, over HTTP
    9  a same-org member with read but not write is split exactly there    403 write, 200 read
   10  the unsubscribe link needs no token at all             a real signed link, 200 not 401

Steps run in file order and share state through `engage_journey`, a dict scoped to this file
alone — `journey` in `test_journey.py` is a *different* fixture instance with the same session
scope, and writing into it here would race whichever file pytest happens to collect first.

Nothing here is stubbed except the one thing step 6 explicitly refuses to stub: real DNS. The
connection points at the MailHog `make infra` already runs (`tools/dev-bootstrap/compose.yml`),
so no mail leaves the machine; the domain step generates a real DKIM keypair and asks a real
resolver about a name nobody has published records for, and the honest answer — still pending —
is the assertion, not a workaround for one.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest
from test_journey import _claims, auth_header, login

pytestmark = pytest.mark.e2e

PRODUCT = "engage"

#: MailHog, already running for the whole suite (`make infra`). Pointed at directly rather than
#: through a DNS name, so the connection step proves a real SMTP host round-trips through the
#: schema without needing a mail server that resolves anywhere.
#: The connection API only accepts a public submission host (its dial guard resolves it and
#: refuses private addresses, so MailHog on localhost can never be stored) on a standard port.
#: Creating a connection resolves the host and stores it; it never opens an SMTP session.
SMTP_SUBMISSION_HOST = "smtp.gmail.com"
SMTP_SUBMISSION_PORT = 587

#: The five DNS record kinds `services.create_domain` must generate for a fresh sending domain
#: (`app/domain.py::dns_records_for`). Asserted as a set so a record silently dropped — or one
#: silently added — both fail loudly.
DNS_RECORD_KINDS = {"spf", "dkim", "dmarc", "return_path", "tracking"}


@pytest.fixture(scope="session")
def engage_journey() -> dict[str, Any]:
    """State carried between the ordered steps — tokens, ids, the secret nobody sees again."""
    return {}


def _wait_for_registration(probe: Any, org_id: str, *, timeout: float = 45.0) -> None:
    """Block until product-service's relay has published the activation event and
    account-service's consumer has installed the manifest — the same two-hop wait
    `test_journey.py`'s `test_04`/`test_05` perform for CRM, reused rather than reinvented.

    This has to be a DB-level wait, not an HTTP one, and the reason is a trap this file fell
    into on its first draft: the `ent` claim goes true the instant product-service commits the
    activation row, which is *before* the event has even reached the outbox. A token pulled the
    moment `ent` looks right is minted with the organization's **pre-grant** permission version,
    and the gateway's permission resolver caches grants **keyed by that version**
    (`platform_core.gateway.PermissionResolver.resolve`, `authz:{org}:{membership}:{version}`,
    TTL 60s). That token is not stale — its own `pv` matches what the database said a moment
    ago — so every one of its requests samples the cache entry poisoned with the grants that
    existed *before* the owner's role landed, for a full minute, regardless of how long this
    test file goes on to wait afterwards. The fix is to never mint the token this journey
    actually uses until the write it depends on has provably committed.
    """
    deadline = time.monotonic() + timeout
    staged: dict[str, Any] = {}
    while time.monotonic() < deadline:
        # Scoped to engage specifically — see `_probe.py` and `test_journey.py::test_04`'s
        # comment: an organization that has already activated a different product would
        # otherwise satisfy this wait on that product's already-published event.
        staged = probe("product", org_id, PRODUCT)
        if staged.get("published", 0) >= 1:
            break
        time.sleep(0.5)
    else:
        raise AssertionError(
            f"product-service never published an activation event for {org_id} within "
            f"{timeout}s: {staged}"
        )
    published_ids = {row["id"] for row in staged["rows"] if row["published"]}

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        processed = probe("account", org_id)
        if published_ids & set(processed.get("event_ids", [])):
            return
        time.sleep(0.5)
    raise AssertionError(
        f"account-service never processed {sorted(published_ids)} within {timeout}s — the "
        "manifest install (and the owner's admin-role grant) never landed. See "
        "test_journey.py's defect (a)."
    )


class TestEngageJourney:
    """Ordered. Each step depends on the one before it, all through the gateway at /api/v1."""

    # ------------------------------------------------------------------ 1. 401 proves only itself

    def test_01_unauthenticated_is_refused(self, client: httpx.Client) -> None:
        """The one fact 401 actually proves: a token is required. Nothing below this line reuses
        this assertion as evidence of anything else — that is the whole reason this file exists.
        """
        response = client.get("/api/v1/engage/connection")
        assert response.status_code == 401, (
            f"expected 401 with no Authorization header, got {response.status_code} "
            f"{response.text[:200]}"
        )

    # ------------------------------------------------------------------ 2. buy it, for real

    def test_02_the_owner_activates_engage(
        self, client: httpx.Client, tenants: dict, engage_journey: dict, probe: Any
    ) -> None:
        """402 before, a real activation event after — the same distinction
        `test_journey.py::test_11` makes for CRM, proven here for the second product this
        service serves.

        Engage is already registered `available` (`e5f6a7b8c9d0_register_engage_product.py`), so
        unlike CRM/HRMS this suite does not need to promote it out of `soon` first — activation
        is the only step standing between an authenticated owner and a working sender.
        """
        alpha = tenants["alpha"]
        pre_token = login(client, alpha["owner"]["email"], tenants["password"], alpha["org_id"])
        assert PRODUCT not in _claims(pre_token).get("ent", []), (
            "engage is entitled before it was ever activated — every step below would pass for "
            "the wrong reason"
        )

        refused = client.get("/api/v1/engage/connection", headers=auth_header(pre_token))
        assert refused.status_code == 402, (
            f"an owner who has not bought engage got {refused.status_code} reading its setup, "
            f"not 402: {refused.text[:200]}. That is a different failure from 401 — the token is "
            "good, the subscription is not — and collapsing the two hides exactly the gap this "
            "file exists to catch."
        )

        activated = client.post(
            "/api/v1/products/activations",
            headers=auth_header(pre_token),
            json={"product_key": PRODUCT},
        )
        assert activated.status_code == 201, (
            f"activation failed: {activated.status_code} {activated.text[:400]}"
        )
        assert activated.json()["product_key"] == PRODUCT
        assert activated.json()["status"] == "active"

        # Wait for the write this journey actually depends on — the manifest installed and
        # ENGAGE_ADMIN granted to the owner — before minting the token every later step uses.
        # See `_wait_for_registration` for why polling sign-in instead is not just slower but
        # wrong.
        _wait_for_registration(probe, alpha["org_id"])
        token = login(client, alpha["owner"]["email"], tenants["password"], alpha["org_id"])
        assert PRODUCT in _claims(token).get("ent", []), (
            "the re-issued token does not carry engage even after the registration installed"
        )
        engage_journey["owner_token"] = token
        engage_journey["org_id"] = alpha["org_id"]

    # ------------------------------------------------------------------ 3. authenticated, empty

    def test_03_authenticated_read_of_empty_connection(
        self, client: httpx.Client, engage_journey: dict
    ) -> None:
        """A real response for an organization with no connection — not 401, and (reading
        `app/api/engage.py::read_connection`) not 404 either: the route models "at most one
        connection" as a page of at most one, so an organization that has connected nothing gets
        `200` with an empty page rather than a null the caller has to special-case.
        """
        response = client.get(
            "/api/v1/engage/connection", headers=auth_header(engage_journey["owner_token"])
        )
        assert response.status_code == 200, (
            f"expected 200 for an authenticated, entitled read of nothing, got "
            f"{response.status_code} {response.text[:300]}"
        )
        body = response.json()
        assert body["items"] == [], f"a fresh organization already has a connection: {body}"
        assert body["total"] == 0

    # -------------------------------------------------------------- 4. create it, hide the secret

    def test_04_creating_the_connection_never_returns_the_secret(
        self, client: httpx.Client, engage_journey: dict
    ) -> None:
        """The single most important assertion in this file. `ConnectionOut` is written field by
        field precisely so this holds (`app/api/engage.py::_connection_out`); this proves it holds
        over the wire, through the gateway, not just in the schema.
        """
        secret = f"app-password-{uuid.uuid4().hex}"
        username = f"alerts-{uuid.uuid4().hex[:8]}@example.com"
        response = client.post(
            "/api/v1/engage/connection",
            headers=auth_header(engage_journey["owner_token"]),
            json={
                "kind": "smtp",
                "username": username,
                "secret": secret,
                "host": SMTP_SUBMISSION_HOST,
                "port": SMTP_SUBMISSION_PORT,
            },
        )
        assert response.status_code == 201, (
            f"connecting the provider failed: {response.status_code} {response.text[:400]}"
        )
        assert secret not in response.text, (
            "the secret sent to connect the provider came back in the response body — the "
            "credential that can send mail as this organization's own domain just leaked over "
            "the wire"
        )
        body = response.json()
        assert body["kind"] == "smtp"
        assert body["username"] == username
        assert body["host"] == SMTP_SUBMISSION_HOST
        assert body["port"] == SMTP_SUBMISSION_PORT
        assert body["status"] == "connected"
        assert "secret" not in body and "secret_enc" not in body

        # And it stays hidden on the read that follows, not just on the write that created it —
        # a route that hid the secret only from its own response would still leak it the moment
        # anyone read the connection back.
        reread = client.get(
            "/api/v1/engage/connection", headers=auth_header(engage_journey["owner_token"])
        )
        assert reread.status_code == 200, reread.text[:300]
        assert secret not in reread.text, "the secret reappeared on a read of the connection"
        items = reread.json()["items"]
        assert len(items) == 1, f"expected exactly one connection, found {items}"
        assert items[0]["username"] == username

        engage_journey["secret"] = secret

    # ------------------------------------------------------------------ 5. add a domain

    def test_05_creating_a_domain_returns_real_dns_records(
        self, client: httpx.Client, engage_journey: dict
    ) -> None:
        """Five records, and a DKIM value that is a real public key rather than the empty string
        `domain.dns_records_for` produces on its own — `services.create_domain` mints the keypair
        and fills it in, and this is the proof that the fill-in actually happens over HTTP.
        """
        domain_name = f"e2e-engage-{uuid.uuid4().hex[:10]}.example.com"
        response = client.post(
            "/api/v1/engage/domains",
            headers=auth_header(engage_journey["owner_token"]),
            json={"domain": domain_name},
        )
        assert response.status_code == 201, (
            f"adding a sending domain failed: {response.status_code} {response.text[:400]}"
        )
        body = response.json()
        assert body["domain"] == domain_name
        assert body["status"] == "pending", (
            "a domain with no DNS published yet reports verified — nothing has been proven"
        )

        records = {record["kind"]: record for record in body["records"]}
        assert set(records) == DNS_RECORD_KINDS, (
            f"expected records for {sorted(DNS_RECORD_KINDS)}, got {sorted(records)}"
        )

        dkim = records["dkim"]["value"]
        assert dkim.startswith("v=DKIM1; k=rsa; p="), (
            f"the DKIM record does not look like a DKIM TXT value: {dkim!r}"
        )
        public_key = dkim.removeprefix("v=DKIM1; k=rsa; p=")
        assert len(public_key) > 100, (
            f"the DKIM record's public key is empty or implausibly short ({len(public_key)} "
            f"chars) — the keypair `create_domain` is supposed to mint was never filled in: "
            f"{dkim!r}"
        )

        engage_journey["domain_id"] = body["id"]
        engage_journey["domain_name"] = domain_name

    # ------------------------------------------------------------------ 6. verify against real DNS

    def test_06_verifying_against_real_dns_honestly_stays_pending(
        self, client: httpx.Client, engage_journey: dict
    ) -> None:
        """`example.com` is IANA-reserved for exactly this: a name that resolves for nothing this
        organization made up beneath it. No test double stands in for DNS here — `dns_check.py`
        exposes no resolver override over HTTP by design (`Resolver` is injected only in-process,
        for the unit tests that *are* allowed to fake the network) — so the only honest assertion
        available through the gateway is the one this makes: DNS was actually asked, and it
        actually said no.
        """
        response = client.post(
            f"/api/v1/engage/domains/{engage_journey['domain_id']}/verify",
            headers=auth_header(engage_journey["owner_token"]),
            json={},
        )
        assert response.status_code == 200, (
            f"re-checking DNS failed outright: {response.status_code} {response.text[:400]}"
        )
        body = response.json()
        assert body["status"] == "pending", (
            f"a domain with nothing published at its registrar came back {body['status']!r} — "
            "either DNS answered something nobody published, or the fold from records to status "
            "is wrong"
        )
        for record in body["records"]:
            assert record["verified"] is False, (
                f"the {record['kind']} record reports verified for a name nothing was ever "
                f"published under: {record}"
            )
        assert body["last_checked_at"] is not None, "verify ran but never stamped when"

    # ---------------------------------------------------- 7. no sender on an unverified domain

    def test_07_a_sender_is_refused_on_an_unverified_domain(
        self, client: httpx.Client, engage_journey: dict
    ) -> None:
        """The rule `services.create_sender` states plainly: a verified domain is a licence to
        send as a name, and there is no licence yet.
        """
        response = client.post(
            "/api/v1/engage/senders",
            headers=auth_header(engage_journey["owner_token"]),
            json={
                "domain_id": engage_journey["domain_id"],
                "name": "Alerts",
                "address": f"alerts@{engage_journey['domain_name']}",
            },
        )
        assert response.status_code == 422, (
            f"a sender was accepted on an unverified domain: {response.status_code} "
            f"{response.text[:300]}"
        )
        assert "verified" in response.json().get("detail", "").lower(), (
            f"refused, but not for the reason expected: {response.text[:300]}"
        )

    # -------------------------------------------------------------- 8. another tenant, over HTTP

    def test_08_another_organizations_direct_navigation_fails_closed(
        self, client: httpx.Client, tenants: dict, engage_journey: dict, probe: Any
    ) -> None:
        """The IDOR/BOLA check. Beta is activated onto Engage too — deliberately, and not merely
        left unentitled — so that what refuses beta below is proven to be **tenant scoping on the
        row**, not the cheaper and less interesting fact that beta never bought the product. An
        unentitled tenant hitting 402 at the gateway before it even reaches a selector would prove
        nothing about `selectors.get_domain`'s `organization_id` filter; an entitled tenant hitting
        404 on alpha's own domain id proves that filter is doing its job.
        """
        beta = tenants["beta"]
        pre_token = login(client, beta["owner"]["email"], tenants["password"], beta["org_id"])
        activated = client.post(
            "/api/v1/products/activations",
            headers=auth_header(pre_token),
            json={"product_key": PRODUCT},
        )
        assert activated.status_code == 201, (
            f"beta could not activate engage: {activated.status_code} {activated.text[:300]}"
        )
        _wait_for_registration(probe, beta["org_id"])
        beta_token = login(client, beta["owner"]["email"], tenants["password"], beta["org_id"])
        assert PRODUCT in _claims(beta_token).get("ent", []), (
            "beta's re-issued token does not carry engage even after its registration installed"
        )

        # Invisible: beta's own lists carry none of alpha's rows, despite both organizations now
        # holding the same product.
        connection = client.get("/api/v1/engage/connection", headers=auth_header(beta_token))
        assert connection.status_code == 200, connection.text[:300]
        assert connection.json()["items"] == [], (
            f"beta can see alpha's connection: {connection.json()}"
        )

        domains = client.get("/api/v1/engage/domains", headers=auth_header(beta_token))
        assert domains.status_code == 200, domains.text[:300]
        domain_ids = {d["id"] for d in domains.json()["items"]}
        assert engage_journey["domain_id"] not in domain_ids, (
            "alpha's domain appears in beta's domain list"
        )

        senders = client.get("/api/v1/engage/senders", headers=auth_header(beta_token))
        assert senders.status_code == 200, senders.text[:300]
        assert senders.json()["items"] == [], f"beta's sender list is not empty: {senders.json()}"

        # Refused by id: beta, with a real engage write grant of its own, reaches directly for
        # alpha's domain by its real id and is refused — not merely omitted from a list.
        cross_verify = client.post(
            f"/api/v1/engage/domains/{engage_journey['domain_id']}/verify",
            headers=auth_header(beta_token),
            json={},
        )
        assert cross_verify.status_code == 404, (
            f"beta reached alpha's domain by id ({cross_verify.status_code}): "
            f"{cross_verify.text[:300]}. That is a cross-tenant leak through "
            "selectors.get_domain."
        )

        # No sender id exists to run the equivalent check against: step 7 established that this
        # harness cannot honestly produce a *verified* domain (that would mean either faking DNS,
        # which step 6 explicitly refuses to do, or reaching the real internet, which this suite
        # never does), so no sender was ever created for alpha to attempt to reach across the
        # tenant boundary. The list check above is the isolation evidence available for senders
        # in this run; the id-based BOLA check above (domains) is what stands in for it.

    # ------------------------------------------------------------ 9. same org, split permission

    def test_09_a_viewer_reads_but_cannot_write(
        self, client: httpx.Client, tenants: dict, engage_journey: dict, seed_password: Any
    ) -> None:
        """A second alpha member, invited and accepted entirely over HTTP exactly as
        `test_journey.py::TestAnInvitationIsAcceptedOverHttp` does it, holding `ENGAGE_VIEWER`
        rather than the admin template the owner was granted on activation. The split between
        `engage.setup.*.read` and `engage.setup.*.write` is the whole reason those are two
        permissions and not one (`app/catalogue.py`); this is the split proven from outside the
        process, on a caller that actually holds one and not the other.
        """
        alpha = tenants["alpha"]
        invitee_email = f"engage-viewer-{uuid.uuid4().hex[:8]}@example.com"
        created = client.post(
            "/api/v1/invitations",
            headers=auth_header(engage_journey["owner_token"]),
            json={"email": invitee_email, "role_codes": ["ENGAGE_VIEWER"], "product_codes": []},
        )
        assert created.status_code == 201, (
            f"inviting the engage viewer failed: {created.status_code} {created.text[:400]}"
        )

        accepted = client.post(
            "/api/v1/invitations/accept",
            json={"token": created.json()["token"], "full_name": "Vera Viewer"},
        )
        assert accepted.status_code in (200, 201), accepted.text[:400]
        member = accepted.json()
        assert "ENGAGE_VIEWER" in member["roles"], (
            f"the accepted member does not hold ENGAGE_VIEWER: {member['roles']}"
        )
        assert "ENGAGE_ADMIN" not in member["roles"], (
            "the viewer invitation somehow also carried the admin template — the read/write "
            "split this test exists to prove would be meaningless"
        )

        viewer_password = "viewer-Sufficiently-Long-Passw0rd!"  # pragma: allowlist secret
        seed_password(invitee_email, viewer_password)
        viewer_token = login(client, invitee_email, viewer_password, alpha["org_id"])

        read = client.get("/api/v1/engage/connection", headers=auth_header(viewer_token))
        assert read.status_code == 200, (
            f"a holder of engage.setup.connection.read was refused a read: {read.status_code} "
            f"{read.text[:300]}"
        )

        write = client.post(
            "/api/v1/engage/domains",
            headers=auth_header(viewer_token),
            json={"domain": f"viewer-write-attempt-{uuid.uuid4().hex[:8]}.example.com"},
        )
        assert write.status_code == 403, (
            f"a caller with no engage.setup.domain.write grant was allowed to add a domain: "
            f"{write.status_code} {write.text[:300]}"
        )

    # ------------------------------------------------------------------ 10. the route with no token

    def test_10_the_unsubscribe_link_is_reachable_without_a_token(
        self, client: httpx.Client, engage_journey: dict, mint_unsubscribe_token: Any
    ) -> None:
        """The bug this step exists to catch: `GatewayPolicy.public_paths` is an *exact*-match
        list (`full_path in policy.public_paths`), which cannot express a path with a variable
        segment — so the one Engage surface with no principal, the per-recipient unsubscribe
        link, got the same 401 as every permissioned route until `public_path_prefixes`
        existed to name it. A real, validly-signed link has to reach notification-service and
        get an actual answer from it — not a 401 the gateway raised before ever asking.
        """
        token = mint_unsubscribe_token(
            engage_journey["org_id"], f"edge-smoke-{uuid.uuid4().hex[:8]}@example.com"
        )

        page = client.get(f"/api/v1/engage/unsubscribe/{token}")
        assert page.status_code == 200, (
            f"GET the unsubscribe link answered {page.status_code}, not 200 — if it is 401 "
            f"the gateway refused it before notification-service ever saw it: "
            f"{page.text[:300]}"
        )

        one_click = client.post(
            f"/api/v1/engage/unsubscribe/{token}",
            content=b"List-Unsubscribe=One-Click",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert one_click.status_code == 200, (
            f"the RFC 8058 one-click POST answered {one_click.status_code}, not 200: "
            f"{one_click.text[:300]}"
        )
        assert one_click.json() == {"unsubscribed": True}
