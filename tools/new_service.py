#!/usr/bin/env python3
"""Scaffold a service.

Every service in the platform is the same shape, so this generates that shape rather than
asking anyone to remember it. What comes out already has: the app factory wired, a schema
of its own, the outbox and idempotency tables, an audit table, a health check, the three
process entrypoints, Alembic configured to keep its version table in its own schema, and
a test that proves the app boots.

    python tools/new_service.py --name billing --schema billing
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _files(name: str, schema: str, module: str, title: str) -> dict[str, str]:
    return {
        "app/__init__.py": "",
        "app/config.py": f'''"""Configuration for the {name} service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "{name}"
    db_schema: str = "{schema}"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
''',
        "app/models.py": f'''"""Tables owned by the {name} service.

Everything here lives in the `{schema}` schema and nothing outside it may hold a foreign
key into it.
"""

from __future__ import annotations

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base

SCHEMA = "{schema}"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
''',
        "app/schemas.py": f'''"""Request and response contracts for {name}.

This module is the OpenAPI surface: a change here is a change to the published contract,
and the frontend generates its client from it.
"""

from __future__ import annotations

from pydantic import BaseModel


class ServiceInfo(BaseModel):
    service: str
    schema_name: str
''',
        "app/selectors.py": f'''"""Reads for {name}. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)`. A list endpoint that queries directly instead
of coming through a selector is how an organization leaks.
"""

from __future__ import annotations
''',
        "app/services.py": f'''"""Writes for {name}.

Business rules, transactions, audit rows and outbox rows. A write opens its own
transaction with `async with session.begin()` so the state change, its audit entry and
any event it emits all commit or all roll back.
"""

from __future__ import annotations
''',
        "app/api/__init__.py": "",
        "app/api/routes.py": f'''"""HTTP surface for {name}."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.models import SCHEMA
from app.schemas import ServiceInfo

router = APIRouter(tags=["{name}"])


@router.get("/{name}/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)
''',
        "app/main.py": f'''"""Entry point for the {name} service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from platform_core.app import create_app

app = create_app(
    settings(),
    title="{title}",
    description="{title} — part of the Atmora platform.",
    routers=[router],
)
''',
        "app/relay.py": f'''"""Outbox relay for {name}. Runs as its own process.

Must run as a role with BYPASSRLS: it drains every organization's events in one loop, so
under row-level security as the app role it would silently see nothing.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.models import OutboxEvent
from platform_core.db import get_sessionmaker, init_engine
from platform_core.events import OutboxRelay


async def main() -> None:
    cfg = settings()
    init_engine(cfg)
    relay = OutboxRelay(
        amqp_url=cfg.rabbitmq_url,
        outbox_cls=OutboxEvent,
        session_factory=get_sessionmaker(),
        producer=cfg.service_name,
    )
    await relay.run()


if __name__ == "__main__":
    asyncio.run(main())
''',
        "app/consumers.py": f'''"""Event consumers for {name}. Runs as its own process.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the
same transaction as its side effects. Redelivery is normal, not exceptional.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from platform_core.db import init_engine
from platform_core.events import EventConsumer

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="{name}.events")


async def main() -> None:
    init_engine(settings())
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
''',
        "tests/__init__.py": "",
        "tests/conftest.py": """from __future__ import annotations

import sys
from pathlib import Path

# The service directory is the import root: every service exposes a top-level `app`,
# so only one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
""",
        "tests/test_service.py": f'''"""The service boots and answers.

Thin on purpose — it exists so a freshly generated service is green from the first
commit, and so a broken factory wiring fails here rather than in a deploy.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_this_service() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {{"status": "ok", "service": "{name}"}}


def test_info_is_mounted_under_the_versioned_prefix() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/{name}/info")
    assert response.status_code == 200
    assert response.json()["schema_name"] == "{schema}"


def test_unknown_route_returns_problem_json() -> None:
    # Every failure in the platform has the same shape, including 404s.
    with TestClient(app) as client:
        response = client.get("/api/v1/{name}/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "request_id" in response.json()
''',
        "alembic.ini": """[alembic]
script_location = migrations
prepend_sys_path = .
path_separator = os
sqlalchemy.url = driver://user:pass@localhost/dbname

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
""",
        "migrations/env.py": '''"""Alembic environment — async, schema per service.

The schema is created before the first migration runs, so a fresh database needs no
manual DDL. `version_table_schema` keeps this service's alembic_version inside its own
schema rather than a shared public one.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.models import SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

cfg = settings()
config.set_main_option("sqlalchemy.url", cfg.database_url)
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:  # type: ignore[no-untyped-def]
    """Manage only this service's own schema.

    Anything else — another service's tables, or PostGIS's public.spatial_ref_sys, which
    reflects with schema=None — must be invisible, or autogenerate proposes dropping it.
    """
    if type_ == "table":
        return bool(obj.schema == SCHEMA)
    return True


def do_run_migrations(connection: Connection) -> None:
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=cfg.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
''',
        "migrations/script.py.mako": '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
''',
        "bin/api": """#!/usr/bin/env bash
set -euo pipefail
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
""",
        "bin/worker": """#!/usr/bin/env bash
set -euo pipefail
exec python -m app.consumers
""",
        "bin/cron": """#!/usr/bin/env bash
set -euo pipefail
exec python -m app.relay
""",
        "Dockerfile": f"""# syntax=docker/dockerfile:1
# Dependencies resolve once at the workspace root, then exactly one service's code is
# copied in. Services are not installed as packages — each exposes a top-level `app`, so
# only one may be present per image.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PATH="/app/.venv/bin:$PATH"
RUN groupadd -r atmora && useradd -r -g atmora atmora
WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY --chown=atmora:atmora services/{name}/ /app/
USER atmora
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \\
  CMD python -c "import urllib.request as u,sys; \\
      sys.exit(0 if u.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
""",
        "README.md": f"""# {title}

Part of the Atmora platform. Owns the `{schema}` schema.

```bash
make run SERVICE={name} PORT=8110    # API
make worker SERVICE={name}           # event consumer
make relay SERVICE={name}            # outbox relay
```

## Owns

_Describe the tables and the domain rules this service is the authority for._

## Publishes

_Event types, with a line each on what they mean._

## Consumes

_Event types, and which projection each one maintains._
""",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold an Atmora service")
    parser.add_argument("--name", required=True, help="service name, e.g. billing")
    parser.add_argument("--schema", help="postgres schema (defaults to the name)")
    args = parser.parse_args()

    name = args.name.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        print(f"error: {name!r} must be lowercase letters, digits and hyphens", file=sys.stderr)
        return 2

    # A hyphen is fine in a directory and a URL, and illegal in a Postgres identifier
    # used unquoted — so the schema normalises to underscores.
    schema = (args.schema or name).strip().lower().replace("-", "_")
    module = name.replace("-", "_")
    title = name.replace("-", " ").title() + " service"

    target = ROOT / "services" / name
    if target.exists():
        print(f"error: {target} already exists", file=sys.stderr)
        return 1

    for rel, content in _files(name, schema, module, title).items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if rel.startswith("bin/"):
            path.chmod(0o755)

    (target / "migrations" / "versions").mkdir(parents=True, exist_ok=True)
    (target / "migrations" / "versions" / ".gitkeep").write_text("")

    print(f"created services/{name}  (schema {schema})")
    print("next:")
    print(f"  make makemigration SERVICE={name} M='initial schema'")
    print(f"  make run SERVICE={name} PORT=8110")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
