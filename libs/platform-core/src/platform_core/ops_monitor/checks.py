"""The probes (M2). Each returns `{check_name: Observation}` for zero or more checks; `main.py`
merges every check's dict into one `observations` mapping and hands it to `state.evaluate()`.

None of these retry or debounce — that is `state.py`'s job (the pending/reminder/recovery
thresholds). A probe here reports exactly what it saw this cycle, once.

**What this covers, and what it deliberately does not** (documented here because the audit
that started this slice asked for it named, not implied):

* Outbox lag, DLQ depth, API readiness, disk, backup freshness and certificate expiry are all
  covered below.
* **Inventory reconcile drift (`operations-cron`'s nightly sweep, ADR-0014 §3) is NOT covered
  as its own check.** It already writes an `inventory_discrepancies` row, logs
  `inventory.reconcile.drift` and emits `operations.inventory.inconsistency_detected` on the
  outbox — which this monitor's outbox-lag check *does* watch (a `operations` schema listed in
  `EDGE_RELAY_SCHEMAS`), so a drift event that cannot be delivered shows up as outbox lag. What
  is not covered is drift *detection itself having stopped running* — that would need a
  privileged read of `operations.inventory_discrepancies` or a heartbeat table of its own, and
  granting this monitor a role scoped into a product schema is a bigger, separate decision than
  this slice's box-level alerting. Left as a known gap, not silently assumed away.
* **`operations-cron`'s own liveness** (is the sweep container running at all) is likewise not
  covered by a dedicated check — `docker compose ps` is still the tool for "is the process up",
  the same as every other background container on this box (RUNBOOK.md's triage list). What
  this monitor adds on top is the two things that go wrong *quietly* while a container keeps
  running: its outbox filling, and (for `document-cron`/backup) a sweep that runs but stops
  actually finishing its job.
"""

from __future__ import annotations

import shutil
import socket
import ssl
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aio_pika
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from platform_core.edge_relay import SchemaSpec
from platform_core.logging import get_logger
from platform_core.ops_monitor.state import Observation

log = get_logger("ops_monitor")


async def check_outbox_lag(
    engine: AsyncEngine,
    schemas: Iterable[SchemaSpec],
    *,
    warn_seconds: float,
    crit_seconds: float,
    max_attempts: int,
) -> dict[str, Observation]:
    """One `outbox.<schema>` observation per configured schema.

    A schema with no `outbox` table yet (a product profile that is off on this box) is
    skipped entirely — not reported ok, not reported failing, simply absent from the return
    value this cycle, the same "a profile that is off just has no table" tolerance
    `edge_relay.py` already has for the identical situation.
    """
    results: dict[str, Observation] = {}
    async with engine.connect() as conn:
        for spec in schemas:
            schema = spec.schema
            exists = (
                await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{schema}.outbox"})
            ).scalar()
            if exists is None:
                continue
            try:
                # schema is this monitor's own configuration (EDGE_RELAY_SCHEMAS), never
                # request input — the same trust boundary tools/outbox.py's identical query has.
                query = (
                    "SELECT "  # noqa: S608
                    "count(*) FILTER (WHERE published_at IS NULL), "
                    "extract(epoch FROM now() - min(created_at) "
                    "  FILTER (WHERE published_at IS NULL)), "
                    "count(*) FILTER (WHERE published_at IS NULL AND attempts >= :cap) "
                    f'FROM "{schema}".outbox'
                )
                pending, oldest, exhausted = (
                    await conn.execute(text(query), {"cap": max_attempts})
                ).one()
            except Exception as exc:
                results[f"outbox.{schema}"] = Observation(
                    ok=False, detail=f"query failed: {exc!s:.200}"
                )
                continue
            oldest_seconds = float(oldest) if oldest is not None else 0.0
            pending_count = int(pending or 0)
            exhausted_count = int(exhausted or 0)
            detail = (
                f"pending={pending_count} oldest={oldest_seconds:.0f}s exhausted={exhausted_count}"
            )
            if exhausted_count > 0:
                ok = False
                detail = f"{exhausted_count} exhausted row(s) — {detail}"
            elif oldest_seconds >= crit_seconds:
                ok = False
                detail = f"oldest {oldest_seconds:.0f}s >= crit {crit_seconds:.0f}s — {detail}"
            elif oldest_seconds >= warn_seconds:
                ok = False
                detail = f"oldest {oldest_seconds:.0f}s >= warn {warn_seconds:.0f}s — {detail}"
            else:
                ok = True
            results[f"outbox.{schema}"] = Observation(ok=ok, detail=detail)
    return results


async def check_dlq_depth(amqp_url: str, queue_names: Iterable[str]) -> dict[str, Observation]:
    """One `dlq.<queue>` observation per configured consumer queue, checking `{queue}.dlq`.

    A passive `queue_declare` on a queue that does not exist yet closes the channel it was
    issued on (that is plain AMQP, not a bug) — so a fresh channel is opened for every queue,
    and "does not exist yet" is reported ok (a consumer that has never dead-lettered anything
    looks exactly like a consumer that has never been deployed, and neither is an alarm).
    """
    results: dict[str, Observation] = {}
    try:
        connection = await aio_pika.connect_robust(amqp_url)
    except Exception as exc:
        detail = f"amqp connect failed: {exc!s:.200}"
        return {f"dlq.{q}": Observation(ok=False, detail=detail) for q in queue_names}

    async with connection:
        for queue_name in queue_names:
            dlq_name = f"{queue_name}.dlq"
            channel = await connection.channel()
            try:
                queue = await channel.declare_queue(dlq_name, durable=True, passive=True)
                depth = queue.declaration_result.message_count or 0
                results[f"dlq.{queue_name}"] = Observation(
                    ok=depth == 0, detail=f"{dlq_name}: {depth} message(s) parked"
                )
            except aio_pika.exceptions.ChannelClosed:
                results[f"dlq.{queue_name}"] = Observation(
                    ok=True, detail=f"{dlq_name}: not declared yet"
                )
            except Exception as exc:
                results[f"dlq.{queue_name}"] = Observation(
                    ok=False, detail=f"{dlq_name}: check failed: {exc!s:.200}"
                )
            finally:
                if not channel.is_closed:
                    await channel.close()
    return results


async def check_api_readiness(
    client: httpx.AsyncClient, containers: Mapping[str, int]
) -> dict[str, Observation]:
    """One `api.<name>` observation per configured container, `GET http://<name>:<port>/health/ready`.

    Hysteresis (3 consecutive fails before this fires) is `state.py`'s job, not this
    function's — it reports one HTTP call's outcome, nothing more.
    """
    results: dict[str, Observation] = {}
    for name, port in containers.items():
        url = f"http://{name}:{port}/health/ready"
        try:
            response = await client.get(url, timeout=5.0)
            body: dict[str, Any] = {}
            if response.headers.get("content-type", "").startswith("application/json"):
                body = response.json()
            ok = response.status_code == 200 and body.get("status") == "ok"
            results[f"api.{name}"] = Observation(
                ok=ok, detail=f"HTTP {response.status_code} {body.get('status', '')}".strip()
            )
        except Exception as exc:
            results[f"api.{name}"] = Observation(ok=False, detail=f"{url}: {exc!s:.200}")
    return results


def check_disk(path: str, *, min_free_percent: float) -> dict[str, Observation]:
    usage = shutil.disk_usage(path)
    free_percent = (usage.free / usage.total * 100) if usage.total else 0.0
    ok = free_percent >= min_free_percent
    detail = f"{free_percent:.1f}% free of {usage.total // (1024**3)}G at {path}"
    return {"disk": Observation(ok=ok, detail=detail)}


def check_backup_freshness(
    marker_path: Path, *, max_age_hours: float, now: datetime | None = None
) -> dict[str, Observation]:
    """`backup.sh` writes `marker_path` with an ISO-8601 UTC timestamp at the end of a fully
    successful run (database + certificates off-box, and object storage too when it runs).

    A missing marker is its **own** state, deliberately not a failure: a box stood up before
    `BACKUP_S3_*` is configured, or before this monitor shipped, has never had a reason to
    write one, and alarming on that would be a false positive on day one of every box's life.
    It is still visible — `/status` shows the distinct detail string below — just not alerted
    on until a backup has actually run at least once.
    """
    now = now or datetime.now(UTC)
    if not marker_path.exists():
        return {
            "backup": Observation(
                ok=True, detail="no marker yet — backup.sh has not completed successfully"
            )
        }
    try:
        stamp = datetime.fromisoformat(marker_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        return {"backup": Observation(ok=False, detail=f"unreadable marker: {exc!s:.200}")}
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    age_hours = (now - stamp).total_seconds() / 3600
    ok = age_hours <= max_age_hours
    detail = f"last good backup {age_hours:.1f}h ago (limit {max_age_hours:.0f}h)"
    return {"backup": Observation(ok=ok, detail=detail)}


def _handshake_not_after(host: str, port: int, *, sni: str, timeout: float = 5.0) -> datetime:
    ctx = ssl.create_default_context()
    with (
        socket.create_connection((host, port), timeout=timeout) as sock,
        ctx.wrap_socket(sock, server_hostname=sni) as tls,
    ):
        cert = tls.getpeercert()
    if not cert or "notAfter" not in cert:
        raise ValueError("certificate carried no notAfter field")
    not_after = str(cert["notAfter"])
    return datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)


def check_cert_expiry(
    host: str,
    *,
    port: int,
    sni: str,
    warn_days: int,
    now: datetime | None = None,
) -> dict[str, Observation]:
    """A real TLS handshake to `host:port` with SNI `sni` — Caddy on `front`, the same shape
    ADR-0012 §6's own certificate probe uses. Blocking; call through `asyncio.to_thread`."""
    if not sni:
        return {"cert": Observation(ok=True, detail="EDGE_HOST unset — check skipped")}
    now = now or datetime.now(UTC)
    try:
        not_after = _handshake_not_after(host, port, sni=sni)
    except Exception as exc:
        detail = f"handshake to {host}:{port} failed: {exc!s:.200}"
        return {"cert": Observation(ok=False, detail=detail)}
    days_left = (not_after - now).total_seconds() / 86400
    ok = days_left >= warn_days
    detail = f"{days_left:.1f}d until expiry ({not_after.date().isoformat()}) for {sni}"
    return {"cert": Observation(ok=ok, detail=detail)}
