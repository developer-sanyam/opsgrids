"""The probes (M2). Each returns `{check_name: Observation}` for zero or more checks; `main.py`
merges every check's dict into one `observations` mapping and hands it to `state.evaluate()`.

None of these retry or debounce — that is `state.py`'s job (the pending/reminder/recovery
thresholds). A probe here reports exactly what it saw this cycle, once.

**Concurrency (m5).** `check_outbox_lag`, `check_dlq_depth` and `check_api_readiness` each
probe several names (schemas, queues, containers) in one call. Every name is checked
concurrently via `asyncio.gather`, each wrapped in its own `asyncio.wait_for` — so ten schemas
finish in about as long as the slowest one, not the sum of all ten, and a single hung name
times out by itself, naming itself, rather than consuming the whole check's share of
`main._collect`'s outer timeout and starving the names after it in the same call.

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

import asyncio
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
from platform_core.ops_monitor.redact import summarize_exception
from platform_core.ops_monitor.state import Observation

log = get_logger("ops_monitor")


async def _check_one_outbox_schema(
    engine: AsyncEngine,
    spec: SchemaSpec,
    *,
    warn_seconds: float,
    crit_seconds: float,
    max_attempts: int,
    statement_timeout_seconds: float,
) -> tuple[str, Observation | None]:
    """One schema's lag, or `None` when it has no `outbox` table yet (see `check_outbox_lag`).

    Its own **read-only** transaction with `SET LOCAL statement_timeout` (m11/M4):
    `opsgrids_outbox` also holds `UPDATE` (it has to, for the relay this monitor reuses the
    role of), but this check never issues one, and declaring the transaction read-only means a
    bug here cannot write even by accident. The statement timeout means a locked `outbox` table
    (a stuck relay, a long migration) costs this one schema a timed-out observation rather than
    holding the connection open indefinitely.
    """
    schema = spec.schema
    timeout_ms = int(statement_timeout_seconds * 1000)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
            exists = (
                await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{schema}.outbox"})
            ).scalar()
            if exists is None:
                return f"outbox.{schema}", None
            # schema is this monitor's own configuration (EDGE_RELAY_SCHEMAS), never request
            # input — the same trust boundary tools/outbox.py's identical query has, and
            # validated against a plain-identifier pattern at config load
            # (`config._validate_identifiers`).
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
    except TimeoutError:
        return f"outbox.{schema}", Observation(
            ok=False, detail=f"{schema}: timed out after {statement_timeout_seconds:.0f}s"
        )
    except Exception as exc:
        return f"outbox.{schema}", Observation(
            ok=False, detail=f"{schema}: query failed: {summarize_exception(exc)}"
        )

    oldest_seconds = float(oldest) if oldest is not None else 0.0
    pending_count = int(pending or 0)
    exhausted_count = int(exhausted or 0)
    detail = f"pending={pending_count} oldest={oldest_seconds:.0f}s exhausted={exhausted_count}"
    if exhausted_count > 0:
        ok = False
        detail = f"{exhausted_count} exhausted row(s) — {detail}"
    elif oldest_seconds >= crit_seconds:
        ok = False
        detail = f"oldest {oldest_seconds:.0f}s >= crit {crit_seconds:.0f}s — {detail}"
    elif oldest_seconds >= warn_seconds:
        # Warn is visible on /status and in this Observation's detail, but it is NOT a
        # failing observation (M7): only `crit` opens a check and pages anyone. A warn that
        # paged would make "the outbox is a little slow" indistinguishable from "the relay is
        # dead", and the crit threshold exists precisely to be the one that does.
        ok = True
        detail = f"warn: oldest {oldest_seconds:.0f}s >= warn {warn_seconds:.0f}s — {detail}"
    else:
        ok = True
    return f"outbox.{schema}", Observation(ok=ok, detail=detail)


async def check_outbox_lag(
    engine: AsyncEngine,
    schemas: Iterable[SchemaSpec],
    *,
    warn_seconds: float,
    crit_seconds: float,
    max_attempts: int,
    statement_timeout_seconds: float = 10.0,
) -> dict[str, Observation]:
    """One `outbox.<schema>` observation per configured schema, checked concurrently (m5).

    A schema with no `outbox` table yet (a product profile that is off on this box) is
    skipped entirely — not reported ok, not reported failing, simply absent from the return
    value this cycle, the same "a profile that is off just has no table" tolerance
    `edge_relay.py` already has for the identical situation.
    """
    pairs = await asyncio.gather(
        *(
            _check_one_outbox_schema(
                engine,
                spec,
                warn_seconds=warn_seconds,
                crit_seconds=crit_seconds,
                max_attempts=max_attempts,
                statement_timeout_seconds=statement_timeout_seconds,
            )
            for spec in schemas
        )
    )
    return {name: obs for name, obs in pairs if obs is not None}


async def _check_one_dlq_queue(
    connection: aio_pika.abc.AbstractConnection, queue_name: str, *, timeout_seconds: float
) -> tuple[str, Observation]:
    """One queue's DLQ depth, or `None` — never; unlike the outbox check this always returns
    an `Observation`, because "does not exist yet" is itself a meaningful, ok result rather
    than "not applicable"."""
    dlq_name = f"{queue_name}.dlq"
    channel = await connection.channel()
    try:
        queue = await asyncio.wait_for(
            channel.declare_queue(dlq_name, durable=True, passive=True), timeout=timeout_seconds
        )
        depth = queue.declaration_result.message_count or 0
        return f"dlq.{queue_name}", Observation(
            ok=depth == 0, detail=f"{dlq_name}: {depth} message(s) parked"
        )
    except aio_pika.exceptions.ChannelClosed:
        return f"dlq.{queue_name}", Observation(ok=True, detail=f"{dlq_name}: not declared yet")
    except TimeoutError:
        return f"dlq.{queue_name}", Observation(
            ok=False, detail=f"{dlq_name}: timed out after {timeout_seconds:.0f}s"
        )
    except Exception as exc:
        return f"dlq.{queue_name}", Observation(
            ok=False, detail=f"{dlq_name}: check failed: {summarize_exception(exc)}"
        )
    finally:
        if not channel.is_closed:
            await channel.close()


async def check_dlq_depth(
    amqp_url: str, queue_names: Iterable[str], *, connect_timeout_seconds: float = 5.0
) -> dict[str, Observation]:
    """One `dlq.<queue>` observation per configured consumer queue, checking `{queue}.dlq`,
    checked concurrently (m5) once the one connection is open.

    `aio_pika.connect` (M4), not `connect_robust` — the robust variant retries forever on its
    own schedule and never raises, which is right for a long-lived relay and wrong for a probe
    that must report *this* cycle's outcome and give its connection back.
    `connect_timeout_seconds` bounds the one thing that would otherwise hang past it: the
    broker not answering at all.

    A passive `queue_declare` on a queue that does not exist yet closes the channel it was
    issued on (that is plain AMQP, not a bug) — so a fresh channel is opened for every queue,
    and "does not exist yet" is reported ok (a consumer that has never dead-lettered anything
    looks exactly like a consumer that has never been deployed, and neither is an alarm).
    """
    queue_names = list(queue_names)
    try:
        connection = await aio_pika.connect(amqp_url, timeout=connect_timeout_seconds)
    except Exception as exc:
        detail = f"amqp connect failed: {summarize_exception(exc)}"
        return {f"dlq.{q}": Observation(ok=False, detail=detail) for q in queue_names}

    async with connection:
        pairs = await asyncio.gather(
            *(
                _check_one_dlq_queue(connection, name, timeout_seconds=connect_timeout_seconds)
                for name in queue_names
            )
        )
    return dict(pairs)


def _is_dns_failure(exc: BaseException) -> bool:
    """True when `exc`'s cause chain bottoms out in `socket.gaierror` — used only to phrase
    the failure message usefully. **Not** a reason to treat the check as passing (B1, round 2):
    Docker's embedded DNS drops a stopped, crashed, OOM-killed or restarting container exactly
    the same way it would a container whose profile was never turned on — there is no way to
    tell those apart from here, so a name this box's `COMPOSE_PROFILES` says should exist
    (`config.expected_api_containers`) is always a real failure when it does not resolve.
    `socket.EAI_AGAIN` (a transient resolver hiccup) is deliberately included here too — it
    still fails the check, and still counts toward hysteresis, the same as any other failure;
    the point of hysteresis is exactly to absorb a transient blip without paging on the first
    one, not to have this function decide which failures are allowed to count.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return True
        current = current.__cause__ or current.__context__
    return False


async def _check_one_api(
    client: httpx.AsyncClient, name: str, port: int, *, timeout_seconds: float
) -> tuple[str, Observation]:
    url = f"http://{name}:{port}/health/ready"
    try:
        response = await asyncio.wait_for(client.get(url), timeout=timeout_seconds)
        body: dict[str, Any] = {}
        if response.headers.get("content-type", "").startswith("application/json"):
            body = response.json()
        ok = response.status_code == 200 and body.get("status") == "ok"
        return f"api.{name}", Observation(
            ok=ok, detail=f"HTTP {response.status_code} {body.get('status', '')}".strip()
        )
    except TimeoutError:
        return f"api.{name}", Observation(
            ok=False, detail=f"{name}: timed out after {timeout_seconds:.0f}s"
        )
    except httpx.ConnectError as exc:
        if _is_dns_failure(exc):
            # B1 (round 2): this is a real failure, not "not deployed" — a container this
            # box's COMPOSE_PROFILES says should exist that does not resolve at all is
            # indistinguishable from one that crashed, was OOM-killed, or is restarting.
            detail = f"{name} does not resolve on back — stopped/crashed/not created"
        else:
            detail = f"{name}: {summarize_exception(exc)}"
        return f"api.{name}", Observation(ok=False, detail=detail)
    except Exception as exc:
        return f"api.{name}", Observation(ok=False, detail=f"{name}: {summarize_exception(exc)}")


async def check_api_readiness(
    client: httpx.AsyncClient, containers: Mapping[str, int], *, timeout_seconds: float = 5.0
) -> dict[str, Observation]:
    """One `api.<name>` observation per configured container, `GET http://<name>:<port>/health/ready`,
    checked concurrently (m5).

    `containers` is expected to be exactly the set `config.expected_api_containers` says this
    box's `COMPOSE_PROFILES` should run (B1) — every one of them gets a real, failing
    observation if it does not answer, for any reason. There is no "this one is probably just
    not deployed" exemption here any more; that used to hide a crashed or OOM-killed container
    behind the same DNS-resolution failure a genuinely undeployed one produces, and the two are
    not distinguishable from this vantage point.

    Hysteresis (3 consecutive fails before this fires) is `state.py`'s job, not this
    function's — it reports one HTTP call's outcome, nothing more.
    """
    pairs = await asyncio.gather(
        *(
            _check_one_api(client, name, port, timeout_seconds=timeout_seconds)
            for name, port in containers.items()
        )
    )
    return dict(pairs)


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
        return {
            "backup": Observation(ok=False, detail=f"unreadable marker: {summarize_exception(exc)}")
        }
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
        detail = f"handshake to {host}:{port} failed: {summarize_exception(exc)}"
        return {"cert": Observation(ok=False, detail=detail)}
    days_left = (not_after - now).total_seconds() / 86400
    ok = days_left >= warn_days
    detail = f"{days_left:.1f}d until expiry ({not_after.date().isoformat()}) for {sni}"
    return {"cert": Observation(ok=ok, detail=detail)}
