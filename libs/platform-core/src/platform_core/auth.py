"""Identity, permissions and scope — the security core.

A permission is ``product.module.resource.action`` paired with a **scope**:

    OWN       only rows the caller owns
    TEAM      rows belonging to the caller's reports
    LOCATION  rows at one of the caller's locations
    ORG       every row in the organization

Two rules follow from this and are not negotiable.

**Holding a permission is not holding enough scope.** Every employee holds
``hr.profile.employee.write:OWN`` so they can maintain their own record. An endpoint
guarded only by the permission *name* therefore lets any employee create employee records.
Actions with no meaningful "own" reading pass ``minimum=Scope.ORG``.

**Filtering the list and forgetting the detail is the named vulnerability.** ``apply()``
narrows a query; ``assert_can_touch()`` guards a single object. Both are needed, and
LOCATION really does compare locations here — a scope that returns early is a hole.

**"The caller" is one of two ids, and the product says which.** OWN and TEAM compare a row's
owner with ``ScopeContext.own_ref``. For every product that has not said otherwise that is the
caller's *employee link* (``Principal.subject_ref``: an HR employee id, null for a member with
no employee record). A product whose users may have no HR record registers its scope resolver
with ``owner_subject=OwnerSubject.MEMBERSHIP`` and ``own_ref`` is then the caller's *membership
id* (``Principal.membership_id``). Neither is ever a fallback for the other: a caller with no
such id owns nothing, and passes no object check.
"""

from __future__ import annotations

import enum
import hmac
import uuid
from collections.abc import Callable
from typing import Annotated, Any, Protocol

import jwt
from fastapi import Depends, Request
from jwt import PyJWKClient
from pydantic import BaseModel, Field
from sqlalchemy import Select, or_
from sqlalchemy import false as sa_false

from platform_core.config import ServiceSettings
from platform_core.errors import (
    ForbiddenProblem,
    NotFoundProblem,
    PaymentRequiredProblem,
    UnauthorizedProblem,
    UpstreamUnavailableProblem,
)
from platform_core.internal_auth import (
    forwarded_headers_digest,
    verify_gateway_token,
    verify_service_token,
)
from platform_core.logging import get_logger

#: The audience every operator (staff) token carries — minted only by `services.operator_login`
#: in auth-service, never by a tenant sign-in. `require_operator` is what every staff_only route
#: across services (console, catalog, account, billing, …) guards with, so the audience string
#: lives here once rather than being copy-pasted per service.
OPERATOR_AUDIENCE = "operator"


class Scope(enum.StrEnum):
    OWN = "OWN"
    TEAM = "TEAM"
    LOCATION = "LOCATION"
    ORG = "ORG"

    @property
    def rank(self) -> int:
        return {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}[self.value]


class Principal(BaseModel):
    """Who is calling, on behalf of which organization, for which product."""

    sub: uuid.UUID
    organization_id: uuid.UUID
    membership_id: uuid.UUID | None = None
    #: The caller's employee link: the HR employee id, or None when the membership has none. OWN
    #: and TEAM compare it for every product that has not declared `OwnerSubject.MEMBERSHIP`.
    subject_ref: uuid.UUID | None = None
    audience: str | None = None
    session_id: str | None = None
    #: Activated products, used for the entitlement gate.
    entitlements: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    #: ``name:SCOPE`` grants. Resolved by the gateway — never carried in the token.
    perms: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    #: Authentication methods used, e.g. ["pwd", "otp"]. Drives step-up.
    amr: list[str] = Field(default_factory=list)
    permission_version: int = 0
    jti: str | None = None

    def scope_for(self, permission: str) -> Scope | None:
        """Widest scope held for `permission`, or None. Order-independent."""
        best: Scope | None = None
        for grant in self.perms:
            name, _, scope_name = grant.partition(":")
            if not _matches(name, permission):
                continue
            # A grant with no suffix is the narrowest reading, not the widest.
            scope = Scope(scope_name) if scope_name else Scope.OWN
            if best is None or scope.rank > best.rank:
                best = scope
        return best

    def has(self, permission: str) -> bool:
        return self.scope_for(permission) is not None

    def has_product(self, product: str) -> bool:
        return product in self.entitlements


def _matches(granted: str, required: str) -> bool:
    """Exact match, or a trailing ``.*`` covering a subtree.

    ``crm.*`` covers ``crm.pipeline.deal.read``. The wildcard keeps the dot, so
    ``hr.employee.read`` never matches ``hr.employee.read_sensitive`` — masked and
    unmasked PII must not collapse into one grant.
    """
    if granted == required:
        return True
    if granted.endswith(".*"):
        return required.startswith(granted[:-1])
    return False


# --------------------------------------------------------------------- token verification

_jwk_client: PyJWKClient | None = None

#: PyJWT's own default, stated so the runbook's "up to 30 seconds" is the number that runs. Never
#: 0: the cooldown is what keeps a stream of forged `kid`s from becoming a stream of JWKS fetches.
JWKS_REFRESH_COOLDOWN_SECONDS = 30


def _jwks(settings: ServiceSettings) -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        # Caches keys and refreshes on an unknown kid, which is what makes rotation
        # a publish-then-switch rather than a coordinated restart.
        _jwk_client = PyJWKClient(
            settings.jwks_url,
            cache_keys=True,
            lifespan=300,
            cooldown_duration=JWKS_REFRESH_COOLDOWN_SECONDS,
        )
    return _jwk_client


def reset_jwks_cache() -> None:
    """Tests and key rotation drills."""
    global _jwk_client
    _jwk_client = None


def decode_token(
    settings: ServiceSettings,
    token: str,
    *,
    expected_type: str,
    audience: str | None = None,
) -> dict[str, Any]:
    """Verify a token against the platform's public keys.

    Note what this module does **not** provide: a way to mint. Only auth-service holds a
    private key, so a compromised product service cannot forge identity.
    """
    try:
        signing_key = _jwks(settings).get_signing_key_from_jwt(token).key
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=audience,
            options={
                "require": ["exp", "iat", "sub", "iss"],
                "verify_aud": audience is not None,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedProblem("Token has expired") from exc
    except (jwt.PyJWKClientConnectionError, jwt.PyJWKSetError) as exc:
        # Our outage, not a bad credential: a 401 here makes every SPA sign its user out. The
        # logger is fetched here, not at import, so a configured cached logger cannot go stale.
        get_logger("platform_core.auth").error("auth.jwks_unavailable", error=str(exc)[:200])
        raise UpstreamUnavailableProblem(
            "Your session cannot be verified right now. Please retry shortly."
        ) from exc
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        raise UnauthorizedProblem("Token is not valid") from exc

    if claims.get("typ") != expected_type:
        raise UnauthorizedProblem(f"Expected a {expected_type} token")
    return claims


def _principal_from_claims(claims: dict[str, Any]) -> Principal:
    org = claims.get("act_org")
    if not org:
        # No default organization exists, by design (ADR-0000 §1). A token without one
        # is a bug, and guessing is how tenants read each other's data.
        raise UnauthorizedProblem("Token carries no organization")
    return Principal(
        sub=uuid.UUID(str(claims["sub"])),
        organization_id=uuid.UUID(str(org)),
        membership_id=uuid.UUID(str(claims["mem"])) if claims.get("mem") else None,
        subject_ref=uuid.UUID(str(claims["sref"])) if claims.get("sref") else None,
        audience=claims.get("aud") if isinstance(claims.get("aud"), str) else None,
        session_id=claims.get("sid"),
        entitlements=list(claims.get("ent", [])),
        roles=list(claims.get("roles", [])),
        locations=list(claims.get("loc", [])),
        amr=list(claims.get("amr", [])),
        permission_version=int(claims.get("pv", 0)),
        jti=claims.get("jti"),
    )


def _csv(request: Request, header: str) -> list[str]:
    return [p for p in request.headers.get(header, "").split(",") if p]


def _verified_hop(request: Request, settings: ServiceSettings) -> dict[str, Any] | None:
    """The gateway hop token's claims, or `None` when the request carries no valid one.

    A valid token whose `fwd` digest does not match the forwarded headers raises: those headers
    were altered after the gateway signed them (a captured token replayed with escalated
    `X-Perms`, say), and that must not fall back to anything.
    """
    internal = request.headers.get("X-Internal-Token")
    if not internal:
        return None
    try:
        hop = verify_gateway_token(settings, internal)
    except Exception:
        # Not a forwarding token — perhaps a service token for an internal route, perhaps
        # garbage. Fall through to bearer authentication rather than judging it here.
        return None
    if not hmac.compare_digest(str(hop.get("fwd", "")), forwarded_headers_digest(request.headers)):
        raise UnauthorizedProblem("Forwarded identity does not match the hop token")
    return hop


def _principal_from_gateway(request: Request, settings: ServiceSettings) -> Principal | None:
    """Identity forwarded by a gateway, trusted only with a valid forwarding token.

    The gateway verifies the JWT once and resolves permissions; services take its word
    because a short-lived signed token proves the hop **and names the identity being
    forwarded**. This replaced a raw shared-secret comparison: the secret doubling as the
    service-token signing key travelled on every proxied request, so one header capture
    could forge any user in any organization against any service, forever. A captured
    forwarding token is now good for the token TTL, for one user in one organization, and
    opens no `/internal/v1` endpoint (its audience is the forwarding channel, never a
    service name). Any service reachable *without* going through a gateway must still not
    be reachable from outside the cluster. Every other forwarded header (`X-Perms`,
    `X-Audience`, `X-Amr`, …) is bound too, through the token's `fwd` digest.
    """
    hop = _verified_hop(request, settings)
    if hop is None:
        return None
    user_id = request.headers.get("X-User-Id")
    org_id = request.headers.get("X-Org-Id")
    if not user_id or not org_id:
        return None
    if hop.get("uid") != user_id or hop.get("org") != org_id:
        # A valid hop token accompanied by identity headers it does not vouch for is not a
        # configuration mistake — it is tampering, and it must not fall back to anything.
        raise UnauthorizedProblem("Forwarded identity does not match the hop token")

    try:
        sub = uuid.UUID(user_id)
        organization_id = uuid.UUID(org_id)
    except ValueError as exc:
        raise UnauthorizedProblem("Malformed identity headers") from exc

    def opt_uuid(name: str) -> uuid.UUID | None:
        raw = request.headers.get(name)
        if not raw:
            return None
        try:
            return uuid.UUID(raw)
        except ValueError as exc:
            raise UnauthorizedProblem(f"Malformed {name}") from exc

    return Principal(
        sub=sub,
        organization_id=organization_id,
        membership_id=opt_uuid("X-Membership-Id"),
        subject_ref=opt_uuid("X-Subject-Ref"),
        audience=request.headers.get("X-Audience"),
        session_id=request.headers.get("X-Session-Id"),
        entitlements=_csv(request, "X-Entitlements"),
        roles=_csv(request, "X-Roles"),
        perms=_csv(request, "X-Perms"),
        locations=_csv(request, "X-Locations"),
        amr=_csv(request, "X-Amr"),
        permission_version=int(request.headers.get("X-Permission-Version") or 0),
    )


def client_ip(request: Request | None) -> str | None:
    """The visitor's address, for audit rows and anything else that records one.

    Behind a gateway the TCP peer is the gateway's container, so the gateway forwards the
    address it resolved in `X-Client-Ip`. That header is believed only under a hop token that
    binds it; otherwise — a direct caller, a tampered hop — the peer is the honest answer.
    """
    if request is None:
        return None
    forwarded = request.headers.get("X-Client-Ip")
    if forwarded and request.headers.get("X-Internal-Token"):
        try:
            if _verified_hop(request, request.app.state.settings) is not None:
                return forwarded
        except UnauthorizedProblem:
            pass
    return request.client.host if request.client else None


def get_principal(request: Request) -> Principal:
    """Resolve the caller: gateway headers first, then a bearer token."""
    settings: ServiceSettings = request.app.state.settings

    forwarded = _principal_from_gateway(request, settings)
    if forwarded is not None:
        return forwarded

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise UnauthorizedProblem("Missing bearer token")
    claims = decode_token(settings, header.removeprefix("Bearer ").strip(), expected_type="access")
    # A directly-presented token carries no permission list by design, so a caller that
    # bypasses the gateway is authenticated but holds nothing. Default deny.
    return _principal_from_claims(claims)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


# --------------------------------------------------------------------- scope resolution


class OwnerSubject(enum.StrEnum):
    """Whose id OWN and TEAM compare a row's owner column with.

    A product declares it once, when it registers its scope resolver
    (``register_scope_resolver(..., owner_subject=...)``); ``require()`` snapshots it onto the
    ``ScopeContext``, and ``ScopeContext.own_ref`` is the one accessor that reads it.
    """

    #: ``Principal.subject_ref`` — the caller's employee link. Today's behaviour, and the default
    #: for every product that does not say otherwise.
    EMPLOYEE_LINK = "employee_link"
    #: ``Principal.membership_id`` — the caller's membership in the organization. What a product
    #: with no HR link (a CRM-only tenant's rep) needs.
    MEMBERSHIP = "membership"


class ScopeResolver(Protocol):
    """How one product turns a scope into a filter.

    TEAM and LOCATION are HR-shaped ideas. CRM's natural narrowing is territory or
    account ownership. Rather than force every product onto one reading, each registers
    how its own rows are scoped.

    The registration also declares whose id OWN and TEAM compare:
    ``register_scope_resolver(product, factory, owner_subject=...)``. That lives on the
    registration, not on this Protocol, because a new required member here would break strict
    mypy in every service that implements it. ``ScopeContext.apply`` does not call these
    methods: each selector passes its own columns, so they describe a product's rows rather
    than enforce them.
    """

    def owner_column(self) -> Any: ...
    def team_column(self) -> Any | None: ...
    def location_column(self) -> Any | None: ...


class _ColumnScopeResolver:
    """A `ScopeResolver` that is nothing but its three columns."""

    def __init__(self, owner: Any, team: Any | None, location: Any | None) -> None:
        self._owner = owner
        self._team = team
        self._location = location

    def owner_column(self) -> Any:
        return self._owner

    def team_column(self) -> Any | None:
        return self._team

    def location_column(self) -> Any | None:
        return self._location


def column_scope_resolver(
    *, owner: Any, team: Any | None = None, location: Any | None = None
) -> Callable[[], ScopeResolver]:
    """A factory for the common `ScopeResolver`: one owner column, and TEAM / LOCATION either
    named or (the default) unsupported.

    Pass the result to `register_scope_resolver(product, factory)`. A product whose rows are only
    ever narrowed by an owner column — which is most of them — needs no resolver class of its own.
    """

    def factory() -> ScopeResolver:
        return _ColumnScopeResolver(owner, team, location)

    return factory


_resolvers: dict[str, tuple[Callable[[], ScopeResolver], OwnerSubject]] = {}


def register_scope_resolver(
    product: str,
    factory: Callable[[], ScopeResolver],
    *,
    owner_subject: OwnerSubject = OwnerSubject.EMPLOYEE_LINK,
) -> None:
    """Declare how `product` scopes its rows, and whose id its OWN and TEAM rungs compare.

    Runs at import in each service's routes module, and one process may reach it twice for the
    same product (customer registers `crm` from two modules), so it is idempotent: the same
    subject again is a no-op that keeps the first factory. A **different** subject raises —
    silently flipping which id a product's scope compares is a scope defect, not a last-write-
    wins convenience.
    """
    existing = _resolvers.get(product)
    if existing is not None:
        if existing[1] is not owner_subject:
            raise ValueError(
                f"scope resolver for {product!r} is already registered with owner subject "
                f"{existing[1].value!r}; refusing to re-register it as {owner_subject.value!r}"
            )
        return
    _resolvers[product] = (factory, owner_subject)


def owner_subject_for(permission: str) -> OwnerSubject:
    """The subject the product that owns `permission` declared; the employee link when none did.

    The product is the text before the first dot. A miss is safe by construction: a permission
    whose prefix is not a registered product (notification registers `notification` while its
    Engage routes require `engage.*` codes; `core` has no resolver) keeps today's behaviour.
    """
    entry = _resolvers.get(permission.partition(".")[0])
    return entry[1] if entry is not None else OwnerSubject.EMPLOYEE_LINK


def reset_scope_resolvers() -> None:
    """Empty the registry. Tests only: import order and test pollution must never decide
    which subject a context carries. No file under `services/*/app` may call this."""
    _resolvers.clear()


class ScopeContext(BaseModel):
    """The caller plus the scope resolved for the permission being exercised."""

    principal: Principal
    scope: Scope
    permission: str
    #: Whose id OWN and TEAM compare. Snapshotted by `require()` from the product's resolver
    #: registration; a hand-built context takes the default, the employee link, which is
    #: harmless for the ORG contexts a service builds for itself (ORG returns before any subject
    #: is read).
    owner_subject: OwnerSubject = OwnerSubject.EMPLOYEE_LINK

    model_config = {"arbitrary_types_allowed": True}

    @property
    def organization_id(self) -> uuid.UUID:
        return self.principal.organization_id

    @property
    def subject_ref(self) -> uuid.UUID | None:
        return self.principal.subject_ref

    @property
    def own_ref(self) -> uuid.UUID | None:
        """The id this context's OWN and TEAM rungs compare a row's owner with.

        The caller's membership id when the product declared `OwnerSubject.MEMBERSHIP`, the
        employee link otherwise; `None` when the caller has no such id, which matches no row.
        `subject_ref` keeps meaning the raw employee link, for HR code that really means it.
        """
        if self.owner_subject is OwnerSubject.MEMBERSHIP:
            return self.principal.membership_id
        return self.principal.subject_ref

    def apply[S: Select[Any]](
        self,
        stmt: S,
        *,
        owner_col: Any,
        team_col: Any | None = None,
        location_col: Any | None = None,
    ) -> S:
        """Inject the scope into a SELECT — the single enforcement point for lists.

        A list endpoint that skips this leaks the organization, which is why every one of
        them carries a negative-authorization test.

        OWN and TEAM compare `own_ref`: the caller's employee link, or their membership id for
        a product that declared `OwnerSubject.MEMBERSHIP`. On a membership subject, TEAM also
        admits the caller's own rows (D-24).
        """
        if self.scope is Scope.ORG:
            return stmt
        if self.scope is Scope.OWN:
            own = self.own_ref
            if own is None:
                # Nothing to own with. Match nothing — `col == NULL` would be SQL NULL
                # and quietly return everything the outer filter allowed.
                return stmt.where(sa_false())
            return stmt.where(owner_col == own)
        if self.scope is Scope.TEAM:
            if team_col is None:
                raise ForbiddenProblem("TEAM scope is not supported on this resource")
            own = self.own_ref
            if own is None:
                # Same guard as OWN, for the same reason: `team_col == None` renders as
                # `team_col IS NULL`, which would return every unmanaged row in the tenant
                # instead of nothing.
                return stmt.where(sa_false())
            if self.owner_subject is OwnerSubject.MEMBERSHIP:
                # A manager's own rows stay inside their team scope (D-24): `scope_for`
                # returns the widest grant only, so a TEAM holder has no OWN grant to fall back on.
                return stmt.where(or_(owner_col == own, team_col == own))
            return stmt.where(team_col == own)
        if self.scope is Scope.LOCATION:
            if location_col is None:
                raise ForbiddenProblem("LOCATION scope is not supported on this resource")
            return stmt.where(location_col.in_(self.principal.locations))
        raise ForbiddenProblem("Unknown scope")

    def assert_can_touch(
        self,
        *,
        owner_id: uuid.UUID | None,
        team_owner_id: uuid.UUID | None = None,
        location_code: str | None = None,
    ) -> None:
        """Object-level check for detail and mutate endpoints.

        LOCATION compares the row's location against the caller's. An earlier system
        returned early here, which meant a location-scoped caller could reach any single
        object it could name.

        OWN and TEAM compare `own_ref`, exactly as `apply` does; a caller with none passes
        neither (`None == None` must never admit a row). On a membership subject, TEAM also
        admits the caller's own row (D-24).
        """
        if self.scope is Scope.ORG:
            return
        own = self.own_ref
        if self.scope is Scope.OWN and own is not None and owner_id == own:
            return
        if self.scope is Scope.TEAM and own is not None and team_owner_id == own:
            return
        if (
            self.scope is Scope.TEAM
            and self.owner_subject is OwnerSubject.MEMBERSHIP
            and own is not None
            and owner_id == own
        ):
            return  # the caller's own row, inside their team scope (D-24)
        if self.scope is Scope.LOCATION:
            if location_code is not None and location_code in self.principal.locations:
                return
            raise ForbiddenProblem("Out of scope for this resource")
        raise ForbiddenProblem("Out of scope for this resource")


# --------------------------------------------------------------------- dependencies


def require(
    permission: str,
    *,
    minimum: Scope | None = None,
    amr: set[str] | None = None,
) -> Any:
    """Dependency factory guarding an endpoint.

    Usage::

        ctx: Annotated[ScopeContext, Depends(require("crm.pipeline.deal.write"))]

    `minimum` sets a scope floor for actions with no "own" reading. `amr` demands a
    step-up — e.g. ``{"otp"}`` on a payroll approval or a PII unmask.
    """

    def _dependency(principal: CurrentPrincipal) -> ScopeContext:
        scope = principal.scope_for(permission)
        if scope is None:
            raise ForbiddenProblem(f"Missing permission: {permission}")
        if minimum is not None and scope.rank < minimum.rank:
            raise ForbiddenProblem(
                f"{permission} requires {minimum.value} scope; this grant is {scope.value}"
            )
        if amr and not amr.issubset(set(principal.amr)):
            missing = ", ".join(sorted(amr - set(principal.amr)))
            raise ForbiddenProblem(f"Additional authentication required: {missing}")
        # Read here, per request and after every permission check above, so the subject is the
        # one registered by now: a route module builds this dependency at import, usually before
        # its service's own `scope.register()` line has run.
        return ScopeContext(
            principal=principal,
            scope=scope,
            permission=permission,
            owner_subject=owner_subject_for(permission),
        )

    return _dependency


def require_product(product: str) -> Any:
    """Refuse when the organization has not activated this product.

    Defence in depth: the gateway rejects these before proxying, but a routing-table
    mistake should not become an entitlement bypass. 402, not 403 — the caller's
    permissions are fine, the subscription is not, so the client offers an upgrade.
    """

    def _dependency(principal: CurrentPrincipal) -> Principal:
        if not principal.has_product(product):
            raise PaymentRequiredProblem(f"{product} is not active for this organization")
        return principal

    return _dependency


def require_operator(*, roles: set[str] | None = None) -> Any:
    """Dependency factory guarding a staff (operator) endpoint — the operator-side `require()`.

    Mirrors the tenant `require()` above, but the axis is different: an operator holds
    `operator_roles` on a `StaffAccount` (`owner` / `billing_ops` / `support`, ADR-0003
    addendum), minted verbatim into the token's `roles` claim on the `operator` audience. These
    are never tenant permission grants and never touch the `pv`/scope machinery.

    Three distinct refusals, and the status code is chosen deliberately for each:

    - **Wrong audience** (a tenant/product token, or none) → **404**, the same answer an unknown
      route gives. ADR-0003 §2: the existence of the operator surface is not something to confirm
      to a caller who is not staff, so this is checked and refused before anything else.
    - **Right audience, second factor not proven** → **403**. Staff MFA is mandatory (ADR-0003
      addendum): every operator token this platform mints now carries `otp` in `amr` once
      sign-in actually completes, so a token missing it is either a stale token minted before
      that rule existed, or one that reached a route it should never have (`operator_login`
      itself never issues a usable token — see `services/auth/app/services.py`). Checked before
      the role, because "not really signed in yet" is a more fundamental refusal than "signed in
      but lacking a role."
    - **Right audience, right factor, missing role** → **403**. The caller has already proven
      they are staff — the surface's existence is not in question — so there is nothing left to
      hide by pretending the route does not exist; a plain "you may not do this" is the honest
      answer.

    Every service that fronts a `staff_only` route (console, catalog, account, billing) uses
    this instead of hand-rolling its own audience check, so the 404-vs-403 split and the role
    grammar stay in one place rather than drifting per service.
    """

    def _dependency(principal: CurrentPrincipal) -> Principal:
        if principal.audience != OPERATOR_AUDIENCE:
            raise NotFoundProblem("Not found")
        if "otp" not in principal.amr:
            raise ForbiddenProblem("This operator session has not completed its second factor")
        if roles is not None and not roles.intersection(principal.roles):
            raise ForbiddenProblem(
                f"This action needs one of these operator roles: {', '.join(sorted(roles))}"
            )
        return principal

    return _dependency


def require_service_token(request: Request) -> None:
    """Guard for endpoints called by another service rather than by a person.

    The token is short-lived, signed, and addressed to *this* service by name. It replaced a
    static shared secret that was sent on every call and accepted by everything — see
    `platform_core.internal_auth` for what that change does and does not buy.

    The failure message stays deliberately vague. Distinguishing "expired" from "wrong
    audience" from "bad signature" tells whoever is probing which half of their guess was
    right.
    """
    settings: ServiceSettings = request.app.state.settings
    token = request.headers.get("X-Internal-Token")
    if not token:
        raise UnauthorizedProblem("Valid service token required")
    try:
        claims = verify_service_token(settings, token)
    except Exception as exc:  # PyJWTError and anything a malformed token provokes
        raise UnauthorizedProblem("Valid service token required") from exc

    # The verified claims were previously discarded, which left an endpoint with no way to say
    # *which* service called it. That matters where a service call is an audited act — opening
    # a customer's sealed phone number, say — because an audit row attributing the read to
    # "some service" is not an audit row. `iss` is the caller's service name and it is
    # trustworthy here precisely because the signature and audience have just been checked;
    # anything the caller merely asserts about itself in a header is not.
    request.state.service_claims = claims
    request.state.service_caller = str(claims.get("iss", "unknown-service"))


__all__ = [
    "OPERATOR_AUDIENCE",
    "CurrentPrincipal",
    "OwnerSubject",
    "Principal",
    "Scope",
    "ScopeContext",
    "ScopeResolver",
    "client_ip",
    "column_scope_resolver",
    "decode_token",
    "get_principal",
    "owner_subject_for",
    "register_scope_resolver",
    "require",
    "require_operator",
    "require_product",
    "require_service_token",
    "reset_jwks_cache",
    "reset_scope_resolvers",
]
