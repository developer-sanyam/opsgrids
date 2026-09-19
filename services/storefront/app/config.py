"""Configuration for the storefront service.

One field here is load-bearing in a way the others are not, and ADR-0010 §13 calls it "the
single worst failure in the design": `db_directory_role`.

`get_directory_session` **falls back to the ordinary application session when no directory
role is configured** (`platform_core/db.py:334-337`). For account-service's internal routes
that fallback is the safe direction to be wrong in — those callers have a tenant or a 404. For
this service it is not safe, it is *invisible*: the anonymous slug lookup would run on the
NOBYPASSRLS application role with no tenant GUC bound, match zero rows under row-level
security, and every buyer in the world would be told the store does not deliver to them —
HTTP 200, a well-formed body, nothing in a log.

So `assert_directory_role_configured` refuses to boot without it (ADR-0010 §4). Not a warning.

**On the environment variable's name.** ADR-0010 §16 calls this `STOREFRONT_DB_DIRECTORY_ROLE`
in `.env.example`, and that name would do nothing: `ServiceSettings` sets no `env_prefix`, so
the variable pydantic reads is `DB_DIRECTORY_ROLE` — shared by every service that reads the
same file. Setting it in the repository's single local `.env` would silently rebind
*product-service's* directory reader to this service's role, which is a second silent outage in
exchange for avoiding the first. The role is therefore pinned as a default here, exactly as
`services/product/app/config.py` pins its own, and `.env.example` documents the trap instead of
setting the variable.
"""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "storefront"
    db_schema: str = "storefront"
    #: Its own NOBYPASSRLS role, granted DML on the `storefront` schema and nothing else — so
    #: this service cannot read another product's tables, and so row-level security can bind it.
    db_app_role: str | None = "opsgrids_storefront"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"
    #: BYPASSRLS but **SELECT-only, on `storefront.storefronts` and nothing else**. The
    #: anonymous serviceability check resolves a store slug to an organization *before* any
    #: tenant can be bound — that is what a stranger's request has instead of a token — so it
    #: cannot run on the application role, which correctly reads nothing without a tenant GUC.
    #: Its own role rather than `opsgrids_directory` because each bypass reader is confined to
    #: its own service's named tables (ADR-0002 §2), and its own role rather than the admin one
    #: so a bug on that path cannot write anything at all.
    db_directory_role: str | None = "opsgrids_storefront_directory"

    #: How long a resolved store slug is kept in Redis (ADR-0010 §12). Worth caching precisely
    #: because it crosses an engine boundary and changes approximately never; the *serviceability
    #: answer* is deliberately not cached, because a merchant editing their map and watching
    #: nothing happen for a TTL is the one failure they will not forgive.
    #:
    #: The cost is stated in the ADR and is real: a newly published store can 404 for up to this
    #: long, and the publish UI says so.
    store_slug_cache_seconds: int = 300


def assert_directory_role_configured(cfg: Settings) -> None:
    """Refuse to start without the bypass reader the anonymous route depends on.

    ADR-0010 §4 and §13, and ADR-0002 §4's rule that the policy stays quiet so the application
    has to be loud. Without a directory role `get_directory_session` yields an ordinary
    application session, the slug lookup binds no tenant, row-level security matches nothing,
    and the service answers every anonymous buyer "we do not deliver to you" with a 200 and no
    log line. There is no runtime symptom to alert on, so the check has to be at boot.

    Fatal in **every** environment, including `local`. `assert_app_role` warns locally because a
    developer on the wrong DSN should not be locked out of their own machine; this one does not,
    because the wrong configuration here produces a service that looks perfectly healthy while
    giving every buyer a wrong answer, and a developer finding that out in review is the cheap
    version of finding it out from a merchant.
    """
    if cfg.db_directory_role:
        return
    raise RuntimeError(
        "storefront refuses to start with no db_directory_role: the anonymous serviceability "
        "check resolves a store slug before any tenant is bound, and without a BYPASSRLS reader "
        "get_directory_session falls back to the application session, which matches zero rows "
        "under row-level security and tells every buyer the store does not deliver to them "
        "(ADR-0010 §4). Configure opsgrids_storefront_directory."
    )


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
