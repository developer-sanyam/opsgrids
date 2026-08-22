"""Typed, fail-fast service configuration.

12-factor env vars via pydantic-settings. A missing or malformed value raises at import
time, before the process ever serves a request.

Note what is *absent*: there is no default organization id. Tenancy is resolved from the
request, always (ADR-0000 §1, §10). A default org id is how one tenant reads another's
data, and every system that has one eventually does.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url


class ServiceSettings(BaseSettings):
    """Base settings every service inherits.

    Subclass per service to pin its identity::

        class Settings(ServiceSettings):
            service_name: str = "account"
            db_schema: str = "account"
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- identity of this process -------------------------------------------------
    service_name: str
    db_schema: str
    environment: str = Field(default="local", pattern="^(local|test|ci|staging|uat|production)$")
    debug: bool = False

    # --- datastores ---------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://atmora:atmora@localhost:5434/atmora"

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
    rabbitmq_url: str = "amqp://atmora:atmora@localhost:5673/"

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
    jwt_issuer: str = "https://auth.atmora.local"
    jwks_url: str = "http://localhost:8110/.well-known/jwks.json"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 604_800

    # Proves a request came from a gateway or a sibling service. Services trust
    # forwarded identity headers ONLY when this is present and matches.
    internal_service_token: SecretStr = SecretStr("dev-only-internal-token")

    # --- PII ----------------------------------------------------------------------
    pii_encryption_key: SecretStr = SecretStr("dev-only-pii-key-change-me")
    pii_decrypt_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- observability ------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    otel_exporter_otlp_endpoint: str | None = None
    sentry_dsn: str | None = None

    # --- platform -----------------------------------------------------------------
    app_base_url: str = "http://localhost:5273"
    # NoDecode is load-bearing: without it the dotenv source tries to JSON-decode a
    # complex field before validators run, and the documented comma-separated form
    # raises SettingsError at import.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5273"]
    )

    @field_validator("cors_origins", "pii_decrypt_keys", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
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
        """Refuse to start a real deployment with development secrets."""
        if self.environment not in ("production", "staging", "uat"):
            return
        insecure = {
            "internal_service_token": self.internal_service_token.get_secret_value()
            == "dev-only-internal-token",
            "pii_encryption_key": self.pii_encryption_key.get_secret_value()
            == "dev-only-pii-key-change-me",
        }
        offenders = [name for name, bad in insecure.items() if bad]
        if offenders:
            raise ValueError(
                f"{self.environment} refuses development secrets: {', '.join(offenders)}"
            )
        if self.debug:
            raise ValueError(f"debug must be off in {self.environment}")


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
