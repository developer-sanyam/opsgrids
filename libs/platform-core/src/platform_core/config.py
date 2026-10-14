"""Typed, fail-fast service configuration.

12-factor env vars via pydantic-settings. A missing or malformed value raises at import
time, before the process ever serves a request.

Note what is *absent*: there is no default organization id. Tenancy is resolved from the
request, always (ADR-0000 §1, §10). A default org id is how one tenant reads another's
data, and every system that has one eventually does.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url


def _repo_env_file() -> str:
    """The workspace `.env`, found by walking up for the root `pyproject.toml`.

    Falls back to the bare name so a deployment that supplies configuration through real
    environment variables — which is the production path — behaves exactly as before.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (parent / "services").is_dir():
            return str(parent / ".env")
    return ".env"


class ServiceSettings(BaseSettings):
    """Base settings every service inherits.

    Subclass per service to pin its identity::

        class Settings(ServiceSettings):
            service_name: str = "account"
            db_schema: str = "account"
    """

    model_config = SettingsConfigDict(
        # Anchored to the repository root, not the working directory.
        #
        # `env_file=".env"` resolves relative to wherever the process started. Every service
        # runs from its own directory — the make target, the Dockerfile and the end-to-end
        # harness all `cd services/<name>` — and there is no `.env` there, so **no configured
        # value loaded at all** and every setting silently fell back to its default.
        #
        # Nothing noticed because the defaults happen to match the development file. The one
        # setting with no usable default, auth-service's signing key path, is what finally
        # surfaced it: the service refused to start, reporting a missing key rather than a
        # missing configuration file.
        env_file=_repo_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # An empty variable is "not configured", not "configured to the empty string". A
        # compose file cannot leave a variable undefined when its value in `.env` is blank —
        # `${RAZORPAY_KEY_ID:-}` still defines it — and `razorpay_key_id: str | None` read
        # `""` as a key, at which point the provider believed it could reach the network.
        # Every optional secret in the platform is typed `| None` on exactly that promise.
        env_ignore_empty=True,
        # A refused setting must never print what it was given. Without this, pydantic puts
        # `input_value=<the whole merged settings input>` into `str(ValidationError)` — read from
        # the environment as plain strings, not `SecretStr` — and shortens it to its head and its
        # tail, so whichever secret is the last field set (a provider key secret, the PII key)
        # lands in the start-up crash on stdout, in the terminal scrollback and in any log pasted
        # into a ticket. The message still names the variable; only the value is withheld.
        hide_input_in_errors=True,
    )

    # --- identity of this process -------------------------------------------------
    service_name: str
    db_schema: str
    environment: str = Field(default="local", pattern="^(local|test|ci|staging|uat|production)$")
    debug: bool = False

    # --- datastores ---------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://opsgrids:opsgrids@localhost:5434/opsgrids"

    #: The role the API connects as. **NOBYPASSRLS**, granted DML on this service's schema
    #: only — so account-service cannot read `auth.credentials`, which is the entire reason
    #: auth-service holds credentials and the directory does not.
    #:
    #: `None` means "use `database_url` unchanged". That is what production does: each
    #: service gets its own DSN carrying its own credentials, and leaves this unset. Locally
    #: one `.env` is shared and only the username is swapped, because a single file cannot
    #: carry a different password per service without becoming a deployment system.
    db_app_role: str | None = None

    #: The role migrations and backfills connect as. **BYPASSRLS**, and it owns the schemas,
    #: so it can write anything — which is why nothing long-running connects as it.
    db_admin_role: str | None = None

    #: The role the internal directory lookups connect as. **BYPASSRLS but SELECT-only**, on a
    #: named handful of tables.
    #:
    #: Deliberately not the admin role. Resolving a person before an organization is known is
    #: genuinely cross-tenant -- it is what lets one login serve several customers -- but it is
    #: a *read*. Giving those endpoints the migration role would hand a bug there the ability
    #: to write anything, and the risk being managed is a route added to that module later, not
    #: the three that exist today.
    db_directory_role: str | None = None

    #: The role the outbox relay and the outbox-lag probe connect as. **BYPASSRLS, with SELECT
    #: and UPDATE on `outbox` only.**
    #:
    #: BYPASSRLS because the relay's query exists to *discover* which tenants have work, so
    #: there is no tenant to bind before it runs. Not the admin role, because a relay bug must
    #: not be able to write anything else. Not the app role, because that one is now correctly
    #: blind: under row-level security with no tenant bound it selects nothing, logs nothing and
    #: loops while the outbox fills (ADR-0002, "Relay stall").
    db_outbox_role: str | None = None

    redis_url: str = "redis://localhost:6380/0"
    rabbitmq_url: str = "amqp://opsgrids:opsgrids@localhost:5673/"

    # Under ASGI we never hold a connection across a request boundary; the pool is the
    # single place connections are managed.
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False

    # --- auth ---------------------------------------------------------------------
    # Only auth-service sets a private key. Every other service leaves it unset and
    # verifies through JWKS — that asymmetry is the point (ADR-0000 §2).
    jwt_private_key_path: str | None = None
    jwt_kid: str = "dev-1"
    jwt_algorithm: str = "EdDSA"
    jwt_issuer: str = "https://auth.opsgrids.local"
    jwks_url: str = "http://localhost:8110/.well-known/jwks.json"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 604_800

    # Proves a request came from a gateway or a sibling service. Services trust
    # forwarded identity headers ONLY when this is present and matches.
    #: Signs the short-lived service tokens in `platform_core.internal_auth`. **A signing key,
    #: not a credential** — presenting it as a token is refused.
    #:
    #: At least 32 bytes: HMAC-SHA256 keys shorter than the digest are weaker than the
    #: algorithm implies, and PyJWT warns about it (RFC 7518 §3.2). The dev default is padded
    #: to that length so the warning means something when it does appear.
    internal_service_token: SecretStr = SecretStr(
        "dev-only-internal-token-padded-to-32-bytes-minimum"
    )
    #: How long a minted service token is good for. Short on purpose: long enough for a call
    #: and a retry, far too short to be worth capturing. See `platform_core.internal_auth`.
    service_token_ttl_seconds: int = 60

    #: HMAC key for the admin-gateway -> auth hop-signature header
    #: (`platform_core.internal_auth.sign_admin_hop`/`verify_admin_hop`). Optional: only
    #: admin-gateway (signs) and auth (verifies) ever read it; every other service leaves it
    #: unset. Defence in depth on top of the route table blocking `/api/v1/auth/operator` at
    #: every gateway (BLOCKER 2 of the 2026-09-24 security review) — even a gateway
    #: misconfigured to forward that prefix produces a request auth's operator routes still
    #: refuse, because it carries no valid hop signature.
    admin_gateway_hop_key: SecretStr | None = None

    # --- PII ----------------------------------------------------------------------
    pii_encryption_key: SecretStr = SecretStr("dev-only-pii-key-change-me")
    pii_decrypt_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- egress ---------------------------------------------------------------------
    #: Lets `platform_core.egress.EgressGuard` accept plain `http://` for an admin-supplied
    #: URL (OIDC discovery, JWKS, a token endpoint) instead of refusing anything but `https`.
    #: Exists only for a local IdP fixture that has no certificate; refused outright below
    #: for `production`, `staging` and `uat` so a value that leaks into one of those `.env`
    #: files fails at startup instead of quietly widening every egress-guarded fetch.
    egress_allow_insecure_http: bool = False

    # --- observability ------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    # No error-tracking or tracing exporter is wired (no Sentry or OpenTelemetry SDK ships), so
    # there is deliberately no DSN or OTLP endpoint setting here: a setting nothing reads
    # suggests a capability the platform does not have. See docs/operations.md, "Error tracking".

    # --- platform -----------------------------------------------------------------
    app_base_url: str = "http://localhost:5273"
    # NoDecode is load-bearing: without it the dotenv source tries to JSON-decode a
    # complex field before validators run, and the documented comma-separated form
    # raises SettingsError at import.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5273"]
    )
    #: A second, narrower CORS allowance for exactly the two cookie-authenticated auth routes
    #: (`platform_core.app.AUTH_CROSS_ORIGIN_PATHS`) — `/api/v1/auth/refresh` and
    #: `/api/v1/auth/logout`. ADR-0003 addendum: the refresh cookie is host-only on
    #: `app.opsgrids.com`, so every other product SPA calls those two routes cross-origin to
    #: reach it, and each needs its own origin allowed *with credentials* to do so. Widening
    #: `cors_origins` itself would let those same origins make a credentialed call to *every*
    #: route this gateway fronts, not just the two that need it — a materially bigger blast
    #: radius for a page compromised on one of them. Empty by default: only the gateway that
    #: fronts `app.opsgrids.com` (app-gateway, in production) ever sets this; every other
    #: service leaves it unset and those two paths fall back to `cors_origins` like any other
    #: route. Storefront hosts (`*.stores.opsgrids.com`, custom domains) must never appear here
    #: — nothing there is meant to hold a platform session.
    auth_refresh_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", "auth_refresh_cors_origins", "pii_decrypt_keys", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("auth_refresh_cors_origins", mode="after")
    @classmethod
    def _auth_refresh_cors_origins_are_narrow_and_real(cls, origins: list[str]) -> list[str]:
        """MINOR 2 of the 2026-09-24 SSO review: this allow-list is credentialed CORS for a
        cookie-authenticated route — a shape mistake here is not a typo that 404s, it is a
        widened attack surface that answers 200. Every entry must be an exact `https://host`
        origin: no `*` (Starlette's own `CORSMiddleware` echoes back *any* origin with
        credentials once a wildcard is configured — there is no partial-wildcard reading of
        this list that is safe), no plain `http://`, no path or query or fragment (an origin is
        scheme+host+port, nothing else — a value that parses as more than that was probably
        meant to be a route, not an origin), and never a storefront host: nothing under
        `stores.opsgrids.com`, and never the bare registrable domain either, since merchant
        storefronts are catch-all subdomains of it (ADR-0012 §2) and nothing there is meant to
        hold a platform session.
        """
        for origin in origins:
            if origin == "*":
                raise ValueError(
                    "auth_refresh_cors_origins must not contain '*' — combined with "
                    "credentials, Starlette echoes back any caller's Origin verbatim"
                )
            parsed = urlsplit(origin)
            if parsed.scheme != "https":
                raise ValueError(
                    f"auth_refresh_cors_origins entry {origin!r} must be an https:// origin"
                )
            if not parsed.hostname:
                raise ValueError(f"auth_refresh_cors_origins entry {origin!r} has no host")
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
                raise ValueError(
                    f"auth_refresh_cors_origins entry {origin!r} must be a bare origin — "
                    "scheme, host and port only, no path, query, fragment or userinfo"
                )
            host = parsed.hostname.lower()
            if host == "stores.opsgrids.com" or host.endswith(".stores.opsgrids.com"):
                raise ValueError(
                    f"auth_refresh_cors_origins entry {origin!r} is a storefront host — "
                    "nothing under stores.opsgrids.com may ever hold a platform session"
                )
        return origins

    @field_validator("admin_gateway_hop_key", mode="before")
    @classmethod
    def _empty_hop_key_is_unset(cls, v: object) -> object:
        """`compose.prod.yml` passes `${ADMIN_GATEWAY_HOP_KEY:-}` — an *empty* value when the
        box has none, so a missing key does not break every compose command (backup.sh's
        `pg_dump` included). An empty string is "unset", not a zero-length key: in a real
        deployment auth then refuses to start for the right reason (no key), and in development
        the signature check stays a no-op rather than rejecting every request against ""."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def dsn_as(self, role: str | None) -> str:
        """`database_url` with its username replaced by `role`.

        Returns the DSN untouched when `role` is None, which is the production path: each
        service is handed its own DSN with its own credentials and never rewrites it.

        The rewrite exists for local development, where one `.env` serves every service and
        the bootstrap gives each role the same password. It touches only the username —
        host, port, database and password are whatever `database_url` already said.
        """
        if not role:
            return self.database_url
        url = make_url(self.database_url)
        return url.set(username=role).render_as_string(hide_password=False)

    @property
    def app_dsn(self) -> str:
        """What the API connects with. NOBYPASSRLS once the roles are split."""
        return self.dsn_as(self.db_app_role)

    @property
    def directory_dsn(self) -> str:
        """What the cross-tenant directory reads connect with. BYPASSRLS, SELECT-only."""
        return self.dsn_as(self.db_directory_role)

    @property
    def outbox_dsn(self) -> str:
        """What the relay and the outbox-lag probe connect with. BYPASSRLS, `outbox` only."""
        return self.dsn_as(self.db_outbox_role)

    @property
    def admin_dsn(self) -> str:
        """What migrations connect with. BYPASSRLS, and it owns everything."""
        return self.dsn_as(self.db_admin_role)

    def model_post_init(self, _: object) -> None:
        """Refuse to start a real deployment with development or CI secrets.

        Placeholders are matched by their documented prefixes (`dev-only-`, `ci-only-` —
        see the Makefile's secret-scan contract), not by exact string: an exact-equality
        check rots the moment a placeholder is edited, and the `ci-only-pii-key` committed
        in ci.yml sailed straight past one for exactly that reason.
        """
        if self.environment not in ("production", "staging", "uat"):
            return
        placeholder_prefixes = ("dev-only-", "ci-only-")
        insecure = {
            "internal_service_token": self.internal_service_token.get_secret_value().startswith(
                placeholder_prefixes
            ),
            "pii_encryption_key": self.pii_encryption_key.get_secret_value().startswith(
                placeholder_prefixes
            ),
        }
        if len(self.internal_service_token.get_secret_value().encode()) < 32:
            raise ValueError(
                "internal_service_token must be at least 32 bytes: it signs service tokens "
                "with HMAC-SHA256, and a key shorter than the digest is weaker than the "
                "algorithm suggests (RFC 7518 §3.2)."
            )
        if len(self.pii_encryption_key.get_secret_value().encode()) < 32:
            raise ValueError(
                "pii_encryption_key must be at least 32 bytes: it is the passphrase every "
                "tenant's PII is sealed under, and a short or guessable one makes the "
                "encryption theatre. Generate 32+ random bytes and treat them as a secret."
            )
        if self.admin_gateway_hop_key is not None:
            hop_key = self.admin_gateway_hop_key.get_secret_value()
            insecure["admin_gateway_hop_key"] = hop_key.startswith(placeholder_prefixes)
            if len(hop_key.encode()) < 32:
                raise ValueError(
                    "admin_gateway_hop_key must be at least 32 bytes: it signs the "
                    "admin-gateway hop header with HMAC-SHA256, and a key shorter than the "
                    "digest is weaker than the algorithm suggests (RFC 7518 §3.2)."
                )

        offenders = [name for name, bad in insecure.items() if bad]
        if offenders:
            raise ValueError(
                f"{self.environment} refuses development/CI secrets: {', '.join(offenders)}"
            )
        if self.debug:
            raise ValueError(f"debug must be off in {self.environment}")
        if self.egress_allow_insecure_http:
            raise ValueError(
                f"{self.environment} refuses egress_allow_insecure_http: every "
                "admin-supplied URL this platform fetches (OIDC discovery, JWKS, a token "
                "endpoint) must be https outside development."
            )


@lru_cache(maxsize=8)
def get_settings(cls: type[ServiceSettings] = ServiceSettings) -> ServiceSettings:
    """Cached settings for `cls`.

    Cached because settings are read on hot paths; keyed by class so a process that
    somehow holds two settings types does not thrash. Tests that need fresh values call
    `get_settings.cache_clear()`.
    """
    # Subclasses populate every field from the environment — the no-arg construction
    # mypy objects to is exactly how pydantic-settings is meant to be used.
    return cls()


def settings_for(cls: type[ServiceSettings]) -> ServiceSettings:
    """Readable alias used by service `config.py` modules."""
    return get_settings(cls)


__all__ = ["ServiceSettings", "get_settings", "settings_for"]
