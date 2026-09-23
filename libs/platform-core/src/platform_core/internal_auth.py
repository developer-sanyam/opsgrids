"""Service-to-service credentials.

The internal API reads across every tenant, and until now it was guarded by a **static shared
secret sent in a header on every call**. That has two problems, and the second is the worse
one: a leaked value is valid forever with no way to tell it has leaked, and the secret itself
travelled on the wire, so anything that captured request headers — a proxy log, an error
report, a debugging session — captured the credential rather than a use of it.

What replaces it is a short-lived HMAC token, minted per call, naming who is calling and which
service it is for:

    iss  the calling service          aud  the service being called
    exp  60 seconds from now          jti  unique per token

**Honest about what this does and does not fix.** The signing key is still shared between
services, so this is not asymmetric identity — a service that can verify can also mint, and a
leaked *key* is still a full compromise. What it buys is that the key stops being transmitted:
the wire now carries a derived, expiring, audience-bound token. A captured header is useful for
sixty seconds against one service instead of forever against all of them.

The end state is per-service keypairs or mTLS, and this is deliberately shaped so that only
`_sign` and `_verify` change when that happens — every call site already passes a caller and an
audience, which is the part that would otherwise need retrofitting.

The same machinery covers **both** internal channels. Service-to-service `/internal/v1` calls
mint per-callee tokens (`aud` = the service being called). The gateway's identity-forwarding
hop — the highest-volume channel, and the last place the raw secret used to travel — mints a
per-request token on the `GATEWAY_AUDIENCE` channel with the forwarded `uid`/`org` bound in,
so a capture can neither open an internal endpoint nor vouch for any other identity.

Replay inside the TTL window is not prevented. Doing so needs a `jti` ledger in Redis on the
*callee*, which puts a hard dependency on Redis in front of every internal call — including the
gateway's permission lookup on every authenticated request. That trade is not obviously worth it
at a sixty-second window; it is recorded here rather than silently omitted.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import jwt

from platform_core.config import ServiceSettings

ALGORITHM = "HS256"

#: Short on purpose. Long enough for a call and a retry, far too short to be worth stealing.
DEFAULT_TTL_SECONDS = 60

#: The audience of the gateway's identity-forwarding tokens. A channel name rather than a
#: service name, because the gateway proxies to every service and the route table maps URL
#: prefixes to ports, not to service identities. Deliberately distinct from any
#: `service_name`, so a captured forwarding token can never open an `/internal/v1` endpoint —
#: `verify_service_token` checks `aud == settings.service_name` and this value never matches.
GATEWAY_AUDIENCE = "opsgrids-gateway-forward"


def mint_service_token(
    settings: ServiceSettings,
    *,
    audience: str,
    caller: str | None = None,
    claims: dict[str, Any] | None = None,
) -> str:
    """A credential for one call to one service.

    `audience` is what stops a token minted for account-service being replayed against
    billing-service — the static secret it replaces was accepted by everything.

    `claims` lets a caller bind extra facts into the token — the gateway binds the forwarded
    user and organization ids, so a captured forwarding token cannot vouch for any *other*
    identity. Reserved JWT claim names cannot be overridden.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = dict(claims or {})
    payload.update(
        {
            "iss": caller or settings.service_name,
            "aud": audience,
            "iat": now,
            "exp": now + timedelta(seconds=settings.service_token_ttl_seconds),
            "jti": uuid.uuid4().hex,
            "typ": "service",
        }
    )
    return jwt.encode(
        payload, settings.internal_service_token.get_secret_value(), algorithm=ALGORITHM
    )


def verify_service_token(settings: ServiceSettings, token: str) -> dict[str, Any]:
    """Decode and check a service token addressed to *this* service.

    Raises `jwt.PyJWTError` on anything wrong — signature, expiry, audience, or type. The
    caller turns that into a 401; this deliberately does not, so the same function is usable
    outside a request.
    """
    claims: dict[str, Any] = jwt.decode(
        token,
        settings.internal_service_token.get_secret_value(),
        algorithms=[ALGORITHM],
        audience=settings.service_name,
        options={"require": ["exp", "iss", "aud", "jti"]},
    )
    if claims.get("typ") != "service":
        # A user token must never open a service endpoint, whatever else it carries.
        raise jwt.InvalidTokenError("not a service token")
    return claims


def verify_gateway_token(settings: ServiceSettings, token: str) -> dict[str, Any]:
    """Decode and check a gateway identity-forwarding token.

    This is what replaced the raw shared secret the gateway used to send on **every** proxied
    request: a header capture used to yield the signing key itself — forge any identity
    against any service, forever, and mint `/internal/v1` credentials with it. Now the wire
    carries a derived, expiring token bound to the one identity being forwarded.

    The audience is the forwarding *channel*, not a service name, so this token opens no
    `/internal/v1` endpoint (those verify `aud == service_name`). Replay within the TTL
    against a sibling service forwards only the same `uid`/`org` the token names —
    `_principal_from_gateway` refuses identity headers the token does not vouch for.

    Raises `jwt.PyJWTError` on anything wrong, like `verify_service_token`.
    """
    claims: dict[str, Any] = jwt.decode(
        token,
        settings.internal_service_token.get_secret_value(),
        algorithms=[ALGORITHM],
        audience=GATEWAY_AUDIENCE,
        options={"require": ["exp", "iss", "aud", "jti"]},
    )
    if claims.get("typ") != "service":
        raise jwt.InvalidTokenError("not a service token")
    return claims


#: The header the admin-gateway hop signature travels on. Never a valid `Authorization`
#: header shape, and distinct from `x-internal-token`, so it cannot be confused with either.
ADMIN_HOP_HEADER = "x-admin-gateway-hop"

#: Short — long enough for one proxied call and a retry through the same gateway hop, far too
#: short for a captured value to be worth anything once it has been logged or cached anywhere.
ADMIN_HOP_TTL_SECONDS = 30

#: How long the callee remembers a nonce it has accepted. Must exceed the signature's own
#: validity window in *both* directions (a timestamp up to `ADMIN_HOP_TTL_SECONDS` in the past
#: or the future still verifies), or a nonce could expire from the store while its signature is
#: still good and be replayed inside the gap. Five seconds of margin over the two windows, so
#: clock skew between the callee and the store, or a request that verifies right at the edge,
#: cannot land after the nonce has already expired.
ADMIN_HOP_NONCE_TTL_SECONDS = 2 * ADMIN_HOP_TTL_SECONDS + 5
_HOP_TS_RE = re.compile(r"[0-9]{1,12}")


def canonical_query(pairs: Iterable[tuple[str, str]]) -> str:
    """One canonical spelling of a query string: the pairs sorted, then URL-encoded. The
    gateway and the callee each build it from the pairs *they* see, so a request whose query
    was reordered or percent-encoded differently in transit still verifies, and one whose
    query was actually changed does not."""
    return urlencode(sorted(pairs))


def _hop_mac(key: str, method: str, path: str, query: str, body: bytes, ts: str, nonce: str) -> str:
    material = "\n".join([method.upper(), path, query, hashlib.sha256(body).hexdigest(), ts, nonce])
    return hmac.new(key.encode(), material.encode(), hashlib.sha256).hexdigest()


def sign_admin_hop(method: str, path: str, *, key: str, query: str = "", body: bytes = b"") -> str:
    """BLOCKER 2 of the 2026-09-24 security review: defence in depth for the operator surface,
    on top of every gateway's route table blocking `/api/v1/auth/operator` outright.

    An HMAC over `METHOD \n path \n canonical_query \n sha256(body) \n ts \n nonce` (m2 of the
    third-round review — the first version bound only method, path and timestamp, so a captured
    header could be replayed against the same path with a different body or query) under a key
    that is **not** the general `internal_service_token`: a deliberately separate secret
    (`ADMIN_GATEWAY_HOP_KEY`), so a leak of the everyday internal-token secret does not also
    hand over this one. The header value is `ts.nonce.mac`; the callee refuses a nonce it has
    already accepted (`platform_core.cache.reserve_once`), which is what turns "valid for 30
    seconds" into "valid once".

    Only a gateway whose `GatewayPolicy.sign_admin_hop` is set (admin-gateway) ever calls this.
    """
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    return f"{ts}.{nonce}.{_hop_mac(key, method, path, query, body, ts, nonce)}"


def verify_admin_hop(
    header_value: str | None,
    method: str,
    path: str,
    *,
    key: str,
    query: str = "",
    body: bytes = b"",
    now: float | None = None,
) -> str | None:
    """Check a hop signature `sign_admin_hop` produced. Returns the header's **nonce** when it
    is valid (truthy, so `assert verify_admin_hop(...)` still reads naturally) and `None` for
    anything wrong — missing, malformed, expired, signed under a different key, or signed for a
    different method, path, query or body. Never raises, so a caller can turn every failure into
    the same 401 without special-casing shapes of bad input. It does **not** check the nonce
    against a store: that is the caller's job, after this passes, because it needs Redis."""
    if not header_value:
        return None
    parts = header_value.split(".")
    if len(parts) != 3:
        return None
    ts_raw, nonce, mac = parts
    # ASCII digits only, and bounded: `str.isdigit()` is true for '²' and other Unicode digits
    # that `int()` then either rejects with a ValueError (an unhandled 500 here) or, for a
    # 5000-digit string, refuses past Python's int-conversion limit. Twelve digits covers every
    # timestamp until the year 33658.
    if not _HOP_TS_RE.fullmatch(ts_raw) or not nonce:
        return None
    current = now if now is not None else time.time()
    if abs(current - int(ts_raw)) > ADMIN_HOP_TTL_SECONDS:
        return None
    expected = _hop_mac(key, method, path, query, body, ts_raw, nonce)
    return nonce if hmac.compare_digest(expected, mac) else None


__all__ = [
    "ADMIN_HOP_HEADER",
    "ADMIN_HOP_NONCE_TTL_SECONDS",
    "ADMIN_HOP_TTL_SECONDS",
    "ALGORITHM",
    "DEFAULT_TTL_SECONDS",
    "GATEWAY_AUDIENCE",
    "canonical_query",
    "mint_service_token",
    "sign_admin_hop",
    "verify_admin_hop",
    "verify_gateway_token",
    "verify_service_token",
]
