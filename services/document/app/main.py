"""Entry point for the document service."""

from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.cron import PurgeSweep
from app.models import OutboxEvent
from app.storage import get_object_store, get_orphan_scanner
from platform_core.app import create_app
from platform_core.db import (
    assert_outbox_role,
    bulkhead_scope,
    create_bulkhead_engine,
    get_outbox_engine,
    get_outbox_sessionmaker,
)
from platform_core.logging import get_logger
from platform_core.supervise import (
    register_sweep_health,
    start_background_sweep,
    stop_background_sweep,
)

log = get_logger("document.main")


async def _start_purge_sweep(app: FastAPI) -> None:
    """Starts the purge sweep as a supervised background task inside this process, when
    `Settings.run_sweeps_in_api` is on (the edge box: `document-cron` no longer exists as its
    own container — the memory-headroom slice's fix round 2; see
    `operations.app.config.Settings.run_sweeps_in_api`'s own comment for the full argument).
    Off by default everywhere else; `bin/cron` still runs it standalone for local debugging.

    **The orphan scan stays off here regardless of `Settings.orphan_sweep_enabled`** (review
    finding: this is a tenancy boundary, not a convenience default). ADR-0009 §3's whole
    argument for the request-handling `document` API never holding
    `DOCUMENT_S3_ORPHAN_ACCESS_KEY`/`_SECRET_KEY` is that a compromised or merely buggy API
    process then cannot enumerate the bucket at all — `s3:ListBucket` is exactly what the
    orphan scanner needs and the row-purge path never does. Running the orphan scan inside this
    same process would mean putting that credential in this container's own environment, where
    it is readable by anything that can reach `/proc/1/environ` on it, permanently — not only
    while a sweep tick happens to be using it, and not only when `orphan_sweep_enabled` happens
    to be true, since compose's `environment:` block is static regardless of a runtime flag.
    That is strictly worse than today's split, where the credential is simply absent from this
    process's environment. The retention purge (`self._scanner` is only ever read when
    `orphan_enabled` is true — see `PurgeSweep.tick`) carries no such risk: it deletes only rows
    this same process's own write-scoped `S3_ACCESS_KEY`/`_SECRET_KEY` already reach, exactly
    what `document-cron` did. An operator who genuinely needs the orphan scan run still gets it
    the way the codebase already treats it — deliberately, by hand: `docker compose run --rm
    --no-deps document ./bin/cron` with the orphan credentials supplied for that one invocation,
    never as an always-on setting on the request-serving container.

    `init_outbox_engine` is already called by `create_app`'s shared lifespan (`document` passes
    its own `outbox_model` for `/health/outbox`) — only the role assertion is this sweep's own
    responsibility, the same split `app/cron.py`'s standalone `main()` makes.
    """
    app.state.purge_sweep_task = None
    app.state.sweep_engine = None
    cfg = settings()
    if not cfg.run_sweeps_in_api:
        return
    try:
        await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
    except Exception:
        # The upload API must not fail to start over the purge sweep's precondition (round 3,
        # LOW) — see `operations.app.main._start_reconcile_sweep`. Skip only the sweep, at error.
        log.exception("document.sweep.not_started", reason="outbox role precondition failed")
        register_sweep_health("document.sweep").record_failure()
        return
    # A one-connection engine of its own: some purge transactions hold their connection across
    # the object-store delete, and must not take one of the upload path's (`create_bulkhead_
    # engine`).
    engine = create_bulkhead_engine(cfg, name="sweep")
    app.state.sweep_engine = engine
    sweep = PurgeSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=bulkhead_scope(engine),
        store=get_object_store(),
        scanner=get_orphan_scanner(),
        retention=timedelta(days=cfg.asset_purge_retention_days),
        orphan_enabled=False,
        orphan_dry_run=cfg.orphan_sweep_dry_run,
        orphan_min_age=timedelta(hours=cfg.orphan_sweep_min_age_hours),
        orphan_max_deletes=cfg.orphan_sweep_max_deletes_per_tick,
    )
    app.state.purge_sweep_task = start_background_sweep(sweep.run(), name="document.sweep")


async def _stop_purge_sweep(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "purge_sweep_task", None))
    engine = getattr(app.state, "sweep_engine", None)
    if engine is not None:
        await engine.dispose()


app = create_app(
    settings(),
    title="Document service",
    description=(
        "The platform's **public asset store** (ADR-0009), not a document vault. A merchant "
        "uploads an image; it is decoded, downscaled and re-encoded, and written to a bucket "
        "that is world-readable in its entirety under a key of two UUIDs. The reference it "
        "returns never expires, because nothing about it is signed — which is the single "
        "property the design exists to get: a presigned URL stored inside a merchant's store "
        "document would serve AccessDenied a week later, with nothing failing on our side. "
        "This service never serves image bytes and offers no way to store a private object; "
        "the private half is named and unbuilt, so the failure mode for a confidential file "
        "is a missing feature rather than a misplaced payslip."
    ),
    routers=[router],
    on_startup=_start_purge_sweep,
    on_shutdown=_stop_purge_sweep,
    # Redis is a declared dependency because the upload route is rate limited, and the limiter
    # fails closed by design — an unverifiable limit must not become an amplifier.
    redis=True,
    outbox_model=OutboxEvent,
)
