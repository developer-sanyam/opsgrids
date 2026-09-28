"""Meta's deauthorize and data-deletion callbacks, driven over real HTTP.

Facebook Login's own trust scheme (`signed_request`), not the WhatsApp Business Platform's
`X-Hub-Signature-256` — `tests/test_meta_signed_request.py` covers that module in isolation. This
covers what only shows up going through the actual routes: attribution against
`provider_accounts.meta_user_id`, the advisory-only effect on connection health (never
`token_state` — see `docs/whatsapp/webhooks.md`), the audit trail, one-shot processing (the
identity link cleared, a replay left powerless), the confirmation-code round trip, idempotent
retries, a forced-overlap race on minting a confirmation, and fan-out across two organizations
sharing one Facebook user id.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, ProviderAccount
from app.w1_models import ConnectionHealth, MetaDataDeletionRequest, SetupRun
from platform_core.config import get_settings
from tests.w1_helpers import clean_w1_tables  # noqa: F401 -- autouse truncation of w1's own tables

pytestmark = pytest.mark.integration

# Named with the repository's `ci-only-` prefix, which the secret scanner's exclude pattern
# recognises — see tests/test_webhooks.py for the same convention.
SECRET = "ci-only-app-secret"
USER_ID = "218471"
OTHER_USER_ID = "999999"


@pytest.fixture(autouse=True)
def _app_secret(monkeypatch: pytest.MonkeyPatch):
    """Settings are cached by class; the cache has to be cleared on both sides of the test, the
    same convention `test_inbound_webhook.py`'s `_webhook_secret` fixture uses."""
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _signed_request(payload: dict, *, secret: str = SECRET) -> str:
    """A genuine `signed_request` value, built the way Meta's own reference implementation does."""
    body = {"algorithm": "HMAC-SHA256", **payload}
    encoded = _b64url(json.dumps(body).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{_b64url(signature)}.{encoded}"


def _form(value: str) -> bytes:
    return f"signed_request={value}".encode("ascii")


def _headers() -> dict[str, str]:
    return {"Content-Type": "application/x-www-form-urlencoded"}


async def _connected_account(
    session,
    org_id: uuid.UUID,
    *,
    meta_user_id: str | None = USER_ID,
    phone_number_id: str | None = None,
    token_state: str = "valid",  # noqa: S107 - a state word, not a credential
) -> None:
    """A connected WhatsApp account naming `meta_user_id`, with a known-good token state so a
    flip to `invalid` is a real, observable change rather than the health row's own default.

    `phone_number_id` defaults to one derived from `org_id`: it is globally unique
    (`provider_accounts.phone_number_id`), and every other test module in this suite reaches for
    the same handful of literal numbers (`"1001"`, `"1002"`) — deriving one here means this file
    can never collide with them, or with itself across two organizations in the same test, without
    every call site having to remember to pick a fresh one.
    """
    phone_number_id = phone_number_id or f"meta-privacy-{org_id.hex[:16]}"
    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id="waba-1",
                phone_number_id=phone_number_id,
                display_number="+919876543210",
                status="connected",
                meta_user_id=meta_user_id,
            )
        )
        session.add(ConnectionHealth(organization_id=org_id, token_state=token_state))


async def _health(session, org_id: uuid.UUID) -> ConnectionHealth:
    stmt = select(ConnectionHealth).where(ConnectionHealth.organization_id == org_id)
    return (await session.execute(stmt)).scalar_one()


async def _audit_count(session, org_id: uuid.UUID, action: str) -> int:
    stmt = (
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.organization_id == org_id, AuditLog.action == action)
    )
    return (await session.execute(stmt)).scalar_one()


# --------------------------------------------------------------------- authenticity


async def test_an_unsigned_deauthorize_is_refused(client) -> None:
    response = await client.post(
        "/webhooks/whatsapp/deauthorize", content=b"signed_request=garbage", headers=_headers()
    )
    assert response.status_code == 401


async def test_an_unsigned_data_deletion_is_refused(client) -> None:
    response = await client.post(
        "/webhooks/whatsapp/data-deletion", content=b"signed_request=garbage", headers=_headers()
    )
    assert response.status_code == 401


async def test_a_data_deletion_signed_by_the_wrong_secret_is_refused(client) -> None:
    value = _signed_request({"user_id": USER_ID}, secret="ci-only-a-different-secret")
    response = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()
    )
    assert response.status_code == 401


# --------------------------------------------------------------------- deauthorize


async def test_deauthorize_with_a_match_is_advisory_only_and_audits_it(
    client, session, org_id
) -> None:
    """Records the advisory `identity_revoked_at` signal and clears the identity link -- never
    `token_state`, which stays exactly as it was: a deauthorize callback must not be able to stop
    sends on its own (see `docs/whatsapp/webhooks.md`)."""
    await _connected_account(session, org_id)
    value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})
    response = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(value), headers=_headers()
    )
    assert response.status_code == 200
    assert response.content == b""

    async with session.begin():
        health = await _health(session, org_id)
        assert health.token_state == "valid"
        assert health.identity_revoked_at is not None
        account = (await session.execute(select(ProviderAccount))).scalars().one()
        assert account.meta_user_id is None
        assert await _audit_count(session, org_id, "engage.whatsapp.meta_deauthorized") == 1


async def test_deauthorize_with_no_match_is_still_200_and_writes_nothing(
    client, session, org_id
) -> None:
    await _connected_account(session, org_id, meta_user_id=None)
    value = _signed_request({"user_id": USER_ID})
    response = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(value), headers=_headers()
    )
    assert response.status_code == 200
    assert response.content == b""

    async with session.begin():
        health = await _health(session, org_id)
        assert health.token_state == "valid"
        assert health.identity_revoked_at is None
        assert await _audit_count(session, org_id, "engage.whatsapp.meta_deauthorized") == 0


async def test_a_retried_deauthorize_callback_does_not_audit_twice(client, session, org_id) -> None:
    """Same `(user_id, issued_at)`: a genuine Meta redelivery, not a fresh event. Idempotent by
    absence now, not a `processed_events` lookup: the first call already cleared `meta_user_id`,
    so the retry's own account lookup matches nothing."""
    await _connected_account(session, org_id)
    value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})

    first = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(value), headers=_headers()
    )
    second = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(value), headers=_headers()
    )
    assert first.status_code == 200
    assert second.status_code == 200

    async with session.begin():
        assert await _audit_count(session, org_id, "engage.whatsapp.meta_deauthorized") == 1


async def test_a_fresh_blob_for_an_already_processed_identity_has_no_further_effect(
    client, session, org_id
) -> None:
    """The lever the security review flagged: a *different*, validly-signed payload for the same
    Facebook user id (a fresh `issued_at`, exactly what a freshly minted SDK login blob would carry
    -- distinct from a byte-identical replay, which the test above already covers) must find
    nothing left to act on once the identity link has been cleared once. One-shot per connection,
    not per `issued_at`, is what makes that true."""
    await _connected_account(session, org_id)
    first_value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})
    second_value = _signed_request({"user_id": USER_ID, "issued_at": 1750350173})

    first = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(first_value), headers=_headers()
    )
    second = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(second_value), headers=_headers()
    )
    assert first.status_code == second.status_code == 200
    assert first.content == second.content == b""

    async with session.begin():
        assert await _audit_count(session, org_id, "engage.whatsapp.meta_deauthorized") == 1


async def test_deauthorize_fans_out_to_every_organization_the_user_id_matches(
    client, session, admin_session, org_id
) -> None:
    """One Facebook login can be the one that connected more than one organization's WhatsApp."""
    other_org = uuid.uuid4()
    await _connected_account(session, org_id)
    await _connected_account(admin_session, other_org)

    value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})
    response = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(value), headers=_headers()
    )
    assert response.status_code == 200

    async with session.begin():
        health = await _health(session, org_id)
        assert health.token_state == "valid"
        assert health.identity_revoked_at is not None
    async with admin_session.begin():
        other_health = await _health(admin_session, other_org)
        assert other_health.token_state == "valid"
        assert other_health.identity_revoked_at is not None
        count = await _audit_count(admin_session, other_org, "engage.whatsapp.meta_deauthorized")
        assert count == 1


async def test_deauthorize_does_not_act_when_the_account_no_longer_carries_that_identity(
    session, org_id
) -> None:
    """The equality re-check `apply_meta_deauthorize` makes under `FOR UPDATE`: a race where the
    connection's `meta_user_id` has already moved on by the time the effect is applied (a
    concurrent reconnect, or a second matched account row that turned out to be stale) must be a
    no-op, not an action taken against the wrong identity."""
    from app import w1_services

    async with session.begin():
        account = ProviderAccount(
            organization_id=org_id,
            channel="whatsapp",
            waba_id="waba-1",
            phone_number_id="meta-privacy-mismatch",
            display_number="+919876543210",
            status="connected",
            meta_user_id=OTHER_USER_ID,
        )
        session.add(account)
        await session.flush()
        account_id = account.id

    async with session.begin():
        applied = await w1_services.apply_meta_deauthorize(
            session, org_id, account_id, user_id=USER_ID
        )
    assert applied is False

    async with session.begin():
        refreshed = await session.get(ProviderAccount, account_id)
        assert refreshed is not None
        assert refreshed.meta_user_id == OTHER_USER_ID
        assert await _audit_count(session, org_id, "engage.whatsapp.meta_deauthorized") == 0


# --------------------------------------------------------------------- data deletion


async def test_data_deletion_with_a_match_clears_the_identity_link(client, session, org_id) -> None:
    await _connected_account(session, org_id)
    value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})
    response = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"url", "confirmation_code"}
    assert len(body["confirmation_code"]) == 32
    expected_suffix = f"/webhooks/whatsapp/data-deletion-status?code={body['confirmation_code']}"
    assert body["url"].endswith(expected_suffix)

    async with session.begin():
        account = (await session.execute(select(ProviderAccount))).scalars().one()
        assert account.meta_user_id is None
        # Business records untouched: the account itself is not disconnected.
        assert account.status == "connected"
        assert account.phone_number_id is not None
        health = await _health(session, org_id)
        # Advisory only -- token_state is never flipped by this callback. See
        # `docs/whatsapp/webhooks.md`.
        assert health.token_state == "valid"
        assert health.identity_revoked_at is not None
        assert await _audit_count(session, org_id, "engage.whatsapp.meta_data_deletion") == 1


async def test_data_deletion_also_clears_a_matching_setup_run_hint_in_the_same_transaction(
    client, session, org_id
) -> None:
    """A run still `running` must not copy a since-deleted identity onto the connection after this
    commits (`w1_setup._step_number` mirrors `meta_user_id_hint` onto the account)."""
    await _connected_account(session, org_id)
    async with session.begin():
        session.add(
            SetupRun(
                organization_id=org_id,
                status="running",
                steps=[],
                code_hash="ci-only-hash",
                meta_user_id_hint=USER_ID,
            )
        )

    value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})
    response = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()
    )
    assert response.status_code == 200

    async with session.begin():
        run = (await session.execute(select(SetupRun))).scalars().one()
        assert run.meta_user_id_hint is None


async def test_data_deletion_still_clears_the_hint_when_deauthorize_already_nulled_the_account(
    client, session, org_id
) -> None:
    """The ordering that orphaned the hint before this fix: deauthorize runs first and nulls
    `ProviderAccount.meta_user_id`, so by the time data-deletion arrives,
    `accounts_for_meta_user_id` finds no account here at all -- `apply_meta_data_deletion` is
    never reached for this organization, and its own hint-clearing never runs. The route's direct
    `w1_selectors.setup_run_organizations_for_meta_user_id_hint` lookup is what still finds and
    clears it. Still returns a genuine confirmation, the same as any other data-deletion request."""
    await _connected_account(session, org_id)
    async with session.begin():
        session.add(
            SetupRun(
                organization_id=org_id,
                status="running",
                steps=[],
                code_hash="ci-only-hash",
                meta_user_id_hint=USER_ID,
            )
        )

    deauth_value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})
    deauth_response = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(deauth_value), headers=_headers()
    )
    assert deauth_response.status_code == 200

    async with session.begin():
        account = (await session.execute(select(ProviderAccount))).scalars().one()
        assert account.meta_user_id is None  # the ordering this test depends on
        run = (await session.execute(select(SetupRun))).scalars().one()
        assert run.meta_user_id_hint == USER_ID  # not touched by deauthorize -- still orphaned

    deletion_value = _signed_request({"user_id": USER_ID, "issued_at": 1750263774})
    deletion_response = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(deletion_value), headers=_headers()
    )
    assert deletion_response.status_code == 200
    body = deletion_response.json()
    assert len(body["confirmation_code"]) == 32

    # `session`'s sessionmaker is `expire_on_commit=False` (see conftest.py), and `run` above is
    # already in this session's identity map from the read right after deauthorize -- without
    # this, the next `select(SetupRun)` would hand back that same cached object with its stale
    # in-memory value, rather than the row the app's own, separate connection just updated.
    session.expire_all()
    async with session.begin():
        run = (await session.execute(select(SetupRun))).scalars().one()
        assert run.meta_user_id_hint is None


async def test_data_deletion_with_no_match_still_returns_a_confirmation(
    client, session, org_id
) -> None:
    await _connected_account(session, org_id, meta_user_id=None)
    value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})
    response = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"url", "confirmation_code"}

    async with session.begin():
        assert await _audit_count(session, org_id, "engage.whatsapp.meta_data_deletion") == 0
        rows = (await session.execute(select(MetaDataDeletionRequest))).scalars().all()
    assert [r.confirmation_code for r in rows] == [body["confirmation_code"]]


async def test_a_retried_data_deletion_reuses_the_same_confirmation_code(
    client, session, org_id
) -> None:
    await _connected_account(session, org_id)
    value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})

    first = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()
    )
    second = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["confirmation_code"] == second.json()["confirmation_code"]

    async with session.begin():
        rows = (await session.execute(select(MetaDataDeletionRequest))).scalars().all()
        assert len(rows) == 1
        # The second call's own fan-out found nothing left to clear (the account's
        # `meta_user_id` was already nulled by the first) — idempotent by absence, not by a
        # second audit row for the same effect.
        assert await _audit_count(session, org_id, "engage.whatsapp.meta_data_deletion") == 1


async def test_a_later_distinct_request_from_the_same_person_gets_its_own_code(
    client, session, org_id
) -> None:
    """Different `issued_at` — Meta stamps a fresh one per "Send Request" click — is a different
    request, not a retry, and must not collide with an earlier one's confirmation code."""
    await _connected_account(session, org_id)
    first_value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})
    second_value = _signed_request({"user_id": USER_ID, "issued_at": 1750350173})

    first = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(first_value), headers=_headers()
    )
    second = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(second_value), headers=_headers()
    )
    assert first.json()["confirmation_code"] != second.json()["confirmation_code"]


async def test_data_deletion_fans_out_to_every_organization_the_user_id_matches(
    client, session, admin_session, org_id
) -> None:
    other_org = uuid.uuid4()
    await _connected_account(session, org_id)
    await _connected_account(admin_session, other_org)

    value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})
    response = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()
    )
    assert response.status_code == 200

    async with session.begin():
        mine = (await session.execute(select(ProviderAccount))).scalars().one()
        assert mine.meta_user_id is None
    async with admin_session.begin():
        stmt = select(ProviderAccount).where(ProviderAccount.organization_id == other_org)
        theirs = (await admin_session.execute(stmt)).scalars().one()
        assert theirs.meta_user_id is None
        count = await _audit_count(admin_session, other_org, "engage.whatsapp.meta_data_deletion")
        assert count == 1


# --------------------------------------------------------------------- status


async def test_the_status_check_round_trips_a_genuine_confirmation(client, session, org_id) -> None:
    """A small HTML page, not JSON -- Meta describes this URL as one a person can go to and check,
    and a real App Review reviewer may click it rather than curl it."""
    await _connected_account(session, org_id)
    value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})
    minted = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()
    )
    code = minted.json()["confirmation_code"]

    response = await client.get(f"/webhooks/whatsapp/data-deletion-status?code={code}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "processed" in response.text
    assert code in response.text


async def test_the_status_check_reveals_nothing_for_an_unknown_code(client) -> None:
    response = await client.get("/webhooks/whatsapp/data-deletion-status?code=" + "0" * 32)
    assert response.status_code == 404


async def test_the_status_check_refuses_a_malformed_code_the_same_way_as_an_unknown_one(
    client,
) -> None:
    response = await client.get("/webhooks/whatsapp/data-deletion-status?code=not-a-code")
    assert response.status_code == 404


# --------------------------------------------------------------------- never reveal recognition


async def test_deauthorize_answers_identically_whether_or_not_the_id_is_recognised(
    client, session, org_id
) -> None:
    await _connected_account(session, org_id, meta_user_id=USER_ID)
    matched_value = _signed_request({"user_id": USER_ID, "issued_at": 1})
    unmatched_value = _signed_request({"user_id": OTHER_USER_ID, "issued_at": 2})

    matched = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(matched_value), headers=_headers()
    )
    unmatched = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(unmatched_value), headers=_headers()
    )
    assert matched.status_code == unmatched.status_code == 200
    assert matched.content == unmatched.content == b""


# --------------------------------------------------------------------- the SDK login-response shape


def _sdk_login_response(payload: dict, *, secret: str = SECRET) -> str:
    body = {"algorithm": "HMAC-SHA256", "code": "ci-only-one-time-code", **payload}
    encoded = _b64url(json.dumps(body).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{_b64url(signature)}.{encoded}"


async def test_deauthorize_refuses_a_payload_shaped_like_the_sdk_login_response(
    client, session, org_id
) -> None:
    """The lever the security review flagged: `FB.login`'s own response is signed by the same app
    secret, in the same shape, plus `code` -- a field the real callback never carries. A validly
    signed payload carrying it must still be refused, not attributed to a connection."""
    await _connected_account(session, org_id)
    value = _sdk_login_response({"user_id": USER_ID, "issued_at": 1750263773})
    response = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(value), headers=_headers()
    )
    assert response.status_code == 401

    async with session.begin():
        health = await _health(session, org_id)
        assert health.identity_revoked_at is None
        assert await _audit_count(session, org_id, "engage.whatsapp.meta_deauthorized") == 0


async def test_data_deletion_refuses_a_payload_shaped_like_the_sdk_login_response(
    client, session, org_id
) -> None:
    await _connected_account(session, org_id)
    value = _sdk_login_response({"user_id": USER_ID, "issued_at": 1750263773})
    response = await client.post(
        "/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()
    )
    assert response.status_code == 401

    async with session.begin():
        count = (
            await session.execute(select(func.count()).select_from(MetaDataDeletionRequest))
        ).scalar_one()
        assert count == 0


async def test_an_oauth_token_field_is_refused_the_same_way(client) -> None:
    """The older SDK's own equivalent of `code` — same shape, same reason to refuse."""
    body = {"algorithm": "HMAC-SHA256", "oauth_token": "ci-only-oauth-token", "user_id": USER_ID}
    encoded = _b64url(json.dumps(body).encode("utf-8"))
    signature = hmac.new(SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    signed = f"{_b64url(signature)}.{encoded}"
    response = await client.post(
        "/webhooks/whatsapp/deauthorize", content=_form(signed), headers=_headers()
    )
    assert response.status_code == 401


# --------------------------------------------------------------------- forced-overlap concurrency


async def test_a_forced_overlap_data_deletion_race_gives_the_loser_the_winners_code(
    client, session, org_id
) -> None:
    """M3: `mint_or_reuse_confirmation`'s race-loser path used to crash (500) because `session.add`
    sat outside the savepoint, so a losing `IntegrityError` left the outer transaction aborted by
    the time the winner's own row was read back. Two real, concurrently-scheduled HTTP requests --
    each opening its own connection through `session_scope`, not two sequential calls in the same
    coroutine -- exercise the actual database race rather than assume it."""
    await _connected_account(session, org_id)
    value = _signed_request({"user_id": USER_ID, "issued_at": 1750263773})

    responses = await asyncio.gather(
        client.post("/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()),
        client.post("/webhooks/whatsapp/data-deletion", content=_form(value), headers=_headers()),
    )
    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
    codes = {r.json()["confirmation_code"] for r in responses}
    assert len(codes) == 1

    async with session.begin():
        count = (
            await session.execute(select(func.count()).select_from(MetaDataDeletionRequest))
        ).scalar_one()
        assert count == 1
