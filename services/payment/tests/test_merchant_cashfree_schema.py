"""The merchant-connect migration (`d9e0f1a2b3c4`, ADR-0030) against the real database: a Cashfree
account holds one secret, a live Razorpay account still holds two, only the two providers exist,
and the verification columns are there. The round trip is a gate (`make migration-roundtrip`); the
downgrade's refusal is pinned here by what it counts.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers import rows

pytestmark = pytest.mark.integration


async def _insert(
    admin: AsyncSession,
    *,
    provider: str,
    status: str = "active",
    key: bytes | None = b"sealed-key",
    hook: bytes | None = None,
) -> None:
    async with admin.begin():
        await admin.execute(
            text(
                "INSERT INTO payment.merchant_accounts (id, organization_id, provider, key_id, "
                "mode, key_secret_enc, key_secret_masked, webhook_secret_enc, "
                "webhook_secret_masked, status, deleted_at) VALUES (:id, :org, :p, 'k', 'test', "
                ":key, '', :hook, '', :s, :deleted)"
            ),
            {
                "id": uuid.uuid4(),
                "org": uuid.uuid4(),
                "p": provider,
                "key": key,
                "hook": hook,
                "s": status,
                "deleted": datetime.now(UTC) if status == "removed" else None,
            },
        )


class TestTheChecks:
    async def test_a_cashfree_account_holds_one_secret(self, admin_session: AsyncSession) -> None:
        await _insert(admin_session, provider="cashfree")
        with pytest.raises(IntegrityError, match="cashfree_has_no_webhook_secret"):
            await _insert(admin_session, provider="cashfree", hook=b"sealed-hook")

    async def test_a_live_razorpay_account_still_holds_both(
        self, admin_session: AsyncSession
    ) -> None:
        await _insert(admin_session, provider="razorpay", hook=b"sealed-hook")
        with pytest.raises(IntegrityError, match="razorpay_active_has_webhook_secret"):
            await _insert(admin_session, provider="razorpay")

    async def test_a_live_account_always_holds_its_key_and_a_removed_one_none(
        self, admin_session: AsyncSession
    ) -> None:
        with pytest.raises(IntegrityError, match="key_secret_present_iff_active"):
            await _insert(admin_session, provider="cashfree", key=None)
        await _insert(admin_session, provider="razorpay", status="removed", key=None)
        with pytest.raises(IntegrityError, match="key_secret_present_iff_active"):
            await _insert(admin_session, provider="cashfree", status="removed")

    async def test_only_the_two_providers_exist(self, admin_session: AsyncSession) -> None:
        with pytest.raises(IntegrityError, match="ck_merchant_accounts_provider"):
            await _insert(admin_session, provider="stripe")

    async def test_the_old_two_secret_check_is_gone_and_the_columns_are_there(
        self, admin_session: AsyncSession
    ) -> None:
        checks = {
            r["conname"]
            for r in await rows(
                admin_session,
                "SELECT conname FROM pg_constraint WHERE conrelid = "
                "'payment.merchant_accounts'::regclass AND contype = 'c'",
            )
        }
        assert "ck_merchant_accounts_secrets_present_iff_active" not in checks
        assert {
            "ck_merchant_accounts_provider",
            "ck_merchant_accounts_key_secret_present_iff_active",
            "ck_merchant_accounts_razorpay_active_has_webhook_secret",
            "ck_merchant_accounts_cashfree_has_no_webhook_secret",
        } <= checks
        columns = {
            r["column_name"]: r["is_nullable"]
            for r in await rows(
                admin_session,
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'payment' AND table_name = 'merchant_accounts'",
            )
        }
        assert columns["verified_at"] == "YES" and columns["last_error_code"] == "YES"

    async def test_the_count_the_downgrade_refuses_on_is_what_a_cashfree_row_makes_it(
        self, admin_session: AsyncSession
    ) -> None:
        sql = "SELECT count(*) AS n FROM payment.merchant_accounts WHERE provider = 'cashfree'"
        (before,) = await rows(admin_session, sql)
        await _insert(admin_session, provider="cashfree")
        (after,) = await rows(admin_session, sql)
        assert after["n"] == before["n"] + 1


def test_the_revision_chains_from_the_previous_head() -> None:
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    (path,) = versions.glob("d9e0f1a2b3c4_*.py")
    spec = importlib.util.spec_from_file_location("merchant_cashfree_revision", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "d9e0f1a2b3c4" and module.down_revision == "c7d8e9f0a1b2"
    revisions = {p.name.split("_", 1)[0] for p in versions.glob("*.py")}
    pointed_at = {
        match.group(1)
        for p in versions.glob("*.py")
        for match in [re.search(r"down_revision[^=]*=\s*['\"]([0-9a-f]+)['\"]", p.read_text())]
        if match is not None
    }
    assert revisions - pointed_at == {"d9e0f1a2b3c4"}, "this revision must be the only head"
