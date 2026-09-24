"""The audit trail stores what it is handed, including a row snapshot taken with `as_dict`.

Found by the cross-tenant sweep rather than by design. `update_organization` passed
`org.as_dict()` as its `before` snapshot — exactly what that method's docstring says it is for —
and every call then died inside the JSONB serializer, because a row snapshot holds UUIDs and
timestamps and `json.dumps` accepts neither. The route answered 500 to everyone, and nothing had
ever called it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from platform_core.audit import REDACTED, record_audit


class _Row:
    """Stands in for a service's audit model: keeps whatever it was constructed with."""

    def __init__(self, **fields: Any) -> None:
        self.fields = fields


class _Session:
    def __init__(self) -> None:
        self.added: list[_Row] = []

    def add(self, row: _Row) -> None:
        self.added.append(row)


def _record(before: dict[str, Any]) -> dict[str, Any]:
    session = _Session()
    record_audit(
        cast(AsyncSession, session),
        _Row,
        organization_id=uuid.uuid4(),
        actor_id=None,
        action="organization.updated",
        entity="organization",
        before=before,
    )
    stored: dict[str, Any] = session.added[0].fields["before"]
    return stored


class TestSnapshotsAreStorable:
    def test_a_row_snapshot_with_uuids_timestamps_and_decimals_is_storable_json(self) -> None:
        """What `as_dict` produces must survive the column's serializer, at any depth."""
        row_id = uuid.uuid4()
        when = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
        stored = _record(
            {
                "id": row_id,
                "updated_at": when,
                "started_on": date(2026, 8, 22),
                "address": {"owner": row_id, "rent": Decimal("10.50")},
                "tags": [row_id],
            }
        )

        json.dumps(stored)  # the JSONB column's default serializer; raises on a raw UUID
        assert stored == {
            "id": str(row_id),
            "updated_at": when.isoformat(),
            "started_on": "2026-08-22",
            "address": {"owner": str(row_id), "rent": "10.50"},
            "tags": [str(row_id)],
        }

    def test_redaction_still_applies_to_a_coerced_snapshot(self) -> None:
        """Coercion must not reorder the pipeline so a secret slips through as a string."""
        stored = _record({"password": "hunter2", "nested": {"token": uuid.uuid4()}})
        assert stored["password"] == REDACTED
        assert stored["nested"]["token"] == REDACTED


def _request(*, forwarded: str | None = None, peer: str | None = "10.0.0.1") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": headers,
            "client": (peer, 5000) if peer is not None else None,
            "query_string": b"",
        }
    )


def _stored_ip(request: Request | None) -> str | None:
    session = _Session()
    record_audit(
        cast(AsyncSession, session),
        _Row,
        organization_id=uuid.uuid4(),
        actor_id=None,
        action="thing.done",
        entity="thing",
        request=request,
    )
    ip: str | None = session.added[0].fields["ip"]
    return ip


class TestTheRecordedClientAddress:
    """`audit_logs.ip` is Postgres `INET`. The value comes from a forwarded header — text a client
    typed — so it is recorded only when it parses as an address. Found when the attendance signals
    began reading the same value: `X-Forwarded-For: unknown` reached the INSERT, the database
    refused it, and the request died with a 500 on every audited endpoint in the platform."""

    def test_the_first_forwarded_hop_is_the_client(self) -> None:
        assert _stored_ip(_request(forwarded="203.0.113.9, 10.0.0.1")) == "203.0.113.9"

    def test_with_no_forwarded_header_the_socket_peer_is_recorded(self) -> None:
        assert _stored_ip(_request(peer="198.51.100.4")) == "198.51.100.4"

    def test_an_ipv6_address_is_normalised(self) -> None:
        assert _stored_ip(_request(forwarded="2001:0DB8::0001")) == "2001:db8::1"

    def test_no_request_records_no_address(self) -> None:
        assert _stored_ip(None) is None

    def test_no_peer_and_no_header_records_no_address(self) -> None:
        assert _stored_ip(_request(peer=None)) is None

    def test_a_header_that_is_not_an_address_is_recorded_as_unknown(self) -> None:
        for junk in ("unknown", "203.0.113.9:443", "not-an-ip", "999.1.1.1", "'; DROP TABLE x;--"):
            assert _stored_ip(_request(forwarded=junk)) is None, junk

    def test_an_empty_first_hop_is_recorded_as_unknown(self) -> None:
        assert _stored_ip(_request(forwarded=" , 10.0.0.1")) is None

    def test_an_entirely_empty_header_is_the_same_as_none_at_all(self) -> None:
        """Unchanged behaviour: a header with no value is no header, so the socket peer stands."""
        assert _stored_ip(_request(forwarded="", peer="198.51.100.4")) == "198.51.100.4"

    def test_a_junk_first_hop_is_not_replaced_by_the_socket_peer(self) -> None:
        """Behind a proxy the peer is the proxy. Recording it would attribute the request to the
        gateway; unknown is the honest answer."""
        assert _stored_ip(_request(forwarded="unknown", peer="10.0.0.1")) is None
