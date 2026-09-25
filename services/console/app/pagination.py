"""Opaque keyset-pagination cursors for console's admin lists.

Not `platform_core.pagination.Page`: that helper is offset-based (page/size) — the right
default for a tenant-scoped list, and the wrong shape once a caller is asking for the platform's
own `/admin/audit` or `/admin/usage`, which read cross-tenant and are the ones the audit flagged
as "unpaginated, full cross-tenant sets… plan paging before ~1k orgs." A cursor here is nothing
more than the columns the list already orders by, opaque only because the caller never needs to
parse it — the same shape account-service's own internal `/organizations` backfill page already
uses (`next_cursor`, requested back as `cursor` until it comes back null).
"""

from __future__ import annotations

import base64

from platform_core.errors import ValidationProblem

#: Unit separator — never a legal character in a UUID, an ISO timestamp or a meter/period key,
#: so joining and splitting on it is unambiguous without escaping.
_SEP = "\x1f"


def encode_cursor(*parts: str) -> str:
    """Pack the ordering columns of the last row on a page into one opaque token."""
    return base64.urlsafe_b64encode(_SEP.join(parts).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str, *, expected_parts: int) -> list[str]:
    """Unpack a token this module produced. A cursor is never client-authored data to trust
    blindly — a caller could hand back a stale, doctored or foreign one — so a malformed token
    is a 400, not a 500 from an index error two frames later."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        parts = raw.split(_SEP)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationProblem("Invalid cursor") from exc
    if len(parts) != expected_parts:
        raise ValidationProblem("Invalid cursor")
    return parts
