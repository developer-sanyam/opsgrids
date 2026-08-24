"""Minting tokens.

Two shapes, and the difference matters.

**Access tokens are per audience and carry no permission list.** One product's token names
that product in `aud` and nothing else. Five products at roughly forty permissions each is
5-6 KB of `Authorization` header once encoded, which is past what several proxies accept —
and the failure mode is a truncated header surfacing as a silent 500 rather than an error
anyone can read. The gateway resolves permissions per request instead, keyed by `pv`.

**Refresh tokens carry almost nothing** — a subject, a family and a session. They are
presented to one endpoint, over one origin, and every extra claim is another thing to keep
consistent across a rotation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.keys import KeyPair, SigningKeyUnavailable

ACCESS = "access"
REFRESH = "refresh"
#: Issued after a password check but before MFA or organization selection. Deliberately
#: useless for anything else: it names no organization and carries no entitlements.
PRE_AUTH = "pre_auth"


@dataclass(frozen=True, slots=True)
class MintedToken:
    token: str
    jti: str
    expires_at: datetime
    expires_in: int


def _sign(pair: KeyPair, claims: dict[str, object]) -> str:
    if pair.private is None:
        raise SigningKeyUnavailable("This process holds no private key and cannot mint")
    return jwt.encode(claims, pair.private, algorithm="EdDSA", headers={"kid": pair.kid})


def _base(
    issuer: str, subject: uuid.UUID, ttl: int, typ: str
) -> tuple[dict[str, object], datetime, str]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl)
    jti = uuid.uuid4().hex
    claims: dict[str, object] = {
        "iss": issuer,
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": jti,
        "typ": typ,
    }
    return claims, expires_at, jti


def mint_access(
    pair: KeyPair,
    *,
    issuer: str,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID | None,
    audience: str,
    session_id: uuid.UUID,
    entitlements: list[str],
    roles: list[str],
    locations: list[str],
    amr: list[str],
    permission_version: int,
    subject_ref: uuid.UUID | None,
    ttl_seconds: int,
    staff: bool = False,
) -> MintedToken:
    """An access token for one product — or, when `staff`, for the operator surface.

    Note what is absent: `perms`. See the module docstring — the gateway resolves them.

    `staff` is the **only** thing that puts a `stf` claim in a token, and it defaults off.
    Every tenant caller (via `services.issue_for`) leaves it off, so a product/app token can
    never carry `stf`: the operator gate at `admin-gateway` keys on that claim, and a token
    that lacks it gets 404 on a staff route (ADR-0003 §2). An operator token is the same
    signature, the same key, the same JWKS verify path — it differs only in its audience and
    this one boolean. `membership_id` is `None` for operators (they hold no membership), and
    the `mem` claim is then omitted so the gateway resolves no tenant grants for them.
    """
    claims, expires_at, jti = _base(issuer, user_id, ttl_seconds, ACCESS)
    claims |= {
        "aud": audience,
        "act_org": str(organization_id),
        "sid": str(session_id),
        "ent": entitlements,
        "roles": roles,
        "loc": locations,
        "amr": amr,
        # The permission version. The gateway compares it against the organization's
        # current value, so a revoked grant stops working on the next request rather
        # than when this token happens to expire.
        "pv": permission_version,
    }
    if membership_id is not None:
        claims["mem"] = str(membership_id)
    if subject_ref is not None:
        claims["sref"] = str(subject_ref)
    if staff:
        # Set only here, only on the operator path. Its presence is what the staff-only
        # gateway routes check; its absence on every tenant token is what keeps a stolen
        # product token inert on the operator surface.
        claims["stf"] = True
    return MintedToken(_sign(pair, claims), jti, expires_at, ttl_seconds)


def mint_refresh(
    pair: KeyPair,
    *,
    issuer: str,
    user_id: uuid.UUID,
    family: str,
    session_id: uuid.UUID,
    organization_id: uuid.UUID | None,
    ttl_seconds: int,
) -> MintedToken:
    claims, expires_at, jti = _base(issuer, user_id, ttl_seconds, REFRESH)
    claims |= {
        "fam": family,
        "sid": str(session_id),
        "act_org": str(organization_id) if organization_id else None,
    }
    return MintedToken(_sign(pair, claims), jti, expires_at, ttl_seconds)


def mint_pre_auth(
    pair: KeyPair,
    *,
    issuer: str,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    reason: str,
    ttl_seconds: int = 600,
) -> MintedToken:
    """Proof that a password was accepted, and nothing more.

    Carried between the password step and whichever step comes next — MFA, or choosing an
    organization. It names no organization and grants nothing, so intercepting one buys an
    attacker only the step they were already at.
    """
    claims, expires_at, jti = _base(issuer, user_id, ttl_seconds, PRE_AUTH)
    claims |= {"sid": str(session_id), "reason": reason}
    return MintedToken(_sign(pair, claims), jti, expires_at, ttl_seconds)


def decode_local(
    pair: KeyPair, token: str, *, issuer: str, expected_type: str, audience: str | None = None
) -> dict[str, object]:
    """Verify with the key this service holds.

    Used for refresh and pre-auth tokens, which never leave this service. Product
    services verify access tokens through JWKS instead, holding no private key at all.
    """
    claims: dict[str, object] = jwt.decode(
        token,
        pair.public,
        algorithms=["EdDSA"],
        issuer=issuer,
        audience=audience,
        options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": audience is not None},
    )
    if claims.get("typ") != expected_type:
        raise jwt.InvalidTokenError(f"Expected a {expected_type} token")
    return claims
