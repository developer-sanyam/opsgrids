"""Wires `state`, `checks`, `mailer` and `server` into the running process.

One 60-second loop (`ops_monitor_poll_interval_seconds`): run every check (each bounded by
`asyncio.wait_for`, M4), feed the results to `state.evaluate()`, persist the result, send one
grouped email for whatever changed this cycle, touch the heartbeat file, and — once a day — ask
`state.evaluate_digest()` whether the dead-man's switch should fire. The HTTP server
(`server.build_app`) runs alongside it as a second task in the same event loop, reading the same
`MonitorRuntime` the loop writes.

**M6, delivery is not the same event as detection.** `state.evaluate()`/`evaluate_digest()`
advance a check's real status unconditionally — that is a fact about the world, observed this
cycle, independent of whether anyone gets told about it. But the *bookkeeping* those functions
also advance (`last_notified_at`, `last_digest_date`) exists only to pace *delivery*, and
committing it before delivery has actually succeeded means a transient SMTP outage silently
eats a firing/reminder/digest notification for a full interval. `run_cycle_and_notify` reverts
just that bookkeeping when `mailer.send` fails, up to `ops_monitor_max_immediate_send_retries`
consecutive cycles, before giving up and falling back to the normal cadence — and a rolling cap
(`ops_monitor_max_emails_per_hour`) throttles delivery itself so a genuinely falling-apart box
pages a human once, not once per check per cycle.

**What to send and what to hold back is not decided here** (round 3): `delivery.plan_delivery`
is a pure function that takes the queue, this cycle's notifications, the send history and
`last_fired_at`, and returns the plan; `_deliver` below only attempts it and applies the result.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from platform_core.logging import configure_logging, get_logger
from platform_core.ops_monitor import checks
from platform_core.ops_monitor.config import OpsMonitorSettings
from platform_core.ops_monitor.delivery import (
    SentRecord,
    bounded_requeue,
    is_firing_like,
    plan_delivery,
)
from platform_core.ops_monitor.mailer import Mailer
from platform_core.ops_monitor.redact import summarize_exception
from platform_core.ops_monitor.server import MonitorRuntime, build_app
from platform_core.ops_monitor.state import (
    CheckState,
    MonitorState,
    Notification,
    Observation,
    dump,
    evaluate,
    evaluate_digest,
    load,
)

log = get_logger("ops_monitor")


def parse_api_containers(entries: list[str]) -> dict[str, int]:
    """`name` or `name:port` -> `{name: port}`, defaulting to 8000, every API container's own
    port (`compose.prod.yml`'s x-service-common healthcheck dials the same one)."""
    containers: dict[str, int] = {}
    for entry in entries:
        if ":" in entry:
            name, _, port = entry.partition(":")
            containers[name.strip()] = int(port.strip() or 8000)
        else:
            containers[entry.strip()] = 8000
    return containers


async def _collect(
    name: str,
    coro: Awaitable[dict[str, Observation]],
    observations: dict[str, Observation],
    *,
    timeout_seconds: float,
) -> None:
    """Run one probe, bounded by `timeout_seconds` (M4), and merge its results into
    `observations`.

    A probe that raises — or that outlives its timeout, a hung broker or a locked table being
    exactly the case this bounds — is reported as one failing observation named after it,
    rather than dropping the cycle's results for every check that probe would have produced (an
    absent check name is a check `state.evaluate()` leaves untouched, which looks like nothing
    happened — the wrong outcome for "this probe just hung or crashed"), and never blocks the
    other probes or the heartbeat behind it.
    """
    try:
        observations.update(await asyncio.wait_for(coro, timeout=timeout_seconds))
    except TimeoutError:
        observations[name] = Observation(ok=False, detail=f"timed out after {timeout_seconds:.0f}s")
        log.error("ops_monitor.probe_timed_out", probe=name, timeout_seconds=timeout_seconds)
    except Exception as exc:  # a probe's own bug must not sink the whole cycle
        observations[name] = Observation(
            ok=False, detail=f"probe crashed: {summarize_exception(exc)}"
        )
        log.error("ops_monitor.probe_crashed", probe=name, error=str(exc)[:300])


async def _check_disk_async(path: str, *, min_free_percent: float) -> dict[str, Observation]:
    return await asyncio.to_thread(checks.check_disk, path, min_free_percent=min_free_percent)


async def _check_backup_freshness_async(
    marker_path: Path, *, max_age_hours: float, now: datetime
) -> dict[str, Observation]:
    return await asyncio.to_thread(
        checks.check_backup_freshness, marker_path, max_age_hours=max_age_hours, now=now
    )


async def _check_cert_expiry_async(
    host: str, *, port: int, sni: str, warn_days: int, now: datetime
) -> dict[str, Observation]:
    return await asyncio.to_thread(
        checks.check_cert_expiry, host, port=port, sni=sni, warn_days=warn_days, now=now
    )


async def run_one_cycle(
    settings: OpsMonitorSettings,
    *,
    outbox_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    now: datetime,
) -> dict[str, Observation]:
    """Run every configured check and merge the results into one observations mapping.

    Every probe — outbox, DLQ, API, disk, backup and cert alike (M4) — goes through `_collect`,
    so one hung or crashing check can cost this cycle only its own observation, never the
    others, and never the heartbeat write that proves the loop itself is still alive.
    """
    observations: dict[str, Observation] = {}
    timeout = settings.ops_monitor_probe_timeout_seconds

    await _collect(
        "outbox",
        checks.check_outbox_lag(
            outbox_engine,
            settings.edge_relay_schemas,
            warn_seconds=settings.ops_monitor_outbox_warn_seconds,
            crit_seconds=settings.ops_monitor_outbox_crit_seconds,
            max_attempts=settings.ops_monitor_outbox_max_attempts,
            statement_timeout_seconds=settings.ops_monitor_outbox_statement_timeout_seconds,
        ),
        observations,
        timeout_seconds=timeout,
    )
    await _collect(
        "dlq",
        checks.check_dlq_depth(
            settings.rabbitmq_url,
            settings.ops_monitor_dlq_queues,
            connect_timeout_seconds=settings.ops_monitor_amqp_connect_timeout_seconds,
        ),
        observations,
        timeout_seconds=timeout,
    )
    await _collect(
        "api",
        checks.check_api_readiness(
            http_client, parse_api_containers(settings.ops_monitor_api_containers)
        ),
        observations,
        timeout_seconds=timeout,
    )
    await _collect(
        "disk",
        _check_disk_async(
            settings.ops_monitor_disk_path,
            min_free_percent=settings.ops_monitor_disk_min_free_percent,
        ),
        observations,
        timeout_seconds=timeout,
    )
    await _collect(
        "backup",
        _check_backup_freshness_async(
            Path(settings.ops_monitor_backup_marker_path),
            max_age_hours=settings.ops_monitor_backup_max_age_hours,
            now=now,
        ),
        observations,
        timeout_seconds=timeout,
    )
    await _collect(
        "cert",
        _check_cert_expiry_async(
            settings.ops_monitor_cert_host,
            port=settings.ops_monitor_cert_port,
            sni=settings.edge_host,
            warn_days=settings.ops_monitor_cert_warn_days,
            now=now,
        ),
        observations,
        timeout_seconds=timeout,
    )
    return observations


def format_email(notifications: list[Notification]) -> tuple[str, str]:
    """One subject/body covering every check that changed this cycle — "one email per cycle
    grouping all changed checks", never one email per check."""
    opened = [n for n in notifications if n.kind == "firing"]
    reminders = [n for n in notifications if n.kind == "reminder"]
    resolved = [n for n in notifications if n.kind == "resolved"]
    digest = [n for n in notifications if n.kind == "digest"]

    parts: list[str] = []
    if opened:
        parts.append(f"{len(opened)} new")
    if reminders:
        parts.append(f"{len(reminders)} still firing")
    if resolved:
        parts.append(f"{len(resolved)} resolved")
    if digest:
        parts.append("daily digest")
    subject = f"[opsgrids] ops-monitor: {', '.join(parts) or 'update'}"

    lines: list[str] = []
    for group_name, group in (
        ("OPENED", opened),
        ("STILL FIRING", reminders),
        ("RESOLVED", resolved),
        ("DIGEST", digest),
    ):
        if not group:
            continue
        lines.append(f"-- {group_name} --")
        lines.extend(f"{n.check}: {n.detail}" for n in group)
        lines.append("")
    return subject, "\n".join(lines).strip() + "\n"


def _revert_notification_bookkeeping(
    prev_state: MonitorState, new_state: MonitorState, notifications: list[Notification]
) -> MonitorState:
    """M6: undo only the *delivery* bookkeeping (`last_notified_at`, `last_digest_date`) for a
    cycle whose email did not go out — never a check's real status, which reflects an actual
    observation regardless of whether anyone was told about it. Reverting `last_notified_at`
    back to what it was before this cycle's `evaluate()` call means next cycle's reminder-due
    check sees it as still due, so a failed send is retried immediately rather than waiting a
    full `ops_monitor_reminder_interval_seconds`.

    `resolved` notifications have no timestamp to revert (`evaluate()` already cleared
    `last_notified_at` to `None` on resolution, and a resolved check will never spontaneously
    re-emit a "resolved" notification on its own) — `run_cycle_and_notify`'s pending queue is
    what actually carries an undelivered `resolved` (or `reminder`) forward; this function only
    protects the reminder/digest *cadence* from thinking a cycle it never told anyone about
    already happened.
    """
    reverted_checks = dict(new_state.checks)
    for note in notifications:
        if note.kind in ("firing", "reminder") and note.check in reverted_checks:
            prev_check = prev_state.checks.get(note.check, CheckState())
            reverted_checks[note.check] = replace(
                reverted_checks[note.check], last_notified_at=prev_check.last_notified_at
            )
    last_digest_date = new_state.last_digest_date
    if any(note.kind == "digest" for note in notifications):
        last_digest_date = prev_state.last_digest_date
    return MonitorState(checks=reverted_checks, last_digest_date=last_digest_date)


async def _deliver(
    settings: OpsMonitorSettings,
    runtime: MonitorRuntime,
    mailer: Mailer,
    notifications: list[Notification],
    now: datetime,
) -> list[Notification]:
    """Execute `delivery.plan_delivery`'s plan — this function decides nothing about what to
    send or hold back (round 3: that logic failed review twice as a patch on top of this
    function and was pulled out into its own pure module). All this does is attempt the send
    `plan_delivery` decided on, and apply the result: on success, commit `plan.new_pending` and
    advance `last_fired_at`/`sent_history`; on failure, requeue exactly what was attempted
    (`bounded_requeue`, original `Notification` objects, original `kind`) alongside whatever
    was already held back — same bounded-retry fallback as before (M6) once
    `ops_monitor_max_immediate_send_retries` is exceeded.

    Returns the notifications (from `notifications`, this cycle's fresh ones) whose
    state-machine bookkeeping the caller must revert because they were not delivered this
    cycle — a queued `pending_notifications` entry from an *earlier* cycle already had its own
    bookkeeping reverted back when it was first queued, so it is never in this return value.
    """
    cutoff = now - timedelta(hours=1)
    runtime.sent_history = [r for r in runtime.sent_history if r.at >= cutoff]

    plan = plan_delivery(
        runtime.pending_notifications,
        notifications,
        now,
        runtime.sent_history,
        runtime.last_fired_at,
        settings.ops_monitor_max_emails_per_hour,
    )

    if not plan.to_send:
        runtime.pending_notifications = plan.new_pending
        if notifications:
            log.warning(
                "ops_monitor.notification_capped",
                count=len(notifications),
                pending_total=len(runtime.pending_notifications),
                cap=settings.ops_monitor_max_emails_per_hour,
            )
        return list(notifications)

    sent_checks = {n.check for n in plan.to_send}
    reverted = [n for n in notifications if n.check not in sent_checks]

    subject, body = format_email(plan.to_send)
    delivered = await mailer.send(subject, body)
    for n in plan.to_send:
        log.info("ops_monitor.notification", check=n.check, kind=n.kind, detail=n.detail)

    if delivered:
        runtime.sent_history.append(SentRecord(at=now, exempt=plan.exempt_send))
        runtime.consecutive_send_failures = 0
        runtime.pending_notifications = plan.new_pending
        for n in plan.to_send:
            if is_firing_like(n, runtime.last_fired_at):
                runtime.last_fired_at[n.check] = now
        return reverted

    runtime.consecutive_send_failures += 1
    if runtime.consecutive_send_failures > settings.ops_monitor_max_immediate_send_retries:
        # Stop hammering a broken SMTP config every single cycle — fall back to the normal
        # reminder/digest cadence, and let the next real change try again from a clean slate.
        # Whatever was actually attempted (`plan.to_send`) is accepted as if it had gone out —
        # its bookkeeping is *not* reverted, so this does not retry forever with no way out;
        # only whatever the cap itself was already holding back (`reverted`) still needs it.
        log.error(
            "ops_monitor.notification_retry_exhausted",
            consecutive_failures=runtime.consecutive_send_failures,
        )
        runtime.consecutive_send_failures = 0
        runtime.pending_notifications = []
        return reverted

    # The whole batch failed to send — everything attempted goes back into the queue as the
    # same objects (original kind intact), alongside whatever was already held back.
    runtime.pending_notifications = bounded_requeue(plan.to_send, plan.new_pending)
    return list(notifications)


async def run_cycle_and_notify(
    settings: OpsMonitorSettings,
    runtime: MonitorRuntime,
    mailer: Mailer,
    *,
    outbox_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    now: datetime,
) -> None:
    observations = await run_one_cycle(
        settings, outbox_engine=outbox_engine, http_client=http_client, now=now
    )
    new_state, notifications = evaluate(
        runtime.state,
        observations,
        now,
        pending_threshold=settings.ops_monitor_pending_threshold,
        recovery_threshold=settings.ops_monitor_recovery_threshold,
        reminder_interval=timedelta(seconds=settings.ops_monitor_reminder_interval_seconds),
        flap_window=settings.ops_monitor_flap_window,
        flap_threshold=settings.ops_monitor_flap_threshold,
    )
    new_state, digest_notification = evaluate_digest(
        new_state, now, digest_hour_utc=settings.ops_monitor_digest_hour_utc
    )
    if digest_notification is not None:
        notifications.append(digest_notification)

    if notifications or runtime.pending_notifications:
        to_revert = await _deliver(settings, runtime, mailer, notifications, now)
        if to_revert:
            new_state = _revert_notification_bookkeeping(runtime.state, new_state, to_revert)

    runtime.state = new_state
    runtime.last_cycle_at = now
    runtime.last_cycle_error = None
    _dump_safely(new_state, Path(settings.ops_monitor_state_path))


def _dump_safely(state: MonitorState, path: Path) -> None:
    """B2: a state write that fails (an unwritable directory, a full disk) is logged, never
    fatal — the loop keeps running on the in-memory state `runtime.state` already holds
    (assigned by the caller before this is called), and the next successful write catches up.
    A monitor that crashes because it cannot persist its own bookkeeping is worse than one that
    persists nothing until the underlying problem is fixed — usually the state directory not
    yet being owned by this container's uid, see `compose.prod.yml`'s comment on `ops-monitor`
    and `remote.sh`'s `ensure_ops_monitor_state_dir`."""
    try:
        dump(state, path)
    except OSError as exc:
        log.error("ops_monitor.state_write_failed", path=str(path), error=str(exc)[:300])


def _write_heartbeat(path: Path, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(now.isoformat(), encoding="utf-8")


def _write_heartbeat_safely(path: Path, now: datetime) -> None:
    """B2: same reasoning as `_dump_safely` — a heartbeat write failing must not be what takes
    the process down; it already tells the container's own healthcheck that something is
    wrong (a stale heartbeat) without also killing the loop trying to check anything else."""
    try:
        _write_heartbeat(path, now)
    except OSError as exc:
        log.error("ops_monitor.heartbeat_write_failed", path=str(path), error=str(exc)[:300])


async def poll_loop(settings: OpsMonitorSettings, runtime: MonitorRuntime, mailer: Mailer) -> None:
    heartbeat_path = Path(settings.ops_monitor_heartbeat_path)
    runtime.state = load(Path(settings.ops_monitor_state_path))

    outbox_engine = create_async_engine(
        settings.outbox_dsn,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
        # m9 (round 3): below `ops_monitor_probe_timeout_seconds` on purpose. Many more
        # schemas than `pool_size + max_overflow` checking concurrently (`checks.py`'s own
        # `asyncio.gather`) means some of them wait for a connection to free up; left at
        # SQLAlchemy's 30s default, that wait alone could outlast the whole probe's timeout
        # budget, and `_check_one_outbox_schema`'s own `asyncio.wait_for` (bounded by
        # `ops_monitor_outbox_statement_timeout_seconds`) would never get the chance to turn
        # it into a clean per-schema failing observation instead.
        pool_timeout=settings.ops_monitor_outbox_statement_timeout_seconds,
        connect_args={
            "server_settings": {"application_name": "ops-monitor"},
            # M4: bound the TCP/handshake connect itself — a Postgres that accepts the socket
            # and then never answers (a host-level network problem, not a query-level one)
            # must not hang past this, independent of the per-query statement_timeout below.
            "timeout": settings.ops_monitor_probe_timeout_seconds,
        },
    )
    http_client = httpx.AsyncClient()

    try:
        while True:
            now = datetime.now(UTC)
            try:
                await run_cycle_and_notify(
                    settings,
                    runtime,
                    mailer,
                    outbox_engine=outbox_engine,
                    http_client=http_client,
                    now=now,
                )
            except Exception as exc:  # the loop itself must survive one bad cycle
                # last_cycle_error surfaces on /status (round 2's redaction) — a summary, never
                # the raw message; the full text still reaches this process's own log line.
                runtime.last_cycle_error = summarize_exception(exc)
                log.error("ops_monitor.cycle_failed", error=str(exc)[:300])

            # Written every cycle regardless of outcome — this is "the loop is alive",
            # independent of whether any individual check succeeded. The compose healthcheck
            # is `find heartbeat -mmin -3`: three missed cycles at the default interval.
            # Off the event loop: `Path.write_text` is a blocking syscall, however small.
            await asyncio.to_thread(_write_heartbeat_safely, heartbeat_path, now)

            await asyncio.sleep(settings.ops_monitor_poll_interval_seconds)
    finally:
        await http_client.aclose()
        await outbox_engine.dispose()


async def run_forever(settings: OpsMonitorSettings) -> None:
    runtime = MonitorRuntime()
    mailer = Mailer(settings)
    app = build_app(runtime, mailer)

    server_config = uvicorn.Config(
        app,
        host="0.0.0.0",  # noqa: S104 — the monitor's own port, never published (compose gives it no host port)
        port=settings.ops_monitor_port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)

    loop_task = asyncio.create_task(poll_loop(settings, runtime, mailer), name="ops-monitor-loop")
    server_task = asyncio.create_task(server.serve(), name="ops-monitor-http")

    stop_requested = asyncio.Event()
    running_loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            running_loop.add_signal_handler(sig, stop_requested.set)
            installed.append(sig)

    log.info("ops_monitor.started", port=settings.ops_monitor_port)
    stop_task = asyncio.create_task(stop_requested.wait(), name="ops-monitor-stop-wait")
    try:
        await asyncio.wait({loop_task, server_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        server.should_exit = True
        for task in (loop_task, server_task, stop_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(loop_task, server_task, stop_task, return_exceptions=True)
        for sig in installed:
            running_loop.remove_signal_handler(sig)
    log.info("ops_monitor.stopped")


def main() -> None:
    settings = OpsMonitorSettings()
    configure_logging(
        settings.service_name, level=settings.log_level, json_output=settings.log_json
    )
    if not settings.smtp_configured:
        log.warning(
            "ops_monitor.smtp_not_configured",
            hint="OPS_ALERT_EMAIL/SMTP_HOST/EMAIL_FROM unset — alerts are logged only",
        )
    asyncio.run(run_forever(settings))


if __name__ == "__main__":
    main()
