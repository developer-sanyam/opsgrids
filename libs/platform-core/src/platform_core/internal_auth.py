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

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

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
GATEWAY_AUDIENCE = "atmora-gateway-forward"


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


__all__ = [
    "ALGORITHM",
    "DEFAULT_TTL_SECONDS",
    "GATEWAY_AUDIENCE",
    "mint_service_token",
    "verify_gateway_token",
    "verify_service_token",
]
