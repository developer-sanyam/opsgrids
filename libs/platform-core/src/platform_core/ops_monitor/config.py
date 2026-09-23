"""Configuration for the edge box's monitor. One instance, one process, the whole box.

Reuses `EDGE_RELAY_SCHEMAS` verbatim for the outbox-lag check (`checks.check_outbox_lag`) —
the same comma-separated `schema` or `schema:producer` list `edge_relay.py` already parses,
and the same "a profile that is off just has no table" tolerance: a schema this box does not
run is skipped, not alarmed on. There is deliberately no second env var for "which schemas to
watch" — one list, one meaning, read by both processes.
"""

from __future__ import annotations

import re
from typing import Annotated, Final

from pydantic import Field, field_validator, model_validator
from pydantic_settings import NoDecode

from platform_core.config import ServiceSettings
from platform_core.edge_relay import SchemaSpec, parse_edge_relay_schemas

#: A bare Postgres identifier (m11) — exactly what `EDGE_RELAY_SCHEMAS`' schema names must be,
#: since each one is interpolated into a double-quoted SQL identifier
#: (`checks.check_outbox_lag`'s `f'FROM "{schema}".outbox'`). No dot, no hyphen: a schema name
#: never has either, and a value that failed this on a real box would be a typo worth failing
#: loudly on at startup, not a value worth quoting defensively and hoping the escaping holds.
_VALID_SCHEMA_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

#: The wider shape an AMQP queue name (`account.events`) or a compose-DNS hostname
#: (`app-gateway`) is allowed to take — a dot or a hyphen after the first character, never
#: anything that could break out of the plain string interpolation `checks.py` builds a URL or
#: a queue name from.
_VALID_DNS_OR_QUEUE_NAME = re.compile(r"^[a-z_][a-z0-9_.-]*$")


def _validate_identifiers(
    values: list[str], *, field_name: str, pattern: re.Pattern[str] = _VALID_DNS_OR_QUEUE_NAME
) -> list[str]:
    bad = [v for v in values if not pattern.match(v)]
    if bad:
        raise ValueError(
            f"{field_name} contains a name that does not match {pattern.pattern}: {bad!r}"
        )
    return values


#: The API containers that run regardless of `COMPOSE_PROFILES` (`memory-budget.json`'s
#: `profile: null` entries, kind `api`) — always checked, whatever profiles are enabled.
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
#: `*-worker` containers) — always checked. Each is checked as `{queue}.dlq`.
CORE_DLQ_QUEUES: Final[tuple[str, ...]] = (
    "account.events",
    "product.events",
    "subscription.events",
    "payment.events",
    "notification.events",
    "notification.dispatch",
)

#: B1/m6: which API containers exist for a given `COMPOSE_PROFILES` entry — the same
#: container -> profile mapping `infrastructure/edge/memory-budget.json` prices, for `kind:
#: "api"`, kept here as a second, static copy rather than parsed from that file at runtime: this
#: process must still know what *should* exist even if the JSON file is unreadable, and the
#: whole point of B1 is that "what should exist" cannot depend on anything that could itself be
#: down. `tests/infra/test_ops_monitor_profile_containers.py` is the gate that keeps the two in
#: sync — a profile added to one and not the other fails there, by name, before it ships.
PROFILE_API_CONTAINERS: Final[dict[str, tuple[str, ...]]] = {
    "storefront": ("storefront", "document"),
    "inventory": ("operations",),
    "hrms": ("approval", "employee", "attendance", "leave"),
    "crm": ("customer", "deal"),
    "pulseboard": ("project", "timesheet", "standup"),
    "projects": ("projects",),
    "support": ("support",),
    "marketing": ("marketing",),
    "finance": ("finance",),
    "itsm": ("itsm",),
}

#: B1/m6: which DLQ-bearing worker exists for a given `COMPOSE_PROFILES` entry — only profiles
#: whose worker actually consumes events get an entry; `storefront`, `inventory`, `crm`,
#: `pulseboard`, `projects`, `support`, `marketing` and `itsm` run no consumer today
#: (`compose.prod.yml` has no `*-worker`/`scheduler`-shaped container for them), so they are
#: absent here on purpose, not by omission.
PROFILE_DLQ_QUEUES: Final[dict[str, tuple[str, ...]]] = {
    "hrms": ("approval.events", "attendance.events", "leave.events"),
    "finance": ("finance.events", "scheduler.events"),
}


def _parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def expected_api_containers(profiles: list[str]) -> list[str]:
    """Every API container this box's `COMPOSE_PROFILES` says should exist — core always,
    plus each enabled profile's own. Order is deterministic (core first, then profiles in the
    order given, each profile's own containers in the order `PROFILE_API_CONTAINERS` lists
    them) so a rendered `.env` or a test fixture can compare it literally."""
    result = list(CORE_API_CONTAINERS)
    for profile in profiles:
        result.extend(PROFILE_API_CONTAINERS.get(profile, ()))
    return result


def expected_dlq_queues(profiles: list[str]) -> list[str]:
    """The DLQ-bearing counterpart of `expected_api_containers`."""
    result = list(CORE_DLQ_QUEUES)
    for profile in profiles:
        result.extend(PROFILE_DLQ_QUEUES.get(profile, ()))
    return result


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
    #: M4: the ceiling on any single probe, from `main._collect` — a hung broker or a locked
    #: outbox table must cost this check a `False` observation, never the whole cycle (and with
    #: it, the heartbeat, and every other check due that minute).
    ops_monitor_probe_timeout_seconds: float = 20.0
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
    #: m9: a check failing this many times or more within the last `ops_monitor_flap_window`
    #: polls opens even without `ops_monitor_pending_threshold` consecutive fails.
    ops_monitor_flap_window: int = 5
    ops_monitor_flap_threshold: int = 3
    #: m9/M6: at most this many notification emails per rolling hour — a box that is genuinely
    #: falling apart pages a human once, not once per check per cycle. Once the cap is hit,
    #: further notifications this hour are logged and counted, not sent; the next email under
    #: the cap says how many were suppressed.
    ops_monitor_max_emails_per_hour: int = 12
    #: M6: how many consecutive cycles a failed send is retried immediately (by *not*
    #: committing the notification's `last_notified_at`/`last_digest_date` advance) before
    #: falling back to the normal reminder/digest cadence — bounds how hard a broken SMTP
    #: config is hammered instead of retrying every single poll forever.
    ops_monitor_max_immediate_send_retries: int = 5

    # --- outbox lag (checks.check_outbox_lag) ----------------------------------------------
    edge_relay_schemas: Annotated[list[SchemaSpec], NoDecode] = Field(default_factory=list)
    ops_monitor_outbox_warn_seconds: float = 300.0
    ops_monitor_outbox_crit_seconds: float = 900.0
    #: Must match the relay's own `edge_relay_max_attempts` (default 50 in both) — this is
    #: what "exhausted" means: a row the relay has already given up on (`tools/outbox.py`).
    ops_monitor_outbox_max_attempts: int = 50
    #: M4: `SET LOCAL statement_timeout` on the outbox-lag query's own transaction — a locked
    #: `outbox` table (a stuck relay holding `FOR UPDATE SKIP LOCKED` open, a long migration)
    #: must cost this one schema a timed-out observation, not hang the connection (and the
    #: pool slot) for the rest of the cycle.
    ops_monitor_outbox_statement_timeout_seconds: float = 10.0

    #: B1: this box's own `COMPOSE_PROFILES` — read so `ops_monitor_api_containers`/
    #: `ops_monitor_dlq_queues` can default to "every container/queue this box actually runs"
    #: rather than a fixed guess. `compose.prod.yml` passes it straight through
    #: (`COMPOSE_PROFILES: ${COMPOSE_PROFILES:-}`) — the same variable that already decides
    #: which containers docker compose itself starts, so there is one source of truth for
    #: "what does this box run", not two that can drift.
    compose_profiles: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- DLQ depth (checks.check_dlq_depth) -------------------------------------------------
    #: Comma-separated. Empty means "derive from COMPOSE_PROFILES" (`expected_dlq_queues`) —
    #: core always, plus each enabled profile's own worker queue. Set explicitly only to
    #: narrow or add to that computed list; setting it REPLACES the computed list entirely.
    ops_monitor_dlq_queues: Annotated[list[str], NoDecode] = Field(default_factory=list)
    #: M4: how long `aio_pika.connect` (not `connect_robust`, which retries forever) waits for
    #: the broker before the DLQ check gives up and reports failing rather than hanging.
    ops_monitor_amqp_connect_timeout_seconds: float = 5.0

    # --- API readiness (checks.check_api_readiness) -----------------------------------------
    #: `name[:port]`, comma-separated. `name` is also the compose service's DNS name on `back`;
    #: port defaults to 8000, every API container's own port. Empty means "derive from
    #: COMPOSE_PROFILES" (`expected_api_containers`) — same replace-not-append rule as
    #: `ops_monitor_dlq_queues` above if set explicitly.
    ops_monitor_api_containers: Annotated[list[str], NoDecode] = Field(default_factory=list)

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
    #: STARTTLS on the plain port (587 by convention) — the default, and what the box's own
    #: Google Workspace submission server expects.
    smtp_use_tls: bool = True
    #: Implicit TLS from the first byte (port 465 by convention, "SMTPS") — mutually exclusive
    #: with `smtp_use_tls`'s STARTTLS upgrade; `mailer.py` refuses both at once. Neither implies
    #: an unencrypted connection is ever allowed to authenticate — see `mailer.Mailer._send_sync`.
    smtp_use_ssl: bool = False

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.ops_alert_email and self.email_from)

    @field_validator("edge_relay_schemas", mode="before")
    @classmethod
    def _parse_schemas(cls, v: object) -> object:
        if isinstance(v, str):
            return parse_edge_relay_schemas(v)
        return v

    @field_validator("edge_relay_schemas")
    @classmethod
    def _validate_schema_names(cls, v: list[SchemaSpec]) -> list[SchemaSpec]:
        _validate_identifiers(
            [spec.schema for spec in v],
            field_name="EDGE_RELAY_SCHEMAS",
            pattern=_VALID_SCHEMA_IDENTIFIER,
        )
        return v

    @field_validator(
        "ops_monitor_dlq_queues", "ops_monitor_api_containers", "compose_profiles", mode="before"
    )
    @classmethod
    def _parse_lists(cls, v: object) -> object:
        if isinstance(v, str):
            return _parse_csv(v)
        return v

    @field_validator("ops_monitor_dlq_queues")
    @classmethod
    def _validate_dlq_queues(cls, v: list[str]) -> list[str]:
        return _validate_identifiers(v, field_name="OPS_MONITOR_DLQ_QUEUES")

    @field_validator("ops_monitor_api_containers")
    @classmethod
    def _validate_api_containers(cls, v: list[str]) -> list[str]:
        _validate_identifiers(
            [entry.split(":")[0] for entry in v], field_name="OPS_MONITOR_API_CONTAINERS"
        )
        return v

    @field_validator("compose_profiles")
    @classmethod
    def _validate_compose_profiles(cls, v: list[str]) -> list[str]:
        return _validate_identifiers(
            v, field_name="COMPOSE_PROFILES", pattern=_VALID_SCHEMA_IDENTIFIER
        )

    @model_validator(mode="after")
    def _default_expected_names_from_profiles(self) -> OpsMonitorSettings:
        """B1: an empty `ops_monitor_api_containers`/`ops_monitor_dlq_queues` means "derive
        from `compose_profiles`" rather than "check nothing" — filled in here, once, after
        every field has its own parsed/validated value, since the derivation needs both."""
        if not self.ops_monitor_api_containers:
            self.ops_monitor_api_containers = expected_api_containers(self.compose_profiles)
        if not self.ops_monitor_dlq_queues:
            self.ops_monitor_dlq_queues = expected_dlq_queues(self.compose_profiles)
        return self

    def model_post_init(self, __context: object) -> None:
        """`ServiceSettings`'s production guard, minus the half of it about secrets this
        process never receives (m11).

        The base guard refuses a `dev-only-`/`ci-only-` `internal_service_token` or
        `pii_encryption_key` in production — right for a service that actually signs
        internal-service tokens or seals PII, and pointless here: ops-monitor holds neither for
        any real purpose, and by the same minimal-environment decision is never handed either
        one in `compose.prod.yml` (no `<<: *service-env`), so it would otherwise fail startup
        over two placeholders nobody set on purpose. `debug` and `egress_allow_insecure_http`
        are unrelated to either secret and stay refused in production — this is not a blanket
        opt-out of the base class's hygiene, only the part that does not apply here.
        """
        if self.environment not in ("production", "staging", "uat"):
            return
        if self.debug:
            raise ValueError(f"debug must be off in {self.environment}")
        if self.egress_allow_insecure_http:
            raise ValueError(
                f"{self.environment} refuses egress_allow_insecure_http, the same as every "
                "other service"
            )
