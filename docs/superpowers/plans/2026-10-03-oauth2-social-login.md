# OAuth2 Self-Serve Login (Google/Microsoft) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add "Continue with Google / Continue with Microsoft" as a self-serve login mechanism in `auth-service`, covering new-workspace creation, invitation acceptance, and returning sign-in — all through one flow.

**Architecture:** `auth-service` owns all OAuth2/OIDC mechanics (reusing `oidc.py`'s PKCE/state/`verify_id_token`) against two new platform-level provider configs. After verifying the ID token, it resolves the email through the existing `Directory.find_by_email`; if found, it auto-links `ExternalIdentity` (reusing the existing `_link_external_identity` helper) and signs in; if not found, it calls one of two **new internal write endpoints** on `account-service` (`signup-federated`, `invitations/accept-federated`) to create the directory row, then links and signs in. No cross-schema reads or writes; `account-service` is the only writer of its own schema.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, httpx, PyJWT, pytest + pytest-asyncio — all already in use in `services/auth` and `services/account`.

**Spec:** `docs/superpowers/specs/2026-10-03-oauth2-social-login-design.md`

## Global Constraints

- **No cross-schema foreign keys / no cross-service table reads** (CLAUDE.md). `auth-service` talks to `account-service` only through its internal HTTP API, never by reading `account.*` tables.
- **Every table carries `organization_id` and every query filters on it** — N/A for `platform_oauth_providers` (platform-wide, no organization), but every `account-service` write this plan adds still goes through the existing tenant-bound transaction machinery (`session_scope(organization_id)`, bypass-then-narrow) unchanged.
- **Only `auth-service` holds the signing key** (ADR-0000 §2). `account-service` never mints a token; it only ever returns `{organization_id, user_id}` for `auth-service` to mint from.
- **Money/PII rules N/A to this feature** except: OAuth client secrets are sealed with `platform_core.pii.seal`/`open_sealed`, same envelope as `OrgIdpConfig.client_secret_enc` — never stored or logged in plaintext.
- **Enqueue background work only after commit** — the existing `publish_event`/outbox pattern is reused unchanged; no new consumers needed.
- **Invitation email must match exactly** before `accept_invitation` runs for a federated accept (spec §4/§6) — this is a new authorization rule, not optional.
- **auth-service's new sessions must NOT be org-pinned** (`idp` prefix must be `oauth:`, never `oidc:`/`saml:`, so `_federated_organization_pin` leaves them unpinned) — spec §6, this is deliberate and must be regression-tested, not merely true by accident.
- **Commit messages must not mention Claude, Claude Code, or any AI assistant** (CLAUDE.md git conventions).

## Review Focus

- **A Google/Microsoft email whose casing or Unicode form differs from Postgres's `CITEXT` fold** (the Kelvin-sign/IDNA class of bug `sso_callback` already defends against) must not let the wrong account be matched — Task 8 ports the existing ASCII-only + exact-match hardening from `sso_callback`.
- **Replaying a consumed `state`** (double-submitting the callback, or an attacker replaying a captured redirect) must fail the same way SSO's replay does — covered by reusing `StateStore.consume`'s atomic `GETDEL`, verified in Task 3's regression test.
- **An invitation token accepted under a Google/Microsoft identity whose email differs from the invitation's** must be refused with the same opaque 404 every other invalid-invitation case gets, never a distinct "email mismatch" message that would let an attacker learn their forged token was real — Task 13.
- **A provider disabled or torn down between `start` and `callback`** (an admin flips `platform_oauth_providers.enabled` off mid-flow) must be refused the same way a torn-down `OrgIdpConfig` is in `sso_callback`, not crash on a stale lookup — Task 5's `oauth_social_start`/Task 8's callback both re-check `enabled` at the point they read the row.
- **A duplicate concurrent signup attempt** (two tabs completing the same new-workspace flow for the same email at once, or a retried request after a timeout) must not create two organizations for one person — Task 12 relies on `signup`'s existing `slug_taken`/`get_user_by_email` 409s, and Task 8's retry policy (re-check `find_by_email` before retrying a write) is itself tested.

---

## File Structure

**`services/auth/`**
- `app/models.py` — add `PlatformOAuthProvider` (modify)
- `migrations/versions/a1f20cb97e34_platform_oauth_providers.py` — create table (new)
- `app/config.py` — add OAuth env settings (modify)
- `app/oidc.py` — generalize `StateStore.consume` to take a parser; add `SocialAuthorizationRequest` (modify)
- `app/provisioning.py` — `ProvisioningClient` protocol, fake, HTTP client for account-service writes (new)
- `app/deps.py` — `ProvisioningDep` (modify)
- `app/main.py` — wire `app.state.provisioning` (modify)
- `app/schemas.py` — `OAuthStartOut` (modify)
- `app/services.py` — `oauth_social_start`, `_resolve_or_provision_identity`, `oauth_social_callback`, two new `EVT_`/constant lines (modify)
- `app/api/routes.py` — `GET /auth/oauth/{provider}/start`, `GET /auth/oauth/{provider}/callback` (modify)
- `bin/seed-oauth-providers` — one-time bootstrap script (new)
- `tests/fake_idp.py` — extend with a Google/Microsoft-shaped fake (modify)
- `tests/test_oauth_social.py` — all new flow tests (new)

**`services/account/`**
- `app/schemas.py` — `SignupFederatedIn`, `SignupFederatedOut`, `InvitationAcceptFederatedIn` (modify)
- `app/services.py` — `accept_invitation` gains `expected_email: str | None = None` (modify)
- `app/api/internal.py` — `POST /internal/v1/signup-federated`, `POST /internal/v1/invitations/accept-federated` (modify)
- `tests/test_internal.py` — tests for both new endpoints (modify)

---

### Task 1: `PlatformOAuthProvider` model and migration

**Files:**
- Modify: `services/auth/app/models.py`
- Create: `services/auth/migrations/versions/a1f20cb97e34_platform_oauth_providers.py`
- Test: `services/auth/tests/test_tenancy.py` (confirm the new table is NOT under RLS, alongside the existing `SigningKey` assertion if one exists; otherwise add a standalone assertion)

**Interfaces:**
- Produces: `PlatformOAuthProvider` ORM class with columns `provider` (PK, str), `enabled` (bool), `client_id` (str|None), `client_secret_enc` (bytes|None), `issuer`/`authorization_endpoint`/`token_endpoint`/`jwks_uri` (str|None).

- [ ] **Step 1: Add the model**

In `services/auth/app/models.py`, add after `OrgIdpConfig` (before `SigningKey`):

```python
class PlatformOAuthProvider(Base):
    """One platform-wide OAuth2 provider — Google or Microsoft.

    Unlike `OrgIdpConfig`, this has no `organization_id` and is **not** under row-level
    security: it is the platform's own registered OAuth app, the same credential for every
    organization, not something an org admin configures. See
    docs/superpowers/specs/2026-10-03-oauth2-social-login-design.md §2.1.

    `client_secret_enc` uses the same `platform_core.pii.seal` envelope as
    `OrgIdpConfig.client_secret_enc`. Seeded by `bin/seed-oauth-providers` from deployment
    env vars, never accepted through any API.
    """

    __tablename__ = "platform_oauth_providers"

    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    client_id: Mapped[str | None] = mapped_column(String(255))
    client_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)

    issuer: Mapped[str | None] = mapped_column(String(255))
    authorization_endpoint: Mapped[str | None] = mapped_column(String(500))
    token_endpoint: Mapped[str | None] = mapped_column(String(500))
    jwks_uri: Mapped[str | None] = mapped_column(String(500))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 2: Write the migration**

```python
"""platform_oauth_providers — platform-wide Google/Microsoft OAuth config

Revision ID: a1f20cb97e34
Revises: 9c3e7a1d5b28
Create Date: 2026-10-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f20cb97e34"  # pragma: allowlist secret
down_revision: str | None = "9c3e7a1d5b28"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"


def upgrade() -> None:
    op.create_table(
        "platform_oauth_providers",
        sa.Column("provider", sa.String(length=20), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("client_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("issuer", sa.String(length=255), nullable=True),
        sa.Column("authorization_endpoint", sa.String(length=500), nullable=True),
        sa.Column("token_endpoint", sa.String(length=500), nullable=True),
        sa.Column("jwks_uri", sa.String(length=500), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    # Deliberately NOT enabling row-level security here — this table is platform-wide,
    # the same category as `signing_keys`, which also carries no RLS policy.


def downgrade() -> None:
    op.drop_table("platform_oauth_providers", schema=SCHEMA)
```

- [ ] **Step 3: Run the migration against the dev database**

Run: `cd services/auth && alembic upgrade head`
Expected: migration applies with no error; `\d auth.platform_oauth_providers` in `psql` shows the table with no RLS policy attached.

- [ ] **Step 4: Write the RLS-exclusion regression test**

In `services/auth/tests/test_tenancy.py`, add:

```python
async def test_platform_oauth_providers_not_under_rls(db_session):
    """Platform-wide config, not a tenant table — see models.py docstring."""
    result = await db_session.execute(
        text(
            "SELECT relrowsecurity FROM pg_class "
            "WHERE relname = 'platform_oauth_providers' "
            "AND relnamespace = 'auth'::regnamespace"
        )
    )
    assert result.scalar_one() is False
```

(Match whatever fixture name `test_tenancy.py` already uses for a raw session — read the top of that file first; `db_session` here is illustrative.)

- [ ] **Step 5: Run the test**

Run: `cd services/auth && pytest tests/test_tenancy.py -k platform_oauth_providers -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/auth/app/models.py services/auth/migrations/versions/a1f20cb97e34_platform_oauth_providers.py services/auth/tests/test_tenancy.py
git commit -m "auth: add platform_oauth_providers table"
```

---

### Task 2: OAuth client settings

**Files:**
- Modify: `services/auth/app/config.py`

**Interfaces:**
- Produces: `Settings.google_oauth_client_id: str | None`, `Settings.google_oauth_client_secret: SecretStr | None`, `Settings.microsoft_oauth_client_id: str | None`, `Settings.microsoft_oauth_client_secret: SecretStr | None`, `Settings.oauth_social_redirect_uri_template: str`.

- [ ] **Step 1: Add the settings**

In `services/auth/app/config.py`, after the `# --- single sign-on` block, add:

```python
    # --- platform OAuth (Google/Microsoft self-serve login) ------------------------
    #: Deployment secrets, not admin-configured rows — same posture as Razorpay keys
    #: (ADR-0013 §6). Unset means the provider is inert; `bin/seed-oauth-providers`
    #: reads these into `platform_oauth_providers` at deploy time.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    microsoft_oauth_client_id: str | None = None
    microsoft_oauth_client_secret: SecretStr | None = None
    #: `{provider}` is substituted with `google`/`microsoft`. Registered with each
    #: provider's console verbatim — see `sso_redirect_uri`'s docstring for why a
    #: redirect target can only ever come from configuration.
    oauth_social_redirect_uri_template: str = (
        "http://localhost:8100/api/v1/auth/oauth/{provider}/callback"
    )
```

Add `from pydantic import SecretStr` to the existing `from pydantic import Field` import line if `SecretStr` is not already imported (check the file first — other settings like `admin_gateway_hop_key` already use `SecretStr`, so the import likely already exists).

- [ ] **Step 2: Verify the service still boots with defaults**

Run: `cd services/auth && python -c "from app.config import settings; print(settings().google_oauth_client_id)"`
Expected: prints `None`, no error.

- [ ] **Step 3: Commit**

```bash
git add services/auth/app/config.py
git commit -m "auth: add platform OAuth client settings"
```

---

### Task 3: Generalize `StateStore` for a second pending-request shape

**Files:**
- Modify: `services/auth/app/oidc.py`
- Modify: `services/auth/app/services.py` (update the one existing call site)
- Test: `services/auth/tests/test_sso.py` (existing SSO state tests must still pass unchanged)

**Interfaces:**
- Consumes: nothing new.
- Produces: `StateStore.consume(state: str, parse: Callable[[str], T]) -> T` (was hardcoded to `AuthorizationRequest.from_json`). `StateStore.issue` is unchanged (it already only calls `.as_json()`, which any pending-request dataclass can provide).

- [ ] **Step 1: Write the failing test (existing behavior preserved via explicit parser)**

In `services/auth/tests/test_sso.py`, find the existing state round-trip test (e.g. `test_state_store_issue_and_consume` or similar — read the file to get the exact fixture names) and add, right after it:

```python
async def test_state_store_consume_takes_explicit_parser(redis_client):
    store = StateStore(redis_client)
    pending = AuthorizationRequest(
        organization_id="org-1",
        config_id="cfg-1",
        nonce="nonce-1",
        code_verifier="verifier-1",
        domain="example.com",
        created_at=now_iso(),
    )
    state = await store.issue(pending, ttl_seconds=60)
    result = await store.consume(state, AuthorizationRequest.from_json)
    assert result == pending
```

(Use whatever Redis fixture `test_sso.py` already uses — do not invent a new one.)

- [ ] **Step 2: Run it to confirm it fails on the current signature**

Run: `cd services/auth && pytest tests/test_sso.py -k consume_takes_explicit_parser -v`
Expected: FAIL — `TypeError: consume() takes 2 positional arguments but 3 were given` (or similar).

- [ ] **Step 3: Generalize `StateStore.consume`**

In `services/auth/app/oidc.py`, change:

```python
    async def consume(self, state: str) -> AuthorizationRequest:
```

to:

```python
    async def consume(self, state: str, parse: Callable[[str], T] = AuthorizationRequest.from_json) -> T:
```

Add `from typing import Callable, TypeVar` (or add to the existing `from typing import Any, Protocol` line) and `T = TypeVar("T")` near the top of the file. Update the method body's return statement from:

```python
            return AuthorizationRequest.from_json(raw if isinstance(raw, str) else raw.decode("utf-8"))
```

to:

```python
            return parse(raw if isinstance(raw, str) else raw.decode("utf-8"))
```

The default parameter keeps every existing call site (`sso_callback` in `services.py`) working with zero changes — this is a pure widening, not a breaking change.

- [ ] **Step 4: Run the new test and the full existing SSO suite**

Run: `cd services/auth && pytest tests/test_sso.py -v`
Expected: all PASS, including the new test and every pre-existing one (`StateStore.consume(state)` call sites with no second argument still work via the default).

- [ ] **Step 5: Commit**

```bash
git add services/auth/app/oidc.py services/auth/tests/test_sso.py
git commit -m "auth: generalize StateStore.consume to accept a parser"
```

---

### Task 4: `ProvisioningClient` — auth-service's write path into account-service

**Files:**
- Create: `services/auth/app/provisioning.py`
- Modify: `services/auth/app/deps.py`
- Modify: `services/auth/app/main.py`
- Test: `services/auth/tests/test_provisioning.py` (new)

**Interfaces:**
- Produces:
  - `ProvisioningClient` Protocol: `async def signup_federated(self, *, organization_name: str, slug: str, full_name: str, email: str) -> ProvisionedAccount`, `async def accept_invitation_federated(self, *, token: str, email: str) -> ProvisionedAccount`.
  - `ProvisionedAccount` frozen dataclass: `organization_id: uuid.UUID`, `user_id: uuid.UUID`.
  - Raised: `ConflictProblem` (duplicate email/slug, or invitation email mismatch — same `platform_core.errors.ConflictProblem` already used elsewhere), `NotFoundProblem` (invalid/expired invitation), `UpstreamUnavailableProblem` (account-service unreachable).
  - `HttpProvisioningClient(base_url: str, settings: ServiceSettings)` — real implementation, same shape as `HttpDirectory`.
  - `FakeProvisioningClient` — in-memory test double.
- Consumes: `platform_core.internal_auth.mint_service_token`, `httpx`.

- [ ] **Step 1: Write the module**

```python
"""auth-service's write path into account-service's directory.

Unlike `directory.py` (read-only — "what authentication needs to know about people"), this
is the one place auth-service **writes** into account-service's schema, and it does so
exclusively through account-service's own internal endpoints — never a direct table write,
never a cross-schema FK. Used only by the OAuth social-login flow (`services.py`'s
`_resolve_or_provision_identity`), when a verified email has no existing directory row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx

from platform_core.config import ServiceSettings
from platform_core.errors import ConflictProblem, NotFoundProblem, UpstreamUnavailableProblem
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("auth.provisioning")


@dataclass(frozen=True, slots=True)
class ProvisionedAccount:
    organization_id: uuid.UUID
    user_id: uuid.UUID


class ProvisioningClient(Protocol):
    async def signup_federated(
        self, *, organization_name: str, slug: str, full_name: str, email: str
    ) -> ProvisionedAccount: ...

    async def accept_invitation_federated(
        self, *, token: str, email: str
    ) -> ProvisionedAccount: ...


class HttpProvisioningClient:
    """The real client. Talks to account-service's internal API, same posture as `HttpDirectory`."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 8.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        self._settings = settings

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Internal-Token": mint_service_token(self._settings, audience="account"),
            "X-Calling-Service": "auth",
        }
        correlation = correlation_id_ctx.get()
        if correlation:
            headers["X-Correlation-ID"] = correlation
        return headers

    async def _post(self, path: str, body: dict[str, str]) -> dict[str, str]:
        try:
            response = await self._client.post(path, headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableProblem("The directory is unavailable") from exc
        if response.status_code == 409:
            raise ConflictProblem(response.json().get("detail", "That account already exists"))
        if response.status_code == 404:
            raise NotFoundProblem(response.json().get("detail", "That invitation is not valid"))
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def signup_federated(
        self, *, organization_name: str, slug: str, full_name: str, email: str
    ) -> ProvisionedAccount:
        body = await self._post(
            "/internal/v1/signup-federated",
            {
                "organization_name": organization_name,
                "slug": slug,
                "full_name": full_name,
                "email": email,
            },
        )
        return ProvisionedAccount(
            organization_id=uuid.UUID(body["organization_id"]), user_id=uuid.UUID(body["user_id"])
        )

    async def accept_invitation_federated(self, *, token: str, email: str) -> ProvisionedAccount:
        body = await self._post(
            "/internal/v1/invitations/accept-federated", {"token": token, "email": email}
        )
        return ProvisionedAccount(
            organization_id=uuid.UUID(body["organization_id"]), user_id=uuid.UUID(body["user_id"])
        )


class FakeProvisioningClient:
    """In-memory test double. Tests seed `.orgs`/`.invitations` and assert on `.calls`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self._next_org_id = uuid.uuid4()
        self._next_user_id = uuid.uuid4()
        self.existing_emails: set[str] = set()
        self.existing_slugs: set[str] = set()
        #: token -> (email, organization_id, user_id, revoked_or_expired: bool)
        self.invitations: dict[str, tuple[str, uuid.UUID, uuid.UUID, bool]] = {}

    async def signup_federated(
        self, *, organization_name: str, slug: str, full_name: str, email: str
    ) -> ProvisionedAccount:
        self.calls.append(("signup_federated", {"email": email, "slug": slug}))
        if slug in self.existing_slugs:
            raise ConflictProblem(f"The address {slug!r} is already taken")
        if email in self.existing_emails:
            raise ConflictProblem("That email already has an account. Sign in instead.")
        self.existing_emails.add(email)
        self.existing_slugs.add(slug)
        return ProvisionedAccount(organization_id=self._next_org_id, user_id=self._next_user_id)

    async def accept_invitation_federated(self, *, token: str, email: str) -> ProvisionedAccount:
        self.calls.append(("accept_invitation_federated", {"token": token, "email": email}))
        entry = self.invitations.get(token)
        if entry is None or entry[3]:
            raise NotFoundProblem("That invitation is not valid")
        invited_email, org_id, user_id, _ = entry
        if invited_email.lower() != email.lower():
            raise NotFoundProblem("That invitation is not valid")
        return ProvisionedAccount(organization_id=org_id, user_id=user_id)
```

- [ ] **Step 2: Wire the dependency**

In `services/auth/app/deps.py`, add:

```python
from app.provisioning import ProvisioningClient
```

```python
def get_provisioning(request: Request) -> ProvisioningClient:
    return request.app.state.provisioning  # type: ignore[no-any-return]
```

```python
ProvisioningDep = Annotated[ProvisioningClient, Depends(get_provisioning)]
```

- [ ] **Step 3: Wire it at startup**

In `services/auth/app/main.py`, add the import and startup/shutdown lines:

```python
from app.provisioning import HttpProvisioningClient
```

```python
    application.state.provisioning = HttpProvisioningClient(cfg.account_service_url, cfg)
```

(in `_startup`, alongside `application.state.directory = ...`)

```python
    provisioning = getattr(application.state, "provisioning", None)
    if isinstance(provisioning, HttpProvisioningClient):
        await provisioning.aclose()
```

(in `_shutdown`, alongside the `directory`/`entitlements`/`idp` close blocks)

- [ ] **Step 4: Write the failing test**

```python
# services/auth/tests/test_provisioning.py
import uuid

import pytest

from app.provisioning import FakeProvisioningClient
from platform_core.errors import ConflictProblem, NotFoundProblem


async def test_signup_federated_happy_path():
    client = FakeProvisioningClient()
    result = await client.signup_federated(
        organization_name="Acme", slug="acme", full_name="Ada", email="ada@acme.com"
    )
    assert isinstance(result.organization_id, uuid.UUID)
    assert isinstance(result.user_id, uuid.UUID)


async def test_signup_federated_duplicate_email_conflicts():
    client = FakeProvisioningClient()
    await client.signup_federated(
        organization_name="Acme", slug="acme", full_name="Ada", email="ada@acme.com"
    )
    with pytest.raises(ConflictProblem):
        await client.signup_federated(
            organization_name="Acme Two", slug="acme-two", full_name="Ada", email="ada@acme.com"
        )


async def test_accept_invitation_federated_email_mismatch_is_not_found():
    client = FakeProvisioningClient()
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    client.invitations["tok-1"] = ("invitee@acme.com", org_id, user_id, False)
    with pytest.raises(NotFoundProblem):
        await client.accept_invitation_federated(token="tok-1", email="attacker@evil.com")
```

- [ ] **Step 5: Run it to confirm it fails**

Run: `cd services/auth && pytest tests/test_provisioning.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.provisioning'` (confirms the test runs before Step 1 is in place; if running after Step 1, skip to Step 6).

- [ ] **Step 6: Run it again now that the module exists**

Run: `cd services/auth && pytest tests/test_provisioning.py -v`
Expected: PASS (all three tests)

- [ ] **Step 7: Commit**

```bash
git add services/auth/app/provisioning.py services/auth/app/deps.py services/auth/app/main.py services/auth/tests/test_provisioning.py
git commit -m "auth: add ProvisioningClient for account-service writes"
```

---

### Task 5: `oauth_social_start` and `GET /auth/oauth/{provider}/start`

**Files:**
- Modify: `services/auth/app/oidc.py`
- Modify: `services/auth/app/schemas.py`
- Modify: `services/auth/app/services.py`
- Modify: `services/auth/app/api/routes.py`
- Test: `services/auth/tests/test_oauth_social.py` (new file)

**Interfaces:**
- Consumes: `PlatformOAuthProvider` (Task 1), `oidc.new_verifier`/`challenge_for`/`authorization_url` (existing), `StateStore` (Task 3's generalized `consume`, `issue` unchanged).
- Produces:
  - `oidc.SocialAuthorizationRequest` dataclass — added to `app/oidc.py` itself, alongside `AuthorizationRequest` (same file: it already imports `json`, which this class's `.as_json()`/`.from_json()` need, and it's the natural home for a second "pending request" shape): `provider: str`, `intent: str` (`"signup"` or `"invite"`), `invitation_token: str | None`, `nonce: str`, `code_verifier: str`, `created_at: str`.
  - `services.oauth_social_start(session, *, cfg, store, provider, intent, invitation_token=None) -> OAuthRedirect` (`OAuthRedirect` is a small new dataclass, defined in `services.py` alongside `LoginOutcome` — business-flow shapes stay in `services.py`, the way `OIDC`/`SSO_AMR` already do: `authorization_url: str`, `state: str`, `expires_in: int`).
  - `OAuthStartOut` schema (mirrors `SsoStartOut` minus the org/provider-name fields).
  - Route: `GET /auth/oauth/{provider}/start?intent=signup|invite&invitation_token=...`.

- [ ] **Step 1: Write the failing test**

```python
# services/auth/tests/test_oauth_social.py
import pytest

from app import oidc, services
from app.models import PlatformOAuthProvider
from platform_core.errors import NotFoundProblem


async def _seed_google_provider(db_session):
    db_session.add(
        PlatformOAuthProvider(
            provider="google",
            enabled=True,
            client_id="client-123",
            issuer="https://accounts.google.com",
            authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        )
    )
    await db_session.flush()


async def test_oauth_social_start_returns_authorization_url(db_session, redis_client):
    await _seed_google_provider(db_session)
    store = oidc.StateStore(redis_client)
    redirect = await services.oauth_social_start(
        db_session, cfg=_test_settings(), store=store, provider="google", intent="signup"
    )
    assert redirect.authorization_url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client-123" in redirect.authorization_url
    assert redirect.state


async def test_oauth_social_start_refuses_disabled_provider(db_session, redis_client):
    db_session.add(PlatformOAuthProvider(provider="microsoft", enabled=False))
    await db_session.flush()
    store = oidc.StateStore(redis_client)
    with pytest.raises(NotFoundProblem):
        await services.oauth_social_start(
            db_session, cfg=_test_settings(), store=store, provider="microsoft", intent="signup"
        )
```

(Use whatever `db_session`/`redis_client` fixtures and settings-builder helper — e.g. `_test_settings()` — `test_sso.py` already defines; do not invent new ones. Read `tests/conftest.py` and `tests/test_sso.py`'s top for the exact names.)

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd services/auth && pytest tests/test_oauth_social.py -v`
Expected: FAIL — `AttributeError: module 'app.services' has no attribute 'oauth_social_start'`

- [ ] **Step 3: Add the schema**

In `services/auth/app/schemas.py`, near `SsoStartOut`:

```python
class OAuthStartOut(BaseModel):
    authorization_url: str
    state: str
    expires_in: int
```

- [ ] **Step 4: Add `SocialAuthorizationRequest` to `oidc.py`**

In `services/auth/app/oidc.py`, right after the `AuthorizationRequest` class (before `class StateStore:`), add:

```python
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
```

Add `"SocialAuthorizationRequest"` to `oidc.py`'s `__all__` list, alphabetically alongside `"AuthorizationRequest"`.

- [ ] **Step 5: Implement `oauth_social_start`**

In `services/auth/app/services.py`, near the SSO section (after the `OIDC = "oidc"` / `SSO_AMR = "sso"` constants), add:

```python
OAUTH_PROVIDERS = ("google", "microsoft")
OAUTH_AMR = "oauth"
EVT_OAUTH_SIGNED_IN = "core.session.oauth_signed_in"


@dataclass(frozen=True, slots=True)
class OAuthRedirect:
    authorization_url: str
    state: str
    expires_in: int


async def _get_enabled_provider(session: AsyncSession, provider: str) -> PlatformOAuthProvider:
    if provider not in OAUTH_PROVIDERS:
        raise NotFoundProblem("That sign-in method is not available")
    row = await session.get(PlatformOAuthProvider, provider)
    if row is None or not row.enabled:
        raise NotFoundProblem("That sign-in method is not available")
    return row


async def oauth_social_start(
    session: AsyncSession,
    *,
    cfg: Settings,
    store: oidc.StateStore,
    provider: str,
    intent: str,
    invitation_token: str | None = None,
) -> OAuthRedirect:
    if intent not in ("signup", "invite"):
        raise ValidationProblem("intent must be 'signup' or 'invite'")
    if intent == "invite" and not invitation_token:
        raise ValidationProblem("invitation_token is required for intent=invite")

    config = await _get_enabled_provider(session, provider)

    verifier = oidc.new_verifier()
    pending = oidc.SocialAuthorizationRequest(
        provider=provider,
        intent=intent,
        invitation_token=invitation_token,
        nonce=secrets.token_urlsafe(24),
        code_verifier=verifier,
        created_at=oidc.now_iso(),
    )
    state = await store.issue(pending, ttl_seconds=cfg.sso_state_ttl_seconds)

    url = oidc.authorization_url(
        authorization_endpoint=str(config.authorization_endpoint),
        client_id=str(config.client_id),
        redirect_uri=cfg.oauth_social_redirect_uri_template.format(provider=provider),
        scopes=list(oidc.DEFAULT_SCOPES),
        state=state,
        nonce=pending.nonce,
        code_challenge=oidc.challenge_for(verifier),
    )
    return OAuthRedirect(authorization_url=url, state=state, expires_in=cfg.sso_state_ttl_seconds)
```

Add `ValidationProblem` to the existing `from platform_core.errors import (...)` block in `services.py` if it is not already imported there (check first).

- [ ] **Step 6: Run the test**

Run: `cd services/auth && pytest tests/test_oauth_social.py -v`
Expected: PASS (both tests)

- [ ] **Step 7: Wire the route**

In `services/auth/app/api/routes.py`, add `OAuthStartOut` to the `from app.schemas import (...)` block, add `ProvisioningDep` to the `from app.deps import (...)` block, and add (near `sso_start`):

```python
@router.get(
    "/auth/oauth/{provider}/start",
    response_model=OAuthStartOut,
    summary="Begin self-serve sign-in with Google or Microsoft",
)
async def oauth_social_start_route(
    provider: str,
    session: SessionDep,
    store: SsoStateDep,
    intent: Annotated[str, Query(pattern="^(signup|invite)$")],
    invitation_token: Annotated[str | None, Query(max_length=255)] = None,
) -> OAuthStartOut:
    redirect = await services.oauth_social_start(
        session,
        cfg=settings(),
        store=store,
        provider=provider,
        intent=intent,
        invitation_token=invitation_token,
    )
    return OAuthStartOut(
        authorization_url=redirect.authorization_url,
        state=redirect.state,
        expires_in=redirect.expires_in,
    )
```

- [ ] **Step 8: Add a route-level test and run the full file**

```python
async def test_oauth_social_start_route(client, db_session):
    await _seed_google_provider(db_session)
    response = await client.get("/api/v1/auth/oauth/google/start", params={"intent": "signup"})
    assert response.status_code == 200
    assert "authorization_url" in response.json()
```

Run: `cd services/auth && pytest tests/test_oauth_social.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add services/auth/app/oidc.py services/auth/app/schemas.py services/auth/app/services.py services/auth/app/api/routes.py services/auth/tests/test_oauth_social.py
git commit -m "auth: add oauth_social_start and the /auth/oauth/{provider}/start route"
```

---

### Task 6: `_resolve_or_provision_identity` — the signup branch

**Files:**
- Modify: `services/auth/app/services.py`
- Test: `services/auth/tests/test_oauth_social.py`

**Interfaces:**
- Consumes: `Directory.find_by_email` (existing), `ProvisioningClient.signup_federated` (Task 4).
- Produces: `async def _resolve_or_provision_identity(*, directory, provisioning, intent, invitation_token, identity: oidc.VerifiedIdentity, full_name_hint: str, organization_name: str | None, slug: str | None) -> DirectoryUser` — no `session` parameter: this function only orchestrates `directory`/`provisioning` calls, never touches the DB directly (the one DB write in this area, `ExternalIdentity`, stays in `oauth_social_callback` via the existing `_link_external_identity`, which does take `session`). Raises `ConflictProblem`/`NotFoundProblem` as appropriate; the returning-user and invite branches are filled in by Tasks 7–8, so this task implements the `intent == "signup", user not found"` path and a placeholder-free stub for the others that Task 7/8 replace (not a TODO — a real, narrower function each task edits in place).

- [ ] **Step 1: Write the failing test**

```python
async def test_resolve_or_provision_creates_new_workspace(fake_directory, fake_provisioning):
    identity = oidc.VerifiedIdentity(subject="google-sub-1", email="new@example.com")
    user = await services._resolve_or_provision_identity(
        directory=fake_directory,
        provisioning=fake_provisioning,
        intent="signup",
        invitation_token=None,
        identity=identity,
        full_name_hint="New Person",
        organization_name="New Co",
        slug="new-co",
    )
    assert user.email == "new@example.com"
    assert (
        "signup_federated",
        {"email": "new@example.com", "slug": "new-co"},
    ) in fake_provisioning.calls
```

(`fake_directory`/`fake_provisioning` are simple fixtures wrapping `FakeProvisioningClient` and a minimal fake `Directory` — add them to `tests/conftest.py` if `test_sso.py`'s existing `fake_idp`/directory fixtures don't already cover this shape; otherwise reuse those.)

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd services/auth && pytest tests/test_oauth_social.py -k resolve_or_provision_creates -v`
Expected: FAIL — function does not exist.

- [ ] **Step 3: Implement it**

In `services/auth/app/services.py`, near `_link_external_identity`:

```python
async def _resolve_or_provision_identity(
    *,
    directory: Directory,
    provisioning: ProvisioningClient,
    intent: str,
    invitation_token: str | None,
    identity: oidc.VerifiedIdentity,
    full_name_hint: str,
    organization_name: str | None = None,
    slug: str | None = None,
) -> DirectoryUser:
    """Find the account a verified OAuth identity belongs to, or create/join one.

    Always resolves by email first — the same shortcut enterprise SSO has no need of
    (it already knows the organization from the domain) but a platform-wide flow does: an
    email that already has an account never gets a second organization (ADR-0013 §3), it
    falls through to the returning-user path instead. See spec §3.
    """
    existing = await directory.find_by_email(identity.email)
    if existing is not None:
        return existing

    if intent == "signup":
        if not organization_name or not slug:
            raise ValidationProblem("organization_name and slug are required to create a workspace")
        provisioned = await provisioning.signup_federated(
            organization_name=organization_name,
            slug=slug,
            full_name=full_name_hint,
            email=identity.email,
        )
    else:
        assert invitation_token is not None  # enforced in oauth_social_start
        provisioned = await provisioning.accept_invitation_federated(
            token=invitation_token, email=identity.email
        )

    created = await directory.get(provisioned.user_id)
    if created is None:  # pragma: no cover - defensive; account-service just created this row
        raise UpstreamUnavailableProblem("The directory is unavailable")
    return created
```

- [ ] **Step 4: Run the test**

Run: `cd services/auth && pytest tests/test_oauth_social.py -k resolve_or_provision_creates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/auth/app/services.py services/auth/tests/test_oauth_social.py
git commit -m "auth: add _resolve_or_provision_identity signup branch"
```

---

### Task 7: `_resolve_or_provision_identity` — the invite branch and email-match test

**Files:**
- Modify: `services/auth/tests/test_oauth_social.py` (no further production code changes — Task 6's `else` branch already handles this; this task is purely to pin the behavior with its own adversarial test, per the plan's Review Focus)

**Interfaces:**
- Consumes: Task 6's `_resolve_or_provision_identity`, `FakeProvisioningClient.accept_invitation_federated` (Task 4).

- [ ] **Step 1: Write the adversarial test first**

```python
async def test_resolve_or_provision_invite_rejects_email_mismatch(
    fake_directory, fake_provisioning
):
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_provisioning.invitations["tok-1"] = ("invitee@acme.com", org_id, user_id, False)
    identity = oidc.VerifiedIdentity(subject="google-sub-2", email="attacker@evil.com")
    with pytest.raises(NotFoundProblem):
        await services._resolve_or_provision_identity(
            directory=fake_directory,
            provisioning=fake_provisioning,
            intent="invite",
            invitation_token="tok-1",
            identity=identity,
            full_name_hint="Attacker",
            organization_name=None,
            slug=None,
        )


async def test_resolve_or_provision_invite_happy_path(fake_directory, fake_provisioning):
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_provisioning.invitations["tok-2"] = ("invitee@acme.com", org_id, user_id, False)
    fake_directory.seed_user(user_id, email="invitee@acme.com", organization_id=org_id)
    identity = oidc.VerifiedIdentity(subject="google-sub-3", email="invitee@acme.com")
    user = await services._resolve_or_provision_identity(
        directory=fake_directory,
        provisioning=fake_provisioning,
        intent="invite",
        invitation_token="tok-2",
        identity=identity,
        full_name_hint="Invitee",
        organization_name=None,
        slug=None,
    )
    assert user.user_id == user_id
```

(`fake_directory.seed_user(...)` — add this helper method to whatever fake `Directory` test double already exists, or to a new minimal one in `conftest.py`, so `directory.get(provisioned.user_id)` in Task 6's implementation resolves.)

- [ ] **Step 2: Run both**

Run: `cd services/auth && pytest tests/test_oauth_social.py -k resolve_or_provision_invite -v`
Expected: PASS — both tests pass against Task 6's existing `else` branch with no further production code change.

- [ ] **Step 3: Commit**

```bash
git add services/auth/tests/test_oauth_social.py
git commit -m "auth: pin invitation-email-mismatch behavior with a dedicated test"
```

---

### Task 8: `oauth_social_callback` — verify, link, mint

**Files:**
- Modify: `services/auth/app/services.py`
- Modify: `services/auth/app/api/routes.py`
- Modify: `services/auth/app/schemas.py` (add `provider` field handling if needed — likely none, `LoginResult` is reused as-is)
- Test: `services/auth/tests/test_oauth_social.py`

**Interfaces:**
- Consumes: `oidc.verify_id_token`, `pii.open_sealed`, `_link_external_identity` (existing), `_resolve_or_provision_identity` (Task 6/7), `resolve_organization`/`start_session`/`issue_for` (existing, from `login`), `LoginOutcome` (existing).
- Produces: `async def oauth_social_callback(session, *, cfg, pair, directory, provisioning, entitlements, idp, store, provider, code, state, request=None, audience="app") -> LoginOutcome`.

- [ ] **Step 1: Write the failing happy-path test**

```python
async def test_oauth_social_callback_signs_in_existing_user(
    db_session, redis_client, fake_idp_google, fake_directory_with_user
):
    store = oidc.StateStore(redis_client)
    pending = oidc.SocialAuthorizationRequest(
        provider="google",
        intent="signup",
        invitation_token=None,
        nonce="n1",
        code_verifier=oidc.new_verifier(),
        created_at=oidc.now_iso(),
    )
    state = await store.issue(pending, ttl_seconds=600)
    await _seed_google_provider(db_session)

    outcome = await services.oauth_social_callback(
        db_session,
        cfg=_test_settings(),
        pair=_test_keypair(),
        directory=fake_directory_with_user,
        provisioning=FakeProvisioningClient(),
        entitlements=_fake_entitlements(),
        idp=fake_idp_google,
        store=store,
        provider="google",
        code="irrelevant-fake-code",
        state=state,
    )
    assert outcome.status == "complete"
    assert outcome.issued is not None
```

(`fake_idp_google`, `fake_directory_with_user`, `_test_keypair`, `_fake_entitlements` — build on the exact fixtures `test_sso.py` already provides for its own `sso_callback` tests; read that file's fixtures before writing these, and extend `tests/fake_idp.py` per this task's Step 4 rather than inventing a parallel fake.)

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd services/auth && pytest tests/test_oauth_social.py -k callback_signs_in_existing -v`
Expected: FAIL — function does not exist.

- [ ] **Step 3: Implement `oauth_social_callback`**

In `services/auth/app/services.py`, near `oauth_social_start`:

```python
async def oauth_social_callback(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    directory: Directory,
    provisioning: ProvisioningClient,
    entitlements: Entitlements,
    idp: oidc.IdpClient,
    store: oidc.StateStore,
    provider: str,
    code: str,
    state: str,
    request: Request | None = None,
    audience: str = "app",
) -> LoginOutcome:
    """Finish self-serve Google/Microsoft sign-in: verify, resolve-or-provision, link, mint.

    No domain binding and no organization pin — unlike enterprise SSO, this proves a
    *person*, not "this org's own domain" (spec §6). `idp` is therefore `oauth:<provider>`,
    never `oidc:`/`saml:`, so `_federated_organization_pin` leaves the session unpinned.
    """
    pending = await store.consume(state, oidc.SocialAuthorizationRequest.from_json)
    if pending.provider != provider:
        raise oidc.SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    config = await _get_enabled_provider(session, provider)
    try:
        client_secret = pii.open_sealed(config.client_secret_enc or b"", settings=cfg)
    except pii.PiiError as exc:
        log.error("oauth.client_secret_unopenable", provider=provider)
        raise UpstreamUnavailableProblem(
            "Single sign-on is temporarily unavailable. Please try again."
        ) from exc

    tokens = await idp.exchange_code(
        str(config.token_endpoint),
        code=code,
        redirect_uri=cfg.oauth_social_redirect_uri_template.format(provider=provider),
        client_id=str(config.client_id),
        client_secret=client_secret,
        code_verifier=pending.code_verifier,
    )
    jwks = await idp.fetch_jwks(str(config.jwks_uri))
    identity = oidc.verify_id_token(
        tokens.id_token,
        jwks=jwks,
        issuer=str(config.issuer),
        audience=str(config.client_id),
        nonce=pending.nonce,
        leeway_seconds=cfg.sso_clock_skew_seconds,
    )

    # Same ASCII hardening as `sso_callback` — the Kelvin-sign/IDNA-vs-CITEXT class of
    # mismatch applies identically here (see that function's comment for the concrete case).
    if not identity.email.isascii():
        log.warning("oauth.callback_refused", reason="non_ascii_identity", provider=provider)
        raise oidc.SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    user = await _resolve_or_provision_identity(
        directory=directory,
        provisioning=provisioning,
        intent=pending.intent,
        invitation_token=pending.invitation_token,
        identity=identity,
        full_name_hint=identity.email.split("@", 1)[0],
    )
    if not user.email.isascii() or user.email.strip().lower() != identity.email.strip().lower():
        log.warning(
            "oauth.callback_refused", reason="matched_user_email_mismatch", provider=provider
        )
        raise oidc.SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    await _link_external_identity(
        session, provider=provider, subject=identity.subject, user=user, email=identity.email
    )

    active = user.active_memberships()
    if not active:
        raise ForbiddenProblem("This account is not a member of any organization")

    auth_session = await start_session(
        session,
        user_id=user.user_id,
        cfg=cfg,
        amr=[OAUTH_AMR],
        idp=f"oauth:{provider}",
        request=request,
    )
    record_audit(
        session,
        AuditLog,
        organization_id=active[0].organization_id,
        actor_id=user.user_id,
        action="auth.oauth_signed_in",
        entity="session",
        entity_id=auth_session.id,
        request=request,
    )

    membership = resolve_organization(user, org_hint=None)
    if membership is None:
        token = tk.mint_pre_auth(
            pair,
            issuer=cfg.jwt_issuer,
            user_id=user.user_id,
            session_id=auth_session.id,
            reason="org_selection",
        )
        await session.commit()
        return LoginOutcome(
            status="org_selection_required", pre_auth_token=token.token, organizations=active
        )

    issued = await issue_for(
        session,
        pair=pair,
        cfg=cfg,
        auth_session=auth_session,
        membership=membership,
        entitlements=entitlements,
        audience=audience,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=membership.organization_id,
        event_type=EVT_OAUTH_SIGNED_IN,
        aggregate_type="session",
        aggregate_id=auth_session.id,
        payload={
            "user_id": str(user.user_id),
            "session_id": str(auth_session.id),
            "organization_id": str(membership.organization_id),
            "idp": f"oauth:{provider}",
            "amr": [OAUTH_AMR],
        },
    )
    await session.commit()
    return LoginOutcome(status="complete", issued=issued)
```

- [ ] **Step 4: Extend `tests/fake_idp.py`**

Read the existing fake's shape (it already implements `oidc.IdpClient` for the enterprise-SSO tests) and add a factory or parametrization so the same fake can serve Google/Microsoft-shaped discovery/token/JWKS responses — it is the same `IdpClient` Protocol, so no new fake class should be needed, only a helper that seeds it with a Google-looking issuer/keypair if the existing one hardcodes SSO-specific values. Make the minimal change; do not duplicate the fake.

- [ ] **Step 5: Run the test**

Run: `cd services/auth && pytest tests/test_oauth_social.py -v`
Expected: PASS (all tests in the file so far)

- [ ] **Step 6: Write and run the session-pin regression test**

```python
def test_oauth_session_is_not_federation_pinned():
    session = Session(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        amr=["oauth"],
        auth_time=datetime.now(UTC),
        idp="oauth:google",
    )
    assert services._federated_organization_pin(session) is None
```

Run: `cd services/auth && pytest tests/test_oauth_social.py -k not_federation_pinned -v`
Expected: PASS — confirms `_federated_organization_pin`'s existing `prefix in ("oidc", "saml")` check already excludes `oauth:`, with no code change needed. If this fails, that function needs a fix, not this test — stop and reassess before proceeding.

- [ ] **Step 7: Commit**

```bash
git add services/auth/app/services.py services/auth/tests/test_oauth_social.py services/auth/tests/fake_idp.py
git commit -m "auth: add oauth_social_callback"
```

---

### Task 9: `GET /auth/oauth/{provider}/callback`

**Files:**
- Modify: `services/auth/app/api/routes.py`
- Test: `services/auth/tests/test_oauth_social.py`

**Interfaces:**
- Consumes: `services.oauth_social_callback` (Task 8), `_to_result`/`_set_refresh_cookie`/`LoginResult` (existing, same as `/auth/login`).

- [ ] **Step 1: Write the failing end-to-end route test**

```python
async def test_oauth_social_callback_route_sets_refresh_cookie(
    client, db_session, fake_idp_google_app_override
):
    # Arrange: seed provider, drive /start to get a real `state`, then hit /callback.
    await _seed_google_provider(db_session)
    start = await client.get("/api/v1/auth/oauth/google/start", params={"intent": "signup"})
    state = start.json()["state"]
    response = await client.get(
        "/api/v1/auth/oauth/google/callback", params={"code": "fake-code", "state": state}
    )
    assert response.status_code == 200
    assert "opsgrids_refresh" in response.cookies
```

(Wire `fake_idp_google_app_override` the same way `test_sso.py`'s existing end-to-end SSO route test overrides `app.state.idp`/`app.state.directory` via `dependency_overrides` — follow that exact pattern, do not invent a new one.)

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd services/auth && pytest tests/test_oauth_social.py -k callback_route_sets_refresh_cookie -v`
Expected: FAIL — 404, route does not exist yet.

- [ ] **Step 3: Add the route**

In `services/auth/app/api/routes.py`, near `sso_callback`:

```python
@router.get(
    "/auth/oauth/{provider}/callback",
    response_model=LoginResult,
    summary="Finish self-serve sign-in with Google or Microsoft",
)
async def oauth_social_callback_route(
    provider: str,
    request: Request,
    response: Response,
    session: SessionDep,
    directory: DirectoryDep,
    provisioning: ProvisioningDep,
    entitlements: EntitlementsDep,
    idp: IdpDep,
    store: SsoStateDep,
    pair: KeyDep,
    code: Annotated[str, Query(min_length=1, max_length=2048)],
    state: Annotated[str, Query(min_length=1, max_length=512)],
) -> Any:
    cfg = settings()
    outcome = await services.oauth_social_callback(
        session,
        cfg=cfg,
        pair=pair,
        directory=directory,
        provisioning=provisioning,
        entitlements=entitlements,
        idp=idp,
        store=store,
        provider=provider,
        code=code,
        state=state,
        request=request,
    )
    return _to_result(outcome, response, cfg)
```

- [ ] **Step 4: Run the test**

Run: `cd services/auth && pytest tests/test_oauth_social.py -v`
Expected: PASS (every test in the file)

- [ ] **Step 5: Run the full auth-service suite**

Run: `cd services/auth && pytest -v`
Expected: PASS — in particular every pre-existing `test_sso.py`/`test_tenancy.py` test still passes unchanged, confirming Task 3's `StateStore` widening and Task 1's new table did not disturb anything.

- [ ] **Step 6: Commit**

```bash
git add services/auth/app/api/routes.py services/auth/tests/test_oauth_social.py
git commit -m "auth: add the /auth/oauth/{provider}/callback route"
```

---

### Task 10: `bin/seed-oauth-providers`

**Files:**
- Create: `services/auth/bin/seed-oauth-providers`

**Interfaces:**
- Consumes: `Settings.google_oauth_client_id`/`_secret`, `microsoft_oauth_client_id`/`_secret` (Task 2), `oidc.HttpIdpClient.discover`, `pii.seal`.

- [ ] **Step 1: Add the seeding function to `app/bootstrap.py`**

`bin/seed-staff` is a thin shell wrapper (`exec python -m app.bootstrap`) around a `main()` in `app/bootstrap.py` that uses `init_engine(cfg)` / `async with session_scope() as session:` / `finally: await dispose_engine()` (no explicit `session.commit()` — `session_scope()` commits on clean exit). Follow that exact shape rather than writing a new standalone script, so there is one bootstrap entrypoint, not two. Add to `services/auth/app/bootstrap.py`:

```python
from app.models import PlatformOAuthProvider
from app.oidc import HttpIdpClient
from platform_core import pii

DISCOVERY_URLS = {
    "google": "https://accounts.google.com/.well-known/openid-configuration",
    "microsoft": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
}


async def seed_oauth_providers(session: AsyncSession, *, cfg: Settings) -> None:
    """Seed `platform_oauth_providers` from deployment env vars.

    Run after `alembic upgrade head`, once per environment, and again whenever a client
    secret rotates. A provider with no client_id/secret configured is left disabled —
    "inert without keys, by design" (ADR-0013 §6's Razorpay precedent).
    """
    idp = HttpIdpClient()
    try:
        creds = {
            "google": (cfg.google_oauth_client_id, cfg.google_oauth_client_secret),
            "microsoft": (cfg.microsoft_oauth_client_id, cfg.microsoft_oauth_client_secret),
        }
        for provider, (client_id, client_secret) in creds.items():
            row = await session.get(PlatformOAuthProvider, provider)
            if row is None:
                row = PlatformOAuthProvider(provider=provider)
                session.add(row)
            if not client_id or not client_secret:
                row.enabled = False
                continue
            doc = await idp.discover(DISCOVERY_URLS[provider])
            row.client_id = client_id
            row.client_secret_enc, _ = pii.seal(
                client_secret.get_secret_value(), kind="oauth_client_secret", settings=cfg
            )
            row.issuer = doc["issuer"]
            row.authorization_endpoint = doc["authorization_endpoint"]
            row.token_endpoint = doc["token_endpoint"]
            row.jwks_uri = doc["jwks_uri"]
            row.enabled = True
    finally:
        await idp.aclose()
```

Then add a second `main()`-style entrypoint, mirroring `bootstrap.py`'s existing `main()`/`if __name__ == "__main__":` pattern exactly (read the end of the file to match it — it likely dispatches on `sys.argv[0]`-style or is a second function the shell script could call). Add `bin/seed-oauth-providers` as a one-line shell wrapper identical in shape to `bin/seed-staff`, pointing at this new entrypoint:

```bash
#!/usr/bin/env bash
set -euo pipefail
exec python -m app.bootstrap seed-oauth-providers
```

Adjust `app/bootstrap.py`'s `main()`/CLI dispatch to route a `seed-oauth-providers` argument to `seed_oauth_providers`, using whatever argument-handling (or lack of it — it may currently take none and need a minimal `sys.argv` check added) the existing `main()` has. Read it before changing it; do not restructure unrelated bootstrap behavior.

- [ ] **Step 2: Make it executable and smoke-test with no credentials set**

Run: `chmod +x services/auth/bin/seed-oauth-providers && cd services/auth && ./bin/seed-oauth-providers`
Expected: runs to completion; `SELECT provider, enabled FROM auth.platform_oauth_providers;` shows both rows with `enabled = false`.

- [ ] **Step 3: Commit**

```bash
git add services/auth/bin/seed-oauth-providers services/auth/app/bootstrap.py
git commit -m "auth: add bin/seed-oauth-providers"
```

---

### Task 11: account-service — `POST /internal/v1/signup-federated`

**Files:**
- Modify: `services/account/app/schemas.py`
- Modify: `services/account/app/api/internal.py`
- Test: `services/account/tests/test_internal.py`

**Interfaces:**
- Consumes: `services.signup` (existing, unchanged), `SignupIn` (existing).
- Produces: `SignupFederatedIn` schema (`organization_name`, `slug`, `full_name`, `email` — same fields as `SignupIn`), `SignupFederatedOut` (`organization_id`, `user_id`), route `POST /internal/v1/signup-federated`.

- [ ] **Step 1: Write the failing test**

```python
# services/account/tests/test_internal.py — add alongside the existing internal-route tests
async def test_signup_federated_creates_org_and_returns_user_id(client, service_token_for_auth):
    response = await client.post(
        "/internal/v1/signup-federated",
        json={
            "organization_name": "Acme",
            "slug": "acme-federated",
            "full_name": "Ada",
            "email": "ada@acme-federated.com",
        },
        headers={"X-Internal-Token": service_token_for_auth},
    )
    assert response.status_code == 201
    body = response.json()
    assert "organization_id" in body and "user_id" in body


async def test_signup_federated_requires_service_token(client):
    response = await client.post(
        "/internal/v1/signup-federated",
        json={
            "organization_name": "Acme",
            "slug": "acme-2",
            "full_name": "Ada",
            "email": "ada2@x.com",
        },
    )
    assert response.status_code == 401
```

(`service_token_for_auth` — reuse whatever fixture `test_internal.py` already uses to mint a valid service token for its existing tests; read the top of that file first.)

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd services/account && pytest tests/test_internal.py -k signup_federated -v`
Expected: FAIL — 404, route does not exist.

- [ ] **Step 3: Add the schemas**

In `services/account/app/schemas.py`, near `SignupIn`/`SignupOut`:

```python
class SignupFederatedIn(BaseModel):
    """Same shape as `SignupIn`, called by auth-service after it has already verified the
    email via a Google/Microsoft ID token — see
    docs/superpowers/specs/2026-10-03-oauth2-social-login-design.md §4."""

    organization_name: str = Field(min_length=1, max_length=200)
    slug: Slug
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr


class SignupFederatedOut(BaseModel):
    organization_id: uuid.UUID
    user_id: uuid.UUID
```

- [ ] **Step 4: Add the route**

In `services/account/app/api/internal.py`, add the imports (`SignupFederatedIn`, `SignupFederatedOut`, `SignupIn`, `services`, `uuid`, `session_scope`, `selectors` — check which of these the file does not already import) and:

`require_service_token` is already applied to every route on this router via `dependencies=[Depends(require_service_token)]` on the `APIRouter(...)` call at the top of the file (confirmed by reading it — not per-route). Do not add it again as a route parameter; adding this route to the existing `router` is sufficient.

```python
@router.post(
    "/signup-federated",
    response_model=SignupFederatedOut,
    status_code=201,
    summary="Create an organisation for a verified OAuth identity (called by auth-service)",
)
async def signup_federated(
    payload: SignupFederatedIn, directory: DirectorySessionDep
) -> SignupFederatedOut:
    """Same duplicate checks and same `services.signup` as the public `/account/signup` route
    (routes.py) — only the caller differs: this is reached by auth-service, already holding a
    verified email, never directly by a browser."""
    if await selectors.slug_taken(directory, payload.slug):
        raise ConflictProblem(f"The address {payload.slug!r} is already taken")
    if await selectors.get_user_by_email(directory, payload.email) is not None:
        raise ConflictProblem("That email already has an account. Sign in instead.")

    organization_id = uuid.uuid4()
    async with session_scope(organization_id) as session:
        membership = await services.signup(
            session,
            organization_id=organization_id,
            payload=SignupIn(
                organization_name=payload.organization_name,
                slug=payload.slug,
                full_name=payload.full_name,
                email=payload.email,
            ),
        )
    return SignupFederatedOut(organization_id=organization_id, user_id=membership.user_id)
```

(`require_service_token` protection comes from the router-level `dependencies=[...]`, same as Task 11 — no per-route dependency parameter needed.)

- [ ] **Step 5: Run the test**

Run: `cd services/account && pytest tests/test_internal.py -k signup_federated -v`
Expected: PASS (both tests)

- [ ] **Step 6: Commit**

```bash
git add services/account/app/schemas.py services/account/app/api/internal.py services/account/tests/test_internal.py
git commit -m "account: add internal signup-federated endpoint"
```

---

### Task 12: account-service — invitation email-match guard and `POST /internal/v1/invitations/accept-federated`

**Files:**
- Modify: `services/account/app/schemas.py`
- Modify: `services/account/app/services.py`
- Modify: `services/account/app/api/internal.py`
- Test: `services/account/tests/test_invitation_accept.py`, `services/account/tests/test_internal.py`

**Interfaces:**
- Consumes: `services.accept_invitation` (existing — gains one new optional parameter).
- Produces: `services.accept_invitation(session, raw_token, full_name, *, expected_email=None, request=None)`, `InvitationAcceptFederatedIn` schema, route `POST /internal/v1/invitations/accept-federated`.

- [ ] **Step 1: Write the failing adversarial test for the service function first**

In `services/account/tests/test_invitation_accept.py`:

```python
async def test_accept_invitation_rejects_email_mismatch(db_session, pending_invitation):
    # pending_invitation fixture: an Invitation row for "invitee@acme.com"
    with pytest.raises(NotFoundProblem):
        await services.accept_invitation(
            db_session,
            pending_invitation.raw_token,
            "Someone Else",
            expected_email="attacker@evil.com",
        )


async def test_accept_invitation_allows_matching_email(db_session, pending_invitation):
    membership = await services.accept_invitation(
        db_session,
        pending_invitation.raw_token,
        "Invitee Name",
        expected_email="invitee@acme.com",
    )
    assert membership is not None


async def test_accept_invitation_still_works_with_no_expected_email(db_session, pending_invitation):
    """The existing public route never passes `expected_email` — must stay unaffected."""
    membership = await services.accept_invitation(
        db_session, pending_invitation.raw_token, "Invitee Name"
    )
    assert membership is not None
```

(`pending_invitation` — reuse whichever fixture `test_invitation_accept.py` already builds for its existing tests; read that file first rather than inventing a new one, and note its `raw_token`/email.)

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd services/account && pytest tests/test_invitation_accept.py -k email_mismatch -v`
Expected: FAIL — `TypeError: accept_invitation() got an unexpected keyword argument 'expected_email'`

- [ ] **Step 3: Add the guard**

In `services/account/app/services.py`, change `accept_invitation`'s signature and body:

```python
async def accept_invitation(
    session: AsyncSession,
    raw_token: str,
    full_name: str,
    *,
    expected_email: str | None = None,
    request: Request | None = None,
) -> Membership:
    invitation = await selectors.get_invitation_by_hash(session, _hash_token(raw_token))
    invalid = NotFoundProblem("That invitation is not valid")
    if invitation is None or invitation.accepted_at or invitation.revoked_at:
        raise invalid
    if invitation.expires_at <= datetime.now(UTC):
        raise invalid
    # A federated accept (Google/Microsoft) proves an email, not a token holder's identity
    # beyond that — the invitation's authorization is "this token", but provisioning has
    # always been keyed by `invitation.email` (`add_member` below). Without this check,
    # holding the link would let anyone accept it under their own OAuth identity. Same
    # opaque message as every other invalid-invitation case — see spec §6/§7.
    if (
        expected_email is not None
        and expected_email.strip().lower() != invitation.email.strip().lower()
    ):
        raise invalid

    membership = await add_member(
        session,
        org_id=invitation.organization_id,
        email=invitation.email,
        full_name=full_name,
        actor_user_id=invitation.invited_by,
        role_codes=list(invitation.role_codes),
        request=request,
    )
    invitation.accepted_at = datetime.now(UTC)
    invitation.accepted_user_id = membership.user_id
    return membership
```

- [ ] **Step 4: Run all three service-level tests**

Run: `cd services/account && pytest tests/test_invitation_accept.py -v`
Expected: PASS — all three new tests, and every pre-existing test in the file (the public `/invitations/accept` route never passes `expected_email`, so its behavior is unchanged).

- [ ] **Step 5: Write the failing internal-route test**

In `services/account/tests/test_internal.py`:

```python
async def test_accept_invitation_federated_happy_path(
    client, db_session, pending_invitation, service_token_for_auth
):
    response = await client.post(
        "/internal/v1/invitations/accept-federated",
        json={
            "token": pending_invitation.raw_token,
            "email": "invitee@acme.com",
            "full_name": "Invitee",
        },
        headers={"X-Internal-Token": service_token_for_auth},
    )
    assert response.status_code == 200
    body = response.json()
    assert "organization_id" in body and "user_id" in body


async def test_accept_invitation_federated_email_mismatch_is_404(
    client, db_session, pending_invitation, service_token_for_auth
):
    response = await client.post(
        "/internal/v1/invitations/accept-federated",
        json={
            "token": pending_invitation.raw_token,
            "email": "attacker@evil.com",
            "full_name": "X",
        },
        headers={"X-Internal-Token": service_token_for_auth},
    )
    assert response.status_code == 404
```

- [ ] **Step 6: Run it to confirm it fails**

Run: `cd services/account && pytest tests/test_internal.py -k accept_invitation_federated -v`
Expected: FAIL — 404, route not wired yet (distinguish from the *intended* 404 in the mismatch test by running the happy-path test alone first).

- [ ] **Step 7: Add the schema and route**

In `services/account/app/schemas.py`:

```python
class InvitationAcceptFederatedIn(BaseModel):
    token: str = Field(min_length=1)
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)


class InvitationAcceptFederatedOut(BaseModel):
    organization_id: uuid.UUID
    user_id: uuid.UUID
```

In `services/account/app/api/internal.py`:

```python
@router.post(
    "/invitations/accept-federated",
    response_model=InvitationAcceptFederatedOut,
    summary="Accept an invitation for a verified OAuth identity (called by auth-service)",
)
async def accept_invitation_federated(
    payload: InvitationAcceptFederatedIn,
    directory: DirectorySessionDep,
) -> InvitationAcceptFederatedOut:
    organization_id = await services.resolve_invitation_organization(directory, payload.token)
    async with session_scope(organization_id) as session:
        membership = await services.accept_invitation(
            session, payload.token, payload.full_name, expected_email=payload.email
        )
    return InvitationAcceptFederatedOut(organization_id=organization_id, user_id=membership.user_id)
```

Match this to however the existing public `/invitations/accept` route in `routes.py` actually structures its bypass-then-narrow call (read it again at this point — it may call `resolve_invitation_organization` + `session_scope` exactly this way, or may do it slightly differently) rather than assuming this snippet is pixel-exact; the behavior (narrow the tenant from the invitation, then accept) must match.

- [ ] **Step 8: Run both tests, then the full account-service suite**

Run: `cd services/account && pytest tests/test_internal.py tests/test_invitation_accept.py -v`
Expected: PASS

Run: `cd services/account && pytest -v`
Expected: PASS — confirms Task 12's `accept_invitation` signature change did not break the public route's existing tests in `test_invitation_accept.py` or elsewhere.

- [ ] **Step 9: Commit**

```bash
git add services/account/app/schemas.py services/account/app/services.py services/account/app/api/internal.py services/account/tests/test_invitation_accept.py services/account/tests/test_internal.py
git commit -m "account: add invitation email-match guard and accept-federated endpoint"
```

---

### Task 13: Full-branch verification

**Files:** none (verification only)

- [ ] **Step 1: Run both services' full suites**

Run: `cd services/auth && pytest -v && cd ../account && pytest -v`
Expected: all PASS.

- [ ] **Step 2: Run lint/typecheck per CLAUDE.md's `make check`**

Run: `make check` from the repo root (or the per-service equivalent if `make check` requires infra this worktree doesn't have running — check `Makefile` first).
Expected: no new lint/type errors in the files this plan touched.

- [ ] **Step 3: Manually trace the three flows against the spec**

Re-read `docs/superpowers/specs/2026-10-03-oauth2-social-login-design.md` §3 side-by-side with the final `oauth_social_callback`/`_resolve_or_provision_identity`/internal-endpoint code and confirm every branch in the flow diagram has a corresponding code path and a passing test. Note any gap found as a new task rather than silently patching it in this verification task.

- [ ] **Step 4: Commit the plan's completion marker (if any docs need a status update)**

If `docs/superpowers/specs/2026-10-03-oauth2-social-login-design.md`'s header should move from "design, approved" to "implemented", make that one-line edit and commit it separately:

```bash
git commit -am "docs: mark OAuth2 social login spec as implemented"
```
