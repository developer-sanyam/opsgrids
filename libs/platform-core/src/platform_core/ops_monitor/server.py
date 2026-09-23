"""The monitor's own `/metrics` and `/status` (M3) — its own port, its own tiny FastAPI app,
never behind Caddy or a gateway (`compose.prod.yml` gives `ops-monitor` no published port and
puts it on `back` only; see the module docstring in `__init__.py`).

Deliberately not built through `platform_core.app.create_app`: that factory prefixes every
router under `/api/v1` and wires a database/Redis lifespan this process does not have — `/status`
and `/metrics` belong at the root, and this process's "database" is the outbox engine `main.py`
already owns for the checks themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from platform_core.metrics import Sample, render
from platform_core.ops_monitor.mailer import Mailer
from platform_core.ops_monitor.state import MonitorState, Status


@dataclass
class MonitorRuntime:
    """The one piece of mutable, shared state between the poll loop and the HTTP server —
    updated by `main.py` at the end of every cycle, read by the routes below. A plain
    dataclass rather than a lock-guarded structure: one asyncio event loop, one writer, and
    FastAPI route handlers here do no `await` between reading two of its fields, so there is
    no interleaving to protect against."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    state: MonitorState = field(default_factory=MonitorState)
    last_cycle_at: datetime | None = None
    last_cycle_error: str | None = None


_HELP = {
    "ops_monitor_check_status": (
        "1 if the check is OK, 0 otherwise (pending counts as OK — not yet firing)."
    ),
    "ops_monitor_check_firing": "1 if the check is currently firing.",
    "ops_monitor_up": "1 — presence of this series is itself the liveness signal.",
    "ops_monitor_last_cycle_timestamp_seconds": "Unix time of the last completed poll cycle.",
    "ops_monitor_smtp_configured": "1 if SMTP_HOST/OPS_ALERT_EMAIL/EMAIL_FROM are all set.",
}
_TYPES = {
    "ops_monitor_check_status": "gauge",
    "ops_monitor_check_firing": "gauge",
    "ops_monitor_up": "gauge",
    "ops_monitor_last_cycle_timestamp_seconds": "gauge",
    "ops_monitor_smtp_configured": "gauge",
}


def render_metrics(runtime: MonitorRuntime, mailer: Mailer) -> str:
    samples: list[Sample] = [Sample("ops_monitor_up", 1.0)]
    for name, check_state in runtime.state.checks.items():
        ok = 1.0 if check_state.status is not Status.FIRING else 0.0
        firing = 1.0 if check_state.status is Status.FIRING else 0.0
        samples.append(Sample("ops_monitor_check_status", ok, {"check": name}))
        samples.append(Sample("ops_monitor_check_firing", firing, {"check": name}))
    if runtime.last_cycle_at is not None:
        samples.append(
            Sample(
                "ops_monitor_last_cycle_timestamp_seconds",
                runtime.last_cycle_at.timestamp(),
            )
        )
    samples.append(Sample("ops_monitor_smtp_configured", 1.0 if mailer.configured else 0.0))
    return render(samples, help_text=_HELP, metric_types=_TYPES)


def render_status(runtime: MonitorRuntime, mailer: Mailer) -> dict[str, Any]:
    return {
        "started_at": runtime.started_at.isoformat(),
        "last_cycle_at": runtime.last_cycle_at.isoformat() if runtime.last_cycle_at else None,
        "last_cycle_error": runtime.last_cycle_error,
        "checks": {
            name: {
                "status": cs.status.value,
                "detail": cs.last_detail,
                "since": cs.since,
                "consecutive_fails": cs.consecutive_fails,
                "consecutive_passes": cs.consecutive_passes,
            }
            for name, cs in sorted(runtime.state.checks.items())
        },
        **mailer.status(),
    }


def build_app(runtime: MonitorRuntime, mailer: Mailer) -> FastAPI:
    app = FastAPI(title="ops-monitor", docs_url=None, openapi_url=None)

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        return render_metrics(runtime, mailer)

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return render_status(runtime, mailer)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "ops-monitor"}

    return app
