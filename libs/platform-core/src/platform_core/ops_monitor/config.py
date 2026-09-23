"""Configuration for the edge box's monitor. One instance, one process, the whole box.

Reuses `EDGE_RELAY_SCHEMAS` verbatim for the outbox-lag check (`checks.check_outbox_lag`) —
the same comma-separated `schema` or `schema:producer` list `edge_relay.py` already parses,
and the same "a profile that is off just has no table" tolerance: a schema this box does not
run is skipped, not alarmed on. There is deliberately no second env var for "which schemas to
watch" — one list, one meaning, read by both processes.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field, field_validator
from pydantic_settings import NoDecode

from platform_core.config import ServiceSettings
from platform_core.edge_relay import SchemaSpec, parse_edge_relay_schemas

#: The API containers that run regardless of `COMPOSE_PROFILES` (`memory-budget.json`'s
#: `profile: null` entries, kind `api`) — the safe default for `OPS_MONITOR_API_CONTAINERS`.
#: A box that enables a product profile adds that product's own API name(s) to its `.env`;
#: nothing here assumes a container this box's profiles do not run.
CORE_API_CONTAINERS: Final[tuple[str, ...]] = (
    "auth",
    "account",
    "product",
    "app-gateway",
    "catalog",
    "subscription",
    "billing",
    "metering",
    "payment",
    "notification",
)

#: The consumer queues the always-on workers bind (`compose.prod.yml`'s `profile: null`
#: `*-worker` containers) — the safe default for `OPS_MONITOR_DLQ_QUEUES`. Each is checked as
#: `{queue}.dlq`. Same reasoning as `CORE_API_CONTAINERS`: a product profile's own worker adds
#: its own queue name(s) via `.env`.
CORE_DLQ_QUEUES: Final[tuple[str, ...]] = (
    "account.events",
    "product.events",
    "subscription.events",
    "payment.events",
    "notification.events",
    "notification.dispatch",
)


def _parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


class OpsMonitorSettings(ServiceSettings):
    service_name: str = "ops-monitor"
    #: Owns no schema of its own — every table it reads belongs to another service, named
    #: explicitly per query, the same shape `EdgeRelaySettings` uses for the same reason.
    db_schema: str = "public"
    #: **BYPASSRLS, SELECT and UPDATE on `outbox` only** — the outbox-lag check connects as
    #: the exact role every relay does (CLAUDE.md), so it sees every organization's backlog
    #: without a tenant bound and without holding a single privilege beyond that one table.
    db_outbox_role: str | None = "opsgrids_outbox"

    # --- the loop -------------------------------------------------------------------------
    ops_monitor_poll_interval_seconds: float = 60.0
    ops_monitor_state_path: str = "/srv/opsgrids/state/ops-monitor.json"
    ops_monitor_heartbeat_path: str = "/srv/opsgrids/state/ops-monitor.heartbeat"
    #: Written by `backup.sh` on a fully successful run. Read-only here.
    ops_monitor_backup_marker_path: str = "/srv/opsgrids/state/backup.last-ok"
    #: `/metrics` and `/status`. Never behind Caddy or a gateway — see the module docstring
    #: and `tests/infra`'s network lint.
    ops_monitor_port: int = 9110

    # --- the state machine's thresholds (state.py's own defaults, made configurable) -------
    ops_monitor_pending_threshold: int = 3
    ops_monitor_recovery_threshold: int = 3
    ops_monitor_reminder_interval_seconds: float = 6 * 3600
    ops_monitor_digest_hour_utc: int = 8

    # --- outbox lag (checks.check_outbox_lag) ----------------------------------------------
    edge_relay_schemas: Annotated[list[SchemaSpec], NoDecode] = Field(default_factory=list)
    ops_monitor_outbox_warn_seconds: float = 300.0
    ops_monitor_outbox_crit_seconds: float = 900.0
    #: Must match the relay's own `edge_relay_max_attempts` (default 50 in both) — this is
    #: what "exhausted" means: a row the relay has already given up on (`tools/outbox.py`).
    ops_monitor_outbox_max_attempts: int = 50

    # --- DLQ depth (checks.check_dlq_depth) -------------------------------------------------
    ops_monitor_dlq_queues: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(CORE_DLQ_QUEUES)
    )

    # --- API readiness (checks.check_api_readiness) -----------------------------------------
    #: `name[:port]`, comma-separated. `name` is also the compose service's DNS name on `back`;
    #: port defaults to 8000, every API container's own port.
    ops_monitor_api_containers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(CORE_API_CONTAINERS)
    )

    # --- disk (checks.check_disk) ------------------------------------------------------------
    ops_monitor_disk_path: str = "/"
    ops_monitor_disk_min_free_percent: float = 15.0

    # --- backup freshness (checks.check_backup_freshness) -------------------------------------
    ops_monitor_backup_max_age_hours: float = 26.0

    # --- certificate expiry (checks.check_cert_expiry) ----------------------------------------
    #: Not the internet — the on-box Caddy container, over `front`, SNI set to `EDGE_HOST`
    #: (ADR-0012 §6's own probe already does the same handshake-on-`front` shape).
    ops_monitor_cert_host: str = "caddy"
    ops_monitor_cert_port: int = 443
    edge_host: str = ""
    ops_monitor_cert_warn_days: int = 14

    # --- alert delivery (mailer.py) -----------------------------------------------------------
    #: Where a firing/reminder/resolved/digest email goes. Unset means: no email, ever — the
    #: alert is still logged at error (`ops.alert.firing`) and `/status` says so in capitals.
    ops_alert_email: str | None = None
    email_from: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.ops_alert_email and self.email_from)

    @field_validator("edge_relay_schemas", mode="before")
    @classmethod
    def _parse_schemas(cls, v: object) -> object:
        if isinstance(v, str):
            return parse_edge_relay_schemas(v)
        return v

    @field_validator("ops_monitor_dlq_queues", "ops_monitor_api_containers", mode="before")
    @classmethod
    def _parse_lists(cls, v: object) -> object:
        if isinstance(v, str):
            return _parse_csv(v)
        return v
