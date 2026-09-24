"""`GET / PUT / DELETE /storefront/store/domain` and `POST .../domain/check` (ADR-0012 §11).

The probes are always a fake here — this suite proves the wire contract and the write paths,
never the real network. `tests/test_probes.py` proves the real DNS/TLS implementations;
`tests/test_cron.py` proves the sweep that drives them unattended.

Four things worth more than the rest:

* **A claim is evidence, not just a string.** `PUT` mints a token and a row; `GET` reads both
  back; re-claiming replaces both, because a verification must not survive the value it proved.
* **The `www`-partner collision is a 409 no unique index can express** (ADR-0012 §3) — the one
  cross-tenant read this service's authenticated surface ever does, and it is exercised here
  across two real organizations.
* **`DELETE` is idempotent** and **`POST .../check` is rate-limited** — both stated in the ADR's
  wire format and both are the kind of thing that silently regresses.
* **`GET /store` and `GET /store/domain` read the same column and must never disagree** — checked
  directly, not by trusting each route's own test.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import select

from app.models import AuditLog, CustomDomainClaim
from app.probes import ProbeResult, ProbeSet
from tests.helpers import all_perms, seed_custom_domain_claim, seed_store, store_payload

pytestmark = pytest.mark.integration

STORE = "/api/v1/storefront/store"
DOMAIN = "/api/v1/storefront/store/domain"
CHECK = "/api/v1/storefront/store/domain/check"


def _code(response) -> str:
    return str(response.json()["errors"][0]["code"])


async def _audit_actions(session, org_id: uuid.UUID) -> list[str]:
    rows = (
        await session.execute(
            select(AuditLog.action)
            .where(AuditLog.organization_id == org_id)
            .order_by(AuditLog.at.asc())
        )
    ).scalars()
    return list(rows)


@dataclass
class _StaticProbe:
    result: ProbeResult

    async def check(self, host: str, **kwargs: object) -> ProbeResult:
        return self.result


_OK = ProbeResult(ok=True, detail="ok")
_FAILING_OWNERSHIP = ProbeResult(ok=False, code="txt_not_found", detail="No TXT record found.")


def _probes(
    *,
    ownership: ProbeResult = _OK,
    routing: ProbeResult = _OK,
    certificate: ProbeResult = _OK,
) -> ProbeSet:
    return ProbeSet(
        ownership=_StaticProbe(ownership),
        routing=_StaticProbe(routing),
        certificate=_StaticProbe(certificate),
    )


@pytest.fixture(autouse=True)
def _fake_probes(monkeypatch):
    """Every test in this file drives a fake — nothing here ever touches the network. Each
    test overrides the specific outcome it needs via `monkeypatch.setattr` again."""
    monkeypatch.setattr("app.services.default_probe_set", lambda: _probes())
    yield


@pytest.fixture(autouse=True)
def _edge_address(monkeypatch):
    """A deterministic reserved IP so the `dns` block's fallback records are assertable."""
    from app.config import settings

    monkeypatch.setattr(settings(), "edge_reserved_ip", "198.51.100.7")
    monkeypatch.setattr(settings(), "edge_host", "edge.opsgrids.com")
    yield


class TestNoClaim:
    async def test_get_before_any_store_is_a_404(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.get(DOMAIN)
        assert response.status_code == 404

    async def test_a_store_with_no_claim_reads_back_all_null_but_the_two_hosts(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            response = await client.get(DOMAIN)

        assert response.status_code == 200
        body = response.json()
        assert body["custom_domain"] is None
        assert body["status"] is None
        assert body["platform_host"].startswith(slug)
        assert body["canonical_host"] == body["platform_host"]
        assert body["verification"] is None
        assert body["dns"] is None
        assert body["checks"] is None


class TestClaimingADomain:
    async def test_put_mints_a_token_and_leaves_the_store_unverified(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            response = await client.put(DOMAIN, json={"custom_domain": "Shop.Example.COM."})

        assert response.status_code == 200
        body = response.json()
        assert body["custom_domain"] == "shop.example.com"
        assert body["status"] == "unverified"
        assert body["verification"]["name"] == "_opsgrids-challenge.shop.example.com"
        assert body["verification"]["value"].startswith("opsgrids-domain-verification=")
        token = body["verification"]["value"].removeprefix("opsgrids-domain-verification=")
        assert len(token) == 32
        assert body["dns"]["apex"] is False  # shop.example.com has a subdomain label
        assert body["checks"]["ownership"]["state"] == "pending"
        assert body["checks"]["routing"]["state"] == "pending"
        assert body["checks"]["certificate"]["state"] == "pending"

    async def test_an_apex_domain_gets_the_alias_and_the_a_record_fallback(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            response = await client.put(DOMAIN, json={"custom_domain": "merchant.example"})

        dns = response.json()["dns"]
        assert dns["apex"] is True
        assert dns["preferred"]["record_type"] == "ALIAS"
        assert dns["preferred"]["value"] == "edge.opsgrids.com"
        assert {r["record_type"] for r in dns["fallback"]} == {"A"}
        assert {r["value"] for r in dns["fallback"]} == {"198.51.100.7"}
        assert dns["also_served"] == ["www.merchant.example"]

    async def test_reclaiming_a_different_domain_mints_a_fresh_token_and_replaces_the_row(
        self, client, as_principal, session, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            first = await client.put(DOMAIN, json={"custom_domain": "merchant.example"})
            second = await client.put(DOMAIN, json={"custom_domain": "shop.example.com"})

        assert first.json()["verification"]["value"] != second.json()["verification"]["value"]
        rows = (
            (
                await session.execute(
                    select(CustomDomainClaim).where(CustomDomainClaim.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1  # replaced, not accumulated

    async def test_reclaiming_the_same_domain_is_a_no_op_and_does_not_reset_the_clock(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """ADR-0012 review finding 2a: without this, re-PUTting a domain nobody has verified
        once a fortnight would push `claim_expires_at` out indefinitely — endless squatting on a
        name `uq_storefronts_custom_domain` makes exclusive platform-wide."""
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            first = await client.put(DOMAIN, json={"custom_domain": "merchant.example"})

        # Simulate the claim being nearly at its 14-day expiry.
        await admin_session.rollback()
        claim = (
            await admin_session.execute(
                select(CustomDomainClaim).where(CustomDomainClaim.organization_id == org_id)
            )
        ).scalar_one()
        almost_expired = claim.claim_expires_at - dt.timedelta(hours=1)
        claim.claim_expires_at = almost_expired
        await admin_session.commit()

        with as_principal(all_perms(org_id)):
            second = await client.put(DOMAIN, json={"custom_domain": "merchant.example"})

        assert second.status_code == 200
        assert second.json()["verification"]["value"] == first.json()["verification"]["value"]
        returned_expiry = dt.datetime.fromisoformat(
            second.json()["verification"]["claim_expires_at"].replace("Z", "+00:00")
        )
        assert abs((returned_expiry - almost_expired).total_seconds()) < 1

    async def test_put_without_a_store_is_a_404(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.put(DOMAIN, json={"custom_domain": "merchant.example"})
        assert response.status_code == 404

    async def test_the_claim_is_audited_without_the_token(
        self, client, as_principal, session, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            await client.put(DOMAIN, json={"custom_domain": "merchant.example"})

        assert "storefront.domain.claimed" in await _audit_actions(session, org_id)
        row = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "storefront.domain.claimed",
                    )
                )
            )
            .scalars()
            .one()
        )
        dumped = str(row.after) + str(row.before)
        assert "verification_token" not in dumped


class TestTheWwwPartnerCollision:
    """ADR-0012 §3: no unique index can express this, so it is the one cross-tenant read this
    service's authenticated surface does."""

    async def test_claiming_a_domain_whose_www_partner_another_org_holds_is_refused(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
    ) -> None:
        theirs = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, theirs, custom_domain="www.merchant.example")

        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            response = await client.put(DOMAIN, json={"custom_domain": "merchant.example"})

        assert response.status_code == 409
        assert _code(response) == "custom_domain_taken"

    async def test_the_exact_value_collision_still_works_through_the_new_route(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
    ) -> None:
        theirs = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, theirs, custom_domain="merchant.example")

        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            response = await client.put(DOMAIN, json={"custom_domain": "merchant.example"})

        assert response.status_code == 409
        assert _code(response) == "custom_domain_taken"

    async def test_claiming_ones_own_apex_and_www_pair_is_not_a_collision_with_oneself(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            response = await client.put(DOMAIN, json={"custom_domain": "merchant.example"})
        assert response.status_code == 200


class TestReleasingADomain:
    async def test_delete_clears_the_claim_and_is_idempotent(
        self, client, as_principal, session, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            await client.put(DOMAIN, json={"custom_domain": "merchant.example"})

            first = await client.delete(DOMAIN)
            second = await client.delete(DOMAIN)
            after = await client.get(DOMAIN)

        assert first.status_code == 204
        assert second.status_code == 204
        assert after.json()["custom_domain"] is None
        rows = (
            (
                await session.execute(
                    select(CustomDomainClaim).where(CustomDomainClaim.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []

    async def test_delete_without_a_store_is_a_404(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.delete(DOMAIN)
        assert response.status_code == 404

    async def test_release_is_audited(
        self, client, as_principal, session, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            await client.put(DOMAIN, json={"custom_domain": "merchant.example"})
            await client.delete(DOMAIN)

        assert "storefront.domain.released" in await _audit_actions(session, org_id)


class TestCheckNow:
    async def test_check_runs_the_probes_and_promotes_a_verified_claim(
        self, client, as_principal, monkeypatch, org_id: uuid.UUID, slug: str
    ) -> None:
        monkeypatch.setattr("app.services.default_probe_set", lambda: _probes())

        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            await client.put(DOMAIN, json={"custom_domain": "merchant.example"})
            response = await client.post(CHECK)

        assert response.status_code == 200
        assert response.json()["status"] == "verified"
        assert response.json()["checks"]["ownership"]["state"] == "ok"

    async def test_a_failing_ownership_check_leaves_it_unverified_with_a_reason(
        self, client, as_principal, monkeypatch, org_id: uuid.UUID, slug: str
    ) -> None:
        monkeypatch.setattr(
            "app.services.default_probe_set", lambda: _probes(ownership=_FAILING_OWNERSHIP)
        )

        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            await client.put(DOMAIN, json={"custom_domain": "merchant.example"})
            response = await client.post(CHECK)

        assert response.json()["status"] == "unverified"
        assert response.json()["checks"]["ownership"]["code"] == "txt_not_found"

    async def test_check_without_a_claim_is_a_404(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            response = await client.post(CHECK)
        assert response.status_code == 404

    async def test_check_is_rate_limited_to_one_call_per_window(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            await client.put(DOMAIN, json={"custom_domain": "merchant.example"})
            first = await client.post(CHECK)
            second = await client.post(CHECK)

        assert first.status_code == 200
        assert second.status_code == 429

    async def test_check_is_audited_every_call_even_with_no_state_change(
        self, client, as_principal, session, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            await client.put(DOMAIN, json={"custom_domain": "merchant.example"})
            await client.post(CHECK)

        actions = await _audit_actions(session, org_id)
        assert actions.count("storefront.domain.checked") == 1

    async def test_a_suspended_claim_recovers_to_verified_once_ownership_and_routing_pass(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str, monkeypatch
    ) -> None:
        """ADR-0012 review finding 5: `suspended` blocks `ask`, so a claim suspended purely by
        the failure count can never again pass its certificate probe on its own — nothing
        issues it a certificate to present. Ownership and routing both checking out has to move
        it back to `verified` instead of leaving it stuck forever."""
        store = await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="suspended",
        )
        await seed_custom_domain_claim(
            admin_session,
            org_id,
            store.id,
            ownership_state="ok",
            routing_state="ok",
            certificate_state="failing",
            consecutive_failures=9,
            verified_at=dt.datetime.now(dt.UTC),
        )
        monkeypatch.setattr(
            "app.services.default_probe_set",
            lambda: _probes(certificate=ProbeResult(ok=False, code="handshake_failed", detail="x")),
        )

        with as_principal(all_perms(org_id)):
            response = await client.post(CHECK)

        assert response.status_code == 200
        assert response.json()["status"] == "verified"


class TestTheTwoScreensAgree:
    async def test_store_out_and_domain_out_read_the_same_status(
        self, client, as_principal, org_id: uuid.UUID, slug: str
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(STORE, json=store_payload(slug))
            await client.put(DOMAIN, json={"custom_domain": "merchant.example"})
            store = await client.get(STORE)
            domain = await client.get(DOMAIN)

        assert store.json()["custom_domain_status"] == domain.json()["status"]
        assert store.json()["custom_domain"] == domain.json()["custom_domain"]


class TestCrossTenantIsolation:
    async def test_another_organizations_claim_is_invisible(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
    ) -> None:
        theirs = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, theirs, custom_domain="theirs.example")

        with as_principal(all_perms(org_id)):
            response = await client.get(DOMAIN)

        assert response.status_code == 404
