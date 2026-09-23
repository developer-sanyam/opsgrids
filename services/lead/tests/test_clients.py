"""The internal-API callers — without a network, and without customer-service or deal-service
actually running. Same shape as notification-service's `test_targets.py`: drive the real
function against a stubbed transport, so the behaviour worth protecting (translating an HTTP
response into a return value or the right exception) is exercised for real.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app import clients
from app.config import Settings
from platform_core.internal_auth import verify_service_token

#: `tests/conftest.py`'s autouse `_stub_owners_active` fixture replaces `clients.invalid_owner_
#: ids` for every test in the suite (every other module's fixtures create leads through
#: `services.create_lead`, which calls it) — captured here, at import time, before that fixture
#: ever runs, so `TestInvalidOwnerIds` below can still exercise the real implementation.
_real_invalid_owner_ids = clients.invalid_owner_ids


def _settings() -> Settings:
    return Settings(
        customer_internal_url="http://customer.invalid",
        deal_internal_url="http://deal.invalid",
    )


async def _with_transport(handler, monkeypatch: pytest.MonkeyPatch, coro_factory):
    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return await coro_factory()


class TestEnsureCompanyAndContact:
    async def test_the_happy_path_returns_both_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        company_id, contact_id = uuid.uuid4(), uuid.uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"company_id": str(company_id), "contact_id": str(contact_id)}
            )

        result = await _with_transport(
            handler,
            monkeypatch,
            lambda: clients.ensure_company_and_contact(
                _settings(),
                uuid.uuid4(),
                company_id=uuid.uuid4(),
                contact_id=uuid.uuid4(),
                company_name="Stellar Robotics",
                owner_id=uuid.uuid4(),
                contact_full_name="Priya Sharma",
                contact_email="priya@stellar.example",
                contact_raw_phone=None,
            ),
        )
        assert result == (company_id, contact_id)

    async def test_no_email_can_still_return_no_contact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        company_id = uuid.uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"company_id": str(company_id), "contact_id": None})

        result = await _with_transport(
            handler,
            monkeypatch,
            lambda: clients.ensure_company_and_contact(
                _settings(),
                uuid.uuid4(),
                company_id=uuid.uuid4(),
                contact_id=uuid.uuid4(),
                company_name="Vertex Freight",
                owner_id=uuid.uuid4(),
                contact_full_name="Arjun Verma",
                contact_email=None,
                contact_raw_phone=None,
            ),
        )
        assert result == (company_id, None)

    async def test_an_unknown_owner_raises_owner_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "not found"})

        with pytest.raises(clients.OwnerUnknown):
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_company_and_contact(
                    _settings(),
                    uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=uuid.uuid4(),
                    company_name="Stellar Robotics",
                    owner_id=uuid.uuid4(),
                    contact_full_name="Priya Sharma",
                    contact_email=None,
                    contact_raw_phone=None,
                ),
            )

    async def test_a_company_deleted_problem_type_raises_company_deleted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Checked by the RFC 7807 `type` field, not by status code alone -- a plain 409
        dedup conflict must not be confused with this specific, differently-worded case."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409,
                json={
                    "type": "https://errors.opsgrids.dev/company-deleted",
                    "title": "That company has been deleted",
                    "status": 409,
                    "detail": "gone",
                },
            )

        with pytest.raises(clients.CompanyDeleted):
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_company_and_contact(
                    _settings(),
                    uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=uuid.uuid4(),
                    company_name="Stellar Robotics",
                    owner_id=uuid.uuid4(),
                    contact_full_name="Priya Sharma",
                    contact_email=None,
                    contact_raw_phone=None,
                ),
            )

    async def test_an_ordinary_409_is_not_confused_with_company_deleted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409,
                json={
                    "type": "https://errors.opsgrids.dev/conflict",
                    "title": "Conflict",
                    "status": 409,
                    "detail": "some other conflict",
                },
            )

        with pytest.raises(clients.ConversionRejected) as excinfo:
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_company_and_contact(
                    _settings(),
                    uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=uuid.uuid4(),
                    company_name="Stellar Robotics",
                    owner_id=uuid.uuid4(),
                    contact_full_name="Priya Sharma",
                    contact_email=None,
                    contact_raw_phone=None,
                ),
            )
        assert not isinstance(excinfo.value, clients.CompanyDeleted)

    async def test_a_malformed_200_is_treated_as_conversion_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 200 that does not parse into `{company_id, contact_id}` is not a success -- and
        must not crash the caller with a raw `KeyError`/`ValueError` either."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        with pytest.raises(clients.ConversionUnavailable):
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_company_and_contact(
                    _settings(),
                    uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=uuid.uuid4(),
                    company_name="Stellar Robotics",
                    owner_id=uuid.uuid4(),
                    contact_full_name="Priya Sharma",
                    contact_email=None,
                    contact_raw_phone=None,
                ),
            )

    async def test_a_5xx_raises_conversion_unavailable_not_owner_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transient outage must be retried, never confused with a permanent refusal."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="upstream is down")

        with pytest.raises(clients.ConversionUnavailable):
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_company_and_contact(
                    _settings(),
                    uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=uuid.uuid4(),
                    company_name="Stellar Robotics",
                    owner_id=uuid.uuid4(),
                    contact_full_name="Priya Sharma",
                    contact_email=None,
                    contact_raw_phone=None,
                ),
            )

    async def test_the_request_carries_a_token_addressed_to_customer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["token"] = request.headers.get("X-Internal-Token")
            seen["path"] = request.url.path
            return httpx.Response(200, json={"company_id": str(uuid.uuid4()), "contact_id": None})

        await _with_transport(
            handler,
            monkeypatch,
            lambda: clients.ensure_company_and_contact(
                _settings(),
                uuid.uuid4(),
                company_id=uuid.uuid4(),
                contact_id=uuid.uuid4(),
                company_name="Stellar Robotics",
                owner_id=uuid.uuid4(),
                contact_full_name="Priya Sharma",
                contact_email=None,
                contact_raw_phone=None,
            ),
        )
        assert seen["token"]
        assert str(seen["path"]).endswith("/leads:convert")

    async def test_a_generic_4xx_raises_conversion_rejected_not_owner_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 404 specifically means "unknown owner" — every other 4xx is a plain, permanent
        refusal, and must not be confused with that one specific, recoverable-by-fixing-the-
        owner case."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": "some validation problem"})

        with pytest.raises(clients.ConversionRejected) as excinfo:
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_company_and_contact(
                    _settings(),
                    uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=uuid.uuid4(),
                    company_name="Stellar Robotics",
                    owner_id=uuid.uuid4(),
                    contact_full_name="Priya Sharma",
                    contact_email=None,
                    contact_raw_phone=None,
                ),
            )
        assert not isinstance(excinfo.value, clients.OwnerUnknown)
        # And never the upstream body, whatever it said.
        assert "some validation problem" not in str(excinfo.value)

    async def test_the_upstream_response_body_never_reaches_the_exception_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret_looking_body = (
            "phone +919876543210 is already used by another contact"  # pragma: allowlist secret
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, text=secret_looking_body)

        with pytest.raises(clients.ConversionRejected) as excinfo:
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_company_and_contact(
                    _settings(),
                    uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=uuid.uuid4(),
                    company_name="Stellar Robotics",
                    owner_id=uuid.uuid4(),
                    contact_full_name="Priya Sharma",
                    contact_email="priya@stellar.example",
                    contact_raw_phone="+919876543210",
                ),
            )
        assert secret_looking_body not in str(excinfo.value)
        assert "+919876543210" not in str(excinfo.value)

    async def test_the_token_binds_the_calling_organization_and_the_acting_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        org_id = uuid.uuid4()
        actor_id = uuid.uuid4()
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["token"] = request.headers.get("X-Internal-Token")
            return httpx.Response(200, json={"company_id": str(uuid.uuid4()), "contact_id": None})

        await _with_transport(
            handler,
            monkeypatch,
            lambda: clients.ensure_company_and_contact(
                _settings(),
                org_id,
                company_id=uuid.uuid4(),
                contact_id=uuid.uuid4(),
                company_name="Stellar Robotics",
                owner_id=uuid.uuid4(),
                contact_full_name="Priya Sharma",
                contact_email=None,
                contact_raw_phone=None,
                actor_id=actor_id,
            ),
        )

        customer_settings = Settings(
            service_name="customer",
            internal_service_token=_settings().internal_service_token,
        )
        claims = verify_service_token(customer_settings, str(seen["token"]))
        assert claims["org"] == str(org_id)
        assert claims["sub"] == str(actor_id)
        assert claims["iss"] == "lead"


class TestEnsureDeal:
    async def test_the_happy_path_returns_the_deal_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deal_id = uuid.uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"deal_id": str(deal_id)})

        result = await _with_transport(
            handler,
            monkeypatch,
            lambda: clients.ensure_deal(
                _settings(),
                uuid.uuid4(),
                deal_id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                contact_id=None,
                owner_id=uuid.uuid4(),
                name="Stellar Robotics — Priya Sharma",
                close_date="2026-10-23",
                source="form",
            ),
        )
        assert result == deal_id

    async def test_a_malformed_200_is_treated_as_conversion_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        with pytest.raises(clients.ConversionUnavailable):
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_deal(
                    _settings(),
                    uuid.uuid4(),
                    deal_id=uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=None,
                    owner_id=uuid.uuid4(),
                    name="x",
                    close_date="2026-10-23",
                    source="form",
                ),
            )

    async def test_a_5xx_raises_conversion_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        with pytest.raises(clients.ConversionUnavailable):
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_deal(
                    _settings(),
                    uuid.uuid4(),
                    deal_id=uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=None,
                    owner_id=uuid.uuid4(),
                    name="x",
                    close_date="2026-10-23",
                    source="form",
                ),
            )


class TestInvalidOwnerIds:
    async def test_an_empty_list_is_never_even_asked_about(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no owners to validate should mean no HTTP call at all")

        result = await _with_transport(
            handler, monkeypatch, lambda: _real_invalid_owner_ids(_settings(), uuid.uuid4(), [])
        )
        assert result == []

    async def test_the_reported_invalid_ids_are_returned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad = uuid.uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"invalid_owner_ids": [str(bad)]})

        result = await _with_transport(
            handler,
            monkeypatch,
            lambda: _real_invalid_owner_ids(_settings(), uuid.uuid4(), [bad, uuid.uuid4()]),
        )
        assert result == [bad]

    async def test_an_unreachable_directory_raises_owners_validate_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        with pytest.raises(clients.OwnersValidateUnavailable):
            await _with_transport(
                handler,
                monkeypatch,
                lambda: _real_invalid_owner_ids(_settings(), uuid.uuid4(), [uuid.uuid4()]),
            )

    async def test_a_non_200_raises_owners_validate_unavailable_not_the_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal stack trace, do not repeat this")

        with pytest.raises(clients.OwnersValidateUnavailable) as excinfo:
            await _with_transport(
                handler,
                monkeypatch,
                lambda: _real_invalid_owner_ids(_settings(), uuid.uuid4(), [uuid.uuid4()]),
            )
        assert "internal stack trace" not in str(excinfo.value)
