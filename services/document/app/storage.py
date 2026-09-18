"""The only module in the platform that talks to object storage.

Everything else — routes, services, selectors — deals in rows and in keys read off rows. That
containment is deliberate: the day the bucket moves, or a CDN lands in front, or the private
bucket of ADR-0009 §2 gets built, this is the file that changes.

**No client ever holds S3 credentials.** This process does, and it is the only thing that
writes. The browser talks to our API on the write path and to the bucket on the read path,
and never the other way round.

**boto3 is synchronous**, so every call here is wrapped in `anyio.to_thread.run_sync`. The
alternative — an async S3 client — is a second SDK and a second set of retry semantics for
three calls.

Failures are values. A botocore error becomes `UpstreamUnavailableProblem` (503) with a
message that says nothing about buckets, keys or endpoints: a merchant gets "try again", and
the endpoint and the key go to the log with the tenant and the request id.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

import anyio.to_thread
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings, settings
from platform_core.errors import UpstreamUnavailableProblem
from platform_core.logging import get_logger

log = get_logger("document.storage")


class ObjectStore:
    """Put, delete and address objects in the public bucket.

    One instance per process, built lazily — a boto3 client is cheap to hold and expensive to
    build per request, and it is thread-safe for the operations used here.
    """

    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg
        self._client: Any | None = None

    # ----------------------------------------------------------------- the client

    def _build_client(self) -> Any:
        credentials: dict[str, str] = {}
        if self._cfg.s3_access_key and self._cfg.s3_secret_key:
            credentials = {
                "aws_access_key_id": self._cfg.s3_access_key.get_secret_value(),
                "aws_secret_access_key": self._cfg.s3_secret_key.get_secret_value(),
            }
        # Absent credentials are not an error: boto3 falls back to its default chain —
        # instance profile, IRSA, or a local profile — which is how production authenticates
        # (ADR-0009 §13). Requiring them here would mean a static IAM key in production.
        return boto3.client(
            "s3",
            endpoint_url=self._cfg.s3_endpoint_url,
            region_name=self._cfg.s3_region,
            config=Config(
                connect_timeout=self._cfg.s3_connect_timeout_seconds,
                read_timeout=self._cfg.s3_read_timeout_seconds,
                # `standard` mode with 3 attempts is one try and two retries, with jittered
                # backoff (ADR-0009 §10).
                retries={"max_attempts": self._cfg.s3_max_attempts, "mode": "standard"},
                s3={"addressing_style": "path" if self._cfg.s3_force_path_style else "auto"},
                signature_version="s3v4",
            ),
            **credentials,
        )

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    # ----------------------------------------------------------------- operations

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        organization_id: uuid.UUID,
    ) -> None:
        """Write the object. Called **before** the row is committed (ADR-0009 §9).

        The order is the choice of which failure to have. If this raises, the request's
        transaction rolls back and nothing is written — the merchant gets a retryable 503 and
        no orphan row. If the row insert fails after this succeeds, there is an orphan
        object: unreferenced, at an unguessable key, invisible, and sweepable. A library entry
        pointing at nothing is not.
        """
        try:
            await anyio.to_thread.run_sync(
                lambda: self.client.put_object(
                    Bucket=self._cfg.s3_public_bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
            )
        except (ClientError, BotoCoreError) as exc:
            log.error(
                "storage.put.failed",
                organization_id=str(organization_id),
                key=key,
                bucket=self._cfg.s3_public_bucket,
                error=str(exc)[:200],
            )
            raise UpstreamUnavailableProblem(
                "Could not store the image right now. Please try again."
            ) from exc

    async def delete(self, key: str, *, organization_id: uuid.UUID) -> None:
        """Remove the object. Idempotent in the direction that matters.

        S3 `DeleteObject` on a missing key returns 204, so an object that is already gone is
        success rather than an error — which is what lets the purge sweep retry a row whose
        `purged_at` is still null without having to reason about whether the previous run got
        halfway.
        """
        try:
            await anyio.to_thread.run_sync(
                lambda: self.client.delete_object(
                    Bucket=self._cfg.s3_public_bucket,
                    Key=key,
                )
            )
        except (ClientError, BotoCoreError) as exc:
            log.error(
                "storage.delete.failed",
                organization_id=str(organization_id),
                key=key,
                bucket=self._cfg.s3_public_bucket,
                error=str(exc)[:200],
            )
            raise UpstreamUnavailableProblem(
                "Could not take the image down right now. Please try again."
            ) from exc

    # ----------------------------------------------------------------- addressing

    def public_url(self, key: str) -> str:
        """The absolute address a browser fetches, for preview and for support.

        **Not what a merchant's store document stores** — that is `domain.public_path`, and
        the difference is the whole of ADR-0009 §6. This value embeds the current host, so
        persisting it anywhere would turn a future CDN into a data migration over
        customer-authored content.
        """
        return f"{self._cfg.public_asset_base_url.rstrip('/')}/{key}"


@lru_cache(maxsize=1)
def get_object_store() -> ObjectStore:
    """FastAPI dependency. One store per process; overridden wholesale in tests."""
    return ObjectStore(settings())
