"""The edge box's own monitor (C10, R7, R37, R12).

One always-on container, `ops-monitor`, running `python -m platform_core.ops_monitor` in the
already-built `product` image — the same trick `edge_relay` uses to avoid a 27th image. It has
no schema of its own: everything it needs to know is either read from another service's schema
through the `opsgrids_outbox` role (outbox lag), asked of the broker directly (DLQ depth), asked
of another container over plain HTTP (`/health/ready`), or read off the box's own filesystem
(disk, the backup marker, its own persisted state). See `RUNBOOK.md`'s "alerts and what each
means" for what a human does when one of these fires.

Module layout, in the order a reader probably wants them:

* `state` — the pure state machine (M1): `evaluate()` takes the previous persisted state, this
  cycle's observations and the clock, and returns the next state plus the notifications this
  cycle produced. No I/O; this is what the unit tests exercise directly.
* `config` — `OpsMonitorSettings`, thresholds and every `OPS_MONITOR_*`/`SMTP_*` knob.
* `checks` — M2: the actual probes (outbox lag, DLQ depth, API readiness, disk, backup
  freshness, certificate expiry), each returning `Observation`s keyed by check name.
* `mailer` — M3: direct SMTP (stdlib `smtplib`, off the event loop via a thread), deliberately
  bypassing notification-service — that path is one of the things being monitored.
* `metrics` — hand-written Prometheus text exposition, no new dependency.
* `server` — the monitor's own `/metrics` and `/status`, on its own port, never behind Caddy.
* `main` — wires all of the above into the 60s poll loop, the heartbeat file and the daily
  digest.
"""

from __future__ import annotations
