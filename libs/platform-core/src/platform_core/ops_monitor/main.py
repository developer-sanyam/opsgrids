"""Wires `state`, `checks`, `mailer` and `server` into the running process.

One 60-second loop (`ops_monitor_poll_interval_seconds`): run every check, feed the results to
`state.evaluate()`, persist the result, send one grouped email for whatever changed this cycle,
touch the heartbeat file, and — once a day — ask `state.evaluate_digest()` whether the dead-man's
switch should fire. The HTTP server (`server.build_app`) runs alongside it as a second task in
the same event loop, reading the same `MonitorRuntime` the loop writes.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from platform_core.logging import configure_logging, get_logger
from platform_core.ops_monitor import checks
from platform_core.ops_monitor.config import OpsMonitorSettings
from platform_core.ops_monitor.mailer import Mailer
from platform_core.ops_monitor.server import MonitorRuntime, build_app
from platform_core.ops_monitor.state import (
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
    name: str, coro: Awaitable[dict[str, Observation]], observations: dict[str, Observation]
) -> None:
    """Run one probe and merge its results into `observations`.

    A probe that raises is reported as one failing observation named after it, rather than
    dropping the cycle's results for every check that probe would have produced — an absent
    check name is a check `state.evaluate()` leaves untouched (looks like nothing happened),
    which is the wrong outcome for "this probe just crashed".
    """
    try:
        observations.update(await coro)
    except Exception as exc:  # a probe's own bug must not sink the whole cycle
        observations[name] = Observation(ok=False, detail=f"probe crashed: {exc!s:.200}")
        log.error("ops_monitor.probe_crashed", probe=name, error=str(exc)[:300])


async def run_one_cycle(
    settings: OpsMonitorSettings,
    *,
    outbox_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    now: datetime,
) -> dict[str, Observation]:
    """Run every configured check and merge the results into one observations mapping."""
    observations: dict[str, Observation] = {}

    await _collect(
        "outbox",
        checks.check_outbox_lag(
            outbox_engine,
            settings.edge_relay_schemas,
            warn_seconds=settings.ops_monitor_outbox_warn_seconds,
            crit_seconds=settings.ops_monitor_outbox_crit_seconds,
            max_attempts=settings.ops_monitor_outbox_max_attempts,
        ),
        observations,
    )
    await _collect(
        "dlq",
        checks.check_dlq_depth(settings.rabbitmq_url, settings.ops_monitor_dlq_queues),
        observations,
    )
    await _collect(
        "api",
        checks.check_api_readiness(
            http_client, parse_api_containers(settings.ops_monitor_api_containers)
        ),
        observations,
    )

    try:
        observations.update(
            checks.check_disk(
                settings.ops_monitor_disk_path,
                min_free_percent=settings.ops_monitor_disk_min_free_percent,
            )
        )
    except OSError as exc:
        observations["disk"] = Observation(ok=False, detail=f"probe crashed: {exc!s:.200}")

    observations.update(
        checks.check_backup_freshness(
            Path(settings.ops_monitor_backup_marker_path),
            max_age_hours=settings.ops_monitor_backup_max_age_hours,
            now=now,
        )
    )
    observations.update(
        await asyncio.to_thread(
            checks.check_cert_expiry,
            settings.ops_monitor_cert_host,
            port=settings.ops_monitor_cert_port,
            sni=settings.edge_host,
            warn_days=settings.ops_monitor_cert_warn_days,
            now=now,
        )
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
    )
    new_state, digest_notification = evaluate_digest(
        new_state, now, digest_hour_utc=settings.ops_monitor_digest_hour_utc
    )
    if digest_notification is not None:
        notifications.append(digest_notification)

    if notifications:
        subject, body = format_email(notifications)
        await mailer.send(subject, body)
        for n in notifications:
            log.info("ops_monitor.notification", check=n.check, kind=n.kind, detail=n.detail)

    runtime.state = new_state
    runtime.last_cycle_at = now
    runtime.last_cycle_error = None
    dump(new_state, Path(settings.ops_monitor_state_path))


def _write_heartbeat(path: Path, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(now.isoformat(), encoding="utf-8")


async def poll_loop(settings: OpsMonitorSettings, runtime: MonitorRuntime, mailer: Mailer) -> None:
    heartbeat_path = Path(settings.ops_monitor_heartbeat_path)
    runtime.state = load(Path(settings.ops_monitor_state_path))

    outbox_engine = create_async_engine(
        settings.outbox_dsn,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
        connect_args={"server_settings": {"application_name": "ops-monitor"}},
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
                runtime.last_cycle_error = str(exc)[:300]
                log.error("ops_monitor.cycle_failed", error=str(exc)[:300])

            # Written every cycle regardless of outcome — this is "the loop is alive",
            # independent of whether any individual check succeeded. The compose healthcheck
            # is `find heartbeat -mmin -3`: three missed cycles at the default interval.
            # Off the event loop: `Path.write_text` is a blocking syscall, however small.
            await asyncio.to_thread(_write_heartbeat, heartbeat_path, now)

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
