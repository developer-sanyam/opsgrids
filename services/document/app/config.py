"""Configuration for the document service.

The object-storage block is ADR-0009 §13's dev/prod parity table, expressed as fields. Two
of its choices are load-bearing and are not conveniences:

**The credentials are optional.** If `s3_access_key` were required, production would get a
long-lived static IAM key by construction — nobody restructures settings under a deadline.
Left optional, boto3 falls back to its default credential chain (instance profile, IRSA, a
local profile), which is how production should authenticate. One `| None` here buys the
absence of a key-rotation policy forever.

**`s3_endpoint_url` defaults to unset.** boto3 derives the AWS endpoint from the region;
pinning a hostname in production would be pinning it for no reason. Development sets it to
the local MinIO in `.env`.
"""

from __future__ import annotations

from pydantic import SecretStr

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "document"
    db_schema: str = "document"
    #: Its own NOBYPASSRLS role, granted DML on the `document` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_document"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"

    # --- object storage (ADR-0009 §13) --------------------------------------------
    #: MinIO locally; **unset** in production, where boto3 derives it from the region.
    s3_endpoint_url: str | None = None
    #: MinIO ignores it, boto3 requires one.
    s3_region: str = "us-east-1"
    #: Optional on purpose — see the module docstring. Unset in production.
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    #: Public-read **in its entirety** (ADR-0009 §2). Nothing that is not intended to be
    #: world-readable is ever written here. The private half is named and unbuilt.
    s3_public_bucket: str = "opsgrids-public"
    #: MinIO serves path-style (`host/bucket/key`) unless wildcard DNS is configured; AWS
    #: prefers virtual-hosted style.
    s3_force_path_style: bool = True
    #: What `AssetOut.public_path` resolves against — the bucket origin in dev, the CDN
    #: origin in production. Changing it moves every image with no data migration, which is
    #: the entire payoff of storing a root-relative path (ADR-0009 §6).
    public_asset_base_url: str = "http://localhost:9100/opsgrids-public"

    #: ADR-0009 §10's timeout table. The 20 s read budget is for 5 MiB of body, not for a
    #: round trip, and 3 retries in boto3's `standard` mode is 2 retries after the first try.
    s3_connect_timeout_seconds: float = 3.0
    s3_read_timeout_seconds: float = 20.0
    s3_max_attempts: int = 3

    # --- upload limits and quota (ADR-0009 §5, §7) --------------------------------
    #: 5 MiB, and the reason it is not 25 is the gateway, not this service: `_forward`
    #: buffers whole request and response bodies, so the in-flight cost of an upload is
    #: roughly twice its size in gateway RAM times concurrency (ADR-0009 §5).
    asset_max_upload_bytes: int = 5 * 1024 * 1024
    #: Hard per-organization ceiling, enforced at upload inside the insert's own transaction.
    #: There is deliberately no per-organization override in this slice: raising it raises it
    #: for everyone. The per-org override arrives as a `catalog` Feature when storage is
    #: priced (ADR-0009 §7).
    asset_quota_bytes: int = 2 * 1024 * 1024 * 1024
    asset_quota_count: int = 5_000
    #: Uploads per organization per minute. The one route in this service that does CPU work
    #: (ADR-0009 §11: the re-encode is what saturates first, at roughly four concurrent
    #: uploads per API process), so it is the one route with a limit in front of it.
    asset_upload_rate_limit_per_minute: int = 60


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
