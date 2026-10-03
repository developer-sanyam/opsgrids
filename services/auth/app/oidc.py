"""OpenID Connect — the authorization-code flow, PKCE, and ID-token validation.

Everything in this module is about one question: **may this external issuer tell us who
somebody is?** The answer is only ever yes for a provider an organization configured, on a
domain that organization proved it owns, for a response we can tie back to a request we
ourselves started.

Three separate mechanisms hold that together, and they defend different things:

* **`state`** ties the callback to a request this service started. It is stored server-side
  in Redis, and **consumed atomically** — a replayed callback finds nothing and is refused.
  Without it, an attacker walks their own authorization response into a victim's browser
  and the victim is silently signed in as the attacker (login CSRF).
* **PKCE** ties the *code* to the request. The verifier never leaves this service; only its
  SHA-256 hash goes to the provider. An authorization code stolen out of a redirect — a
  browser log, a referrer header, a malicious extension — cannot be exchanged without it.
* **The `nonce`** ties the *ID token* to the request. A token minted for a different login
  cannot be replayed into this one.

They overlap deliberately. Each covers a capture the others do not.

**No network in tests.** Everything that would touch an identity provider goes through
`IdpClient`, a Protocol with an httpx implementation for production and a fake for tests —
the same shape `directory.py` uses for account-service, and for the same reason: the whole
flow is worth exercising without a provider running.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKSet

from platform_core.egress import EgressBlockedError, EgressGuard, Resolver
from platform_core.errors import ProblemError, UpstreamUnavailableProblem
from platform_core.logging import get_logger

log = get_logger("auth.sso")

#: What an ID token may be signed with. An allow-list rather than "whatever the header
#: says": `alg: none` and the HMAC family are the two classic JWT forgeries, and both are
#: refused by simply never being on this list. Asymmetric only — the provider signs, we
#: verify, and we hold nothing that could sign on its behalf.
ID_TOKEN_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256")

#: Requested unless the configuration says otherwise. `openid` is mandatory; `email` is what
#: the whole flow turns on, since the mapping to a user is by verified address.
DEFAULT_SCOPES = ("openid", "email", "profile")

_STATE_PREFIX = "sso:state:"

T = TypeVar("T")


# --------------------------------------------------------------------- problems
#
# Four failure shapes, each its own RFC 7807 `type`, and none of them says anything about
# who exists. "That address has no account here", "that person is not a member" and "that
# person is deactivated" are one answer, because the difference between them is exactly
# what an attacker with a list of email addresses is trying to buy.


class SsoNotConfiguredProblem(ProblemError):
    """No usable identity provider for this domain.

    Covers all of: the domain belongs to nobody, it belongs to somebody who never verified
    it, the organization has no OIDC configuration, and the configuration is disabled. One
    message for four causes — an unverified domain answering differently from an unknown one
    tells whoever is probing which domains have been claimed. The distinction is kept in the
    log, where it is useful and not disclosed.
    """

    status_code = 404
    problem_type = "sso-not-configured"
    title = "Single sign-on is not available"


class SsoExchangeProblem(ProblemError):
    """The response coming back from the provider was not one we can accept.

    A replayed or expired `state`, a code the provider refused, an ID token whose signature,
    issuer, audience, nonce or expiry does not check out. Deliberately one type: telling a
    caller *which* validation failed is telling them which half of a forgery attempt worked.
    """

    status_code = 401
    problem_type = "sso-exchange-failed"
    title = "Single sign-on could not be completed"


class SsoNoAccessProblem(ProblemError):
    """The provider vouched for somebody this organization does not admit.

    Unknown address, no membership, membership suspended, account deactivated — all one
    answer. The provider authenticated them; this platform still decides who may in, and the
    way in is an invitation (see `OrgIdpConfig.jit_provisioning`).
    """

    status_code = 403
    problem_type = "sso-no-access"
    title = "No access through single sign-on"


class SsoIdentityConflictProblem(ProblemError):
    """The provider's subject does not match the one previously linked to this account.

    A stored `external_identities` row is the strongest binding available: it survives an
    address being reassigned at the provider, which is the takeover this catches — an
    employee leaves, their address is given to somebody new, and the new holder inherits the
    old account. Refusing loudly is right; the fix is an administrator unlinking, which is a
    deliberate act.
    """

    status_code = 409
    problem_type = "sso-identity-conflict"
    title = "That identity is linked to a different account"


# --------------------------------------------------------------------- PKCE


def new_verifier() -> str:
    """A fresh PKCE code verifier — 43-128 unreserved characters (RFC 7636 §4.1)."""
    return secrets.token_urlsafe(64)


def challenge_for(verifier: str) -> str:
    """The S256 challenge for `verifier`.

    S256 only. The `plain` method sends the verifier itself, which defends against nothing
    an attacker who can see the redirect cannot already read.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    nonce: str,
    code_challenge: str,
    login_hint: str | None = None,
) -> str:
    """Where to send the browser.

    `login_hint` is the address the person typed. It is a convenience the provider may
    ignore, and it is safe to pass precisely because it decides nothing here — the
    organization came from the verified domain, and the identity comes back asserted by the
    provider.
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    joiner = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{joiner}{urlencode(params)}"


# --------------------------------------------------------------------- the provider


@dataclass(frozen=True, slots=True)
class TokenResponse:
    """What a token endpoint hands back. Only the ID token is load-bearing here.

    The access token is deliberately *not* stored: this flow authenticates a person, it does
    not act on their behalf at the provider, and keeping a credential nobody needs is only a
    thing to leak.
    """

    id_token: str


class IdpClient(Protocol):
    """The three calls this service makes to an identity provider. Nothing more."""

    async def discover(self, discovery_url: str) -> dict[str, Any]:
        """Read a provider's `.well-known/openid-configuration`."""
        ...

    async def exchange_code(
        self,
        token_endpoint: str,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str,
    ) -> TokenResponse: ...

    async def fetch_jwks(self, jwks_uri: str) -> dict[str, Any]: ...


class HttpIdpClient:
    """The real client. One httpx session, short timeouts, no redirects followed.

    Redirects are off on purpose: a token endpoint that 302s is either misconfigured or
    hostile, and following one would post the client secret to wherever it pointed.

    **Every call goes through `platform_core.egress.EgressGuard`.** `discovery_url`,
    `jwks_uri` and `token_endpoint` are all administrator input — directly, or via a
    discovery document `discover` itself fetched from one — and this service's container
    sits on the docker `back` network alongside every other service and the cache. Fetching
    one of these without the guard is the SSRF this class exists not to be: an admin who can
    set `discovery_url` would otherwise be able to make auth-service ask
    `http://account:8000/internal/...` or `http://169.254.169.254/...` on their behalf.
    """

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        allow_insecure_http: bool = False,
    ) -> None:
        # `transport` and `resolver` exist so this class — the one piece of the flow that
        # speaks HTTP — can be exercised against `httpx.MockTransport` and a stub DNS answer
        # rather than being the untested half of a module whose fake covers everything else.
        # Production passes neither: the guard resolves for real and connects for real, to
        # the address it just vetted.
        self._guard = EgressGuard(
            timeout=timeout,
            inner_transport=transport,
            resolver=resolver,
            allow_insecure_http=allow_insecure_http,
        )

    async def aclose(self) -> None:
        # Nothing persistent to close: the guard opens one connection per request and closes
        # it before returning (see `EgressGuard.request`), which is what pinning each request
        # to its own freshly-vetted address requires. Kept as a no-op method rather than
        # removed so callers that hold this behind the `IdpClient` protocol need no change.
        return None

    async def discover(self, discovery_url: str) -> dict[str, Any]:
        return await self._get(discovery_url)

    async def fetch_jwks(self, jwks_uri: str) -> dict[str, Any]:
        return await self._get(jwks_uri)

    async def _get(self, url: str) -> dict[str, Any]:
        try:
            response = await self._guard.request("GET", url, headers={"Accept": "application/json"})
            response.raise_for_status()
        except EgressBlockedError as exc:
            # Refused before or during the fetch — an internal address, a redirect, an
            # oversized response. The reason is already in the guard's own log line; nothing
            # about *why* is repeated to whoever is holding this URL.
            log.warning("sso.egress_blocked", url=url[:200], reason=str(exc)[:200])
            raise UpstreamUnavailableProblem(
                "The identity provider could not be reached. Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            # A provider being down is not "your credentials are wrong". 503 so the client
            # says "try again", and so it is separable from a real rejection in the logs.
            raise UpstreamUnavailableProblem(
                "The identity provider could not be reached. Please try again."
            ) from exc
        return self._json(response, url=url)

    @staticmethod
    def _json(response: httpx.Response, *, url: str) -> dict[str, Any]:
        """A JSON object, or a 503 — never an unhandled decode error.

        A misconfigured endpoint answering 200 with an HTML login page is the realistic case,
        and letting `.json()` raise through would surface it as a 500 with a stack trace
        rather than as what it is: an upstream we cannot use. The URL is logged, the body
        is not — a token response body is a place a credential can appear.
        """
        try:
            body = response.json()
        except ValueError as exc:
            log.error("sso.provider_response_not_json", url=url, status=response.status_code)
            raise UpstreamUnavailableProblem(
                "The identity provider returned something unusable. Please try again."
            ) from exc
        if not isinstance(body, dict):
            log.error("sso.provider_response_not_an_object", url=url)
            raise UpstreamUnavailableProblem(
                "The identity provider returned something unusable. Please try again."
            )
        return body

    async def exchange_code(
        self,
        token_endpoint: str,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str,
    ) -> TokenResponse:
        """`client_secret_post` plus the PKCE verifier.

        The secret is a form field rather than a Basic header because every provider accepts
        that form and it keeps the credential out of anything that logs request headers. It
        appears in no log line here either — the failure path records the status code and
        nothing else.
        """
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
        }
        try:
            response = await self._guard.request(
                "POST", token_endpoint, data=form, headers={"Accept": "application/json"}
            )
        except EgressBlockedError as exc:
            log.warning("sso.egress_blocked", url=token_endpoint[:200], reason=str(exc)[:200])
            raise UpstreamUnavailableProblem(
                "The identity provider could not be reached. Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableProblem(
                "The identity provider could not be reached. Please try again."
            ) from exc

        if response.status_code >= 400:
            # An expired, replayed or wrong-verifier code lands here. The provider's own
            # error body is not echoed to the caller: it is written for a developer reading
            # a log, not for whoever is holding a stolen code.
            log.warning("sso.token_exchange_rejected", status=response.status_code)
            raise SsoExchangeProblem("That sign-in could not be completed. Please start again.")

        body = self._json(response, url=token_endpoint)
        id_token = body.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            # A token response with no ID token is not OIDC. Refusing beats guessing an
            # identity out of the userinfo endpoint.
            raise SsoExchangeProblem("That sign-in could not be completed. Please start again.")
        return TokenResponse(id_token=id_token)


# --------------------------------------------------------------------- ID token


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """What an accepted ID token asserts. The two fields that decide anything."""

    subject: str
    email: str


def verify_id_token(
    id_token: str,
    *,
    jwks: dict[str, Any],
    issuer: str,
    audience: str,
    nonce: str,
    leeway_seconds: int = 60,
) -> VerifiedIdentity:
    """Validate an ID token and return the identity it asserts, or raise.

    Everything RFC-mandated is checked, and each check is here because skipping it is a
    known break:

    * **signature**, against the provider's published key, chosen by `kid`, restricted to
      asymmetric algorithms (see `ID_TOKEN_ALGORITHMS`);
    * **`iss`**, against the issuer stored in the configuration — a token from *some*
      provider is not a token from *this* organization's provider;
    * **`aud`**, against our `client_id` — a token minted for a different relying party is
      not ours to accept, and honouring one lets any other client of the same provider mint
      logins here;
    * **`exp`/`iat`**, with a small skew allowance;
    * **`nonce`**, against the value stored with the state — this is what makes the token
      un-replayable into a different login;
    * **`email_verified`**, because the mapping to a platform account is by address, and an
      *unverified* address at the provider is an address anybody could have claimed. This is
      the check `ExternalIdentity` is documented never to skip.
    """
    try:
        key = _signing_key(id_token, jwks)
        claims: dict[str, Any] = jwt.decode(
            id_token,
            key,
            algorithms=list(ID_TOKEN_ALGORITHMS),
            issuer=issuer,
            audience=audience,
            leeway=leeway_seconds,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        log.warning("sso.id_token_rejected", reason=type(exc).__name__)
        raise SsoExchangeProblem(
            "That sign-in could not be completed. Please start again."
        ) from exc

    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        log.warning("sso.id_token_rejected", reason="nonce_mismatch")
        raise SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    email = claims.get("email")
    if not isinstance(email, str) or not email:
        log.warning("sso.id_token_rejected", reason="no_email")
        raise SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    # Absent is not verified. Some providers omit the claim entirely for addresses they
    # have not confirmed, and reading a missing claim as true is the whole vulnerability.
    if claims.get("email_verified") is not True:
        log.warning("sso.id_token_rejected", reason="email_unverified")
        raise SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    return VerifiedIdentity(subject=str(claims["sub"]), email=email)


def _signing_key(id_token: str, jwks: dict[str, Any]) -> Any:
    """The published key this token names, by `kid`.

    A token whose header names a key the provider does not publish is refused rather than
    tried against every key: matching by trial would accept a token signed by a retired key
    the provider has already stopped trusting.
    """
    try:
        header = jwt.get_unverified_header(id_token)
        key_set = PyJWKSet.from_dict(jwks)
    except Exception as exc:
        raise SsoExchangeProblem(
            "That sign-in could not be completed. Please start again."
        ) from exc

    kid = header.get("kid")
    if kid is None:
        # A single-key JWKS with no `kid` is legal and common in test providers.
        if len(key_set.keys) == 1:
            return key_set.keys[0].key
        raise SsoExchangeProblem("That sign-in could not be completed. Please start again.")
    try:
        return key_set[kid].key
    except KeyError as exc:
        log.warning("sso.id_token_rejected", reason="unknown_kid")
        raise SsoExchangeProblem(
            "That sign-in could not be completed. Please start again."
        ) from exc


# --------------------------------------------------------------------- state


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """What this service must remember between the redirect out and the callback back.

    Server-side, never in a cookie. A cookie would work for the CSRF property and fail for
    the two that matter more: the PKCE verifier must not be readable by the browser holding
    it, and single-use consumption has to happen somewhere the client cannot forge.
    """

    organization_id: str
    config_id: str
    nonce: str
    code_verifier: str
    domain: str
    created_at: str

    def as_json(self) -> str:
        return json.dumps(
            {
                "organization_id": self.organization_id,
                "config_id": self.config_id,
                "nonce": self.nonce,
                "code_verifier": self.code_verifier,
                "domain": self.domain,
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> AuthorizationRequest:
        data = json.loads(raw)
        return cls(
            organization_id=str(data["organization_id"]),
            config_id=str(data["config_id"]),
            nonce=str(data["nonce"]),
            code_verifier=str(data["code_verifier"]),
            domain=str(data["domain"]),
            created_at=str(data["created_at"]),
        )


@dataclass(frozen=True, slots=True)
class SocialAuthorizationRequest:
    """What `services.oauth_social_start` must remember until `oauth_social_callback`.

    A second pending-request shape alongside `AuthorizationRequest` — same server-side-only
    reasoning (the PKCE verifier must never be readable by the browser holding it), but for
    the platform-wide Google/Microsoft flow rather than per-org enterprise SSO: no
    `organization_id`/`config_id`/`domain` (there is no organization yet), plus `intent` and
    `invitation_token` to carry the signup-vs-invite branch across the redirect.
    """

    provider: str
    intent: str
    invitation_token: str | None
    nonce: str
    code_verifier: str
    created_at: str

    def as_json(self) -> str:
        return json.dumps(
            {
                "provider": self.provider,
                "intent": self.intent,
                "invitation_token": self.invitation_token,
                "nonce": self.nonce,
                "code_verifier": self.code_verifier,
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> SocialAuthorizationRequest:
        data = json.loads(raw)
        return cls(
            provider=str(data["provider"]),
            intent=str(data["intent"]),
            invitation_token=data.get("invitation_token"),
            nonce=str(data["nonce"]),
            code_verifier=str(data["code_verifier"]),
            created_at=str(data["created_at"]),
        )


class _PendingRequest(Protocol):
    """What `StateStore.issue` needs from whatever it is asked to store.

    Both `AuthorizationRequest` and `SocialAuthorizationRequest` satisfy this structurally —
    `issue` itself never needs to know which "pending request" shape it was handed, only
    that it can be turned back into JSON. Narrower than typing it `AuthorizationRequest`,
    the same widening `consume`'s own `parse` parameter already went through (Task 3).
    """

    def as_json(self) -> str: ...


class StateStore:
    """Pending authorization requests, in Redis, single-use.

    **Fails closed, unlike the platform cache.** `platform_core.cache` swallows Redis errors
    because its callers have a source of truth to fall back on; this one has none, and
    treating "cannot check" as "not replayed" would turn a Redis outage into an open door.
    Every method raises rather than degrades.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def issue(self, request: _PendingRequest, *, ttl_seconds: int) -> str:
        """Store a pending request under a fresh opaque state and return it.

        `nx=True` so a state can never overwrite one already in flight. A collision on 256
        bits of entropy means something is wrong with the entropy source, and quietly
        clobbering the earlier request would be a way to break somebody else's sign-in.
        """
        state = secrets.token_urlsafe(32)
        try:
            stored = await self._redis.set(
                _STATE_PREFIX + state, request.as_json(), ex=ttl_seconds, nx=True
            )
        except Exception as exc:
            log.error("sso.state_store_unavailable", error=str(exc)[:120])
            raise UpstreamUnavailableProblem(
                "Single sign-on is temporarily unavailable. Please try again."
            ) from exc
        if not stored:  # pragma: no cover - 256 bits of collision
            raise UpstreamUnavailableProblem(
                "Single sign-on is temporarily unavailable. Please try again."
            )
        return state

    async def consume(
        self,
        state: str,
        parse: Callable[[str], T] = AuthorizationRequest.from_json,  # type: ignore[assignment]
    ) -> T:
        """Read a pending request **and delete it, atomically**.

        `GETDEL` rather than get-then-delete: two round trips leave a window in which the
        same state is read twice, which is precisely the replay this exists to stop. A state
        that is missing — never issued, already used, or expired — is one answer, because
        telling the two apart tells an attacker whether their captured value was ever real.

        `parse` defaults to `AuthorizationRequest.from_json`, so every existing caller is
        unaffected — it exists so a second pending-request shape, stored under this same
        single-use mechanism, can be read back as itself rather than coerced into this one.
        """
        try:
            raw = await self._redis.getdel(_STATE_PREFIX + state)
        except Exception as exc:
            log.error("sso.state_store_unavailable", error=str(exc)[:120])
            raise UpstreamUnavailableProblem(
                "Single sign-on is temporarily unavailable. Please try again."
            ) from exc
        if raw is None:
            log.warning("sso.state_not_found")
            raise SsoExchangeProblem("That sign-in has expired. Please start again.")
        try:
            return parse(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            raise SsoExchangeProblem("That sign-in has expired. Please start again.") from exc


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "DEFAULT_SCOPES",
    "ID_TOKEN_ALGORITHMS",
    "AuthorizationRequest",
    "HttpIdpClient",
    "IdpClient",
    "SocialAuthorizationRequest",
    "SsoExchangeProblem",
    "SsoIdentityConflictProblem",
    "SsoNoAccessProblem",
    "SsoNotConfiguredProblem",
    "StateStore",
    "TokenResponse",
    "VerifiedIdentity",
    "authorization_url",
    "challenge_for",
    "new_verifier",
    "now_iso",
    "verify_id_token",
]
