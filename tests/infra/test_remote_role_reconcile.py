"""Pins `role_reconcile_sql` (infrastructure/edge/lib/role-reconcile.sh) — the SQL
`remote.sh migrate` runs, idempotently, before any `alembic upgrade`, so a service added after a
box's first boot has a role to run its migration as. See that file's own module docstring for
the deploy this exists to stop breaking.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_RECONCILE_LIB = REPO_ROOT / "infrastructure/edge/lib/role-reconcile.sh"


def _run(function: str, *args: str) -> subprocess.CompletedProcess[str]:
    script = f'source "{ROLE_RECONCILE_LIB}"; {function} "$@"'
    return subprocess.run(
        ["bash", "-c", script, "bash", *args],
        capture_output=True,
        text=True,
    )


def roles_for_services(*services: str) -> list[str]:
    result = _run("roles_for_services", *services)
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def role_reconcile_sql(*services: str) -> str:
    result = _run("role_reconcile_sql", *services)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_roles_for_services_prefixes_each_one() -> None:
    assert roles_for_services("employee", "attendance", "leave") == [
        "opsgrids_employee",
        "opsgrids_attendance",
        "opsgrids_leave",
    ]


def test_role_reconcile_sql_creates_grants_and_sets_the_password_for_each_role() -> None:
    sql = role_reconcile_sql("leave")
    assert "CREATE ROLE opsgrids_leave LOGIN NOBYPASSRLS" in sql
    assert "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsgrids_leave')" in sql
    assert "ALTER ROLE opsgrids_leave PASSWORD :'pw';" in sql
    assert "GRANT CONNECT ON DATABASE opsgrids TO opsgrids_leave;" in sql
    assert "GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO opsgrids_leave;" in sql


def test_role_reconcile_sql_uses_psql_variable_interpolation_not_a_literal_password() -> None:
    """`:'pw'` must survive verbatim into the generated text — it is resolved by psql reading
    this script on stdin with `-v pw=...`, exactly like `postgres-init/30-role-passwords.sh`.
    A literal password baked in here would end up committed to this repository."""
    sql = role_reconcile_sql("leave")
    assert "ALTER ROLE opsgrids_leave PASSWORD :'pw';" in sql


def test_role_reconcile_sql_covers_every_service_in_order() -> None:
    sql = role_reconcile_sql("employee", "attendance", "leave")
    assert sql.index("opsgrids_employee") < sql.index("opsgrids_attendance") < sql.index("opsgrids_leave")


def test_role_reconcile_sql_is_idempotent_ddl() -> None:
    """The CREATE is guarded by an existence check — running this against a database where
    every role already exists must not raise a duplicate-object error."""
    sql = role_reconcile_sql("leave")
    assert "DO $do$" in sql
    assert "IF NOT EXISTS" in sql


def test_role_reconcile_sql_refuses_an_unsafe_service_name() -> None:
    result = _run("role_reconcile_sql", "leave; DROP TABLE foo --")
    assert result.returncode != 0
    assert "refusing service name" in result.stderr


def test_role_reconcile_sql_refuses_an_empty_service_name() -> None:
    result = _run("role_reconcile_sql", "")
    assert result.returncode != 0


def test_the_full_service_list_in_remote_sh_produces_valid_sql_for_every_entry() -> None:
    """Cross-checks against the real `SERVICES_WITH_SCHEMA` array so the two never drift apart —
    the same reason `tools/reconcile-roles.sh` reads that array out of remote.sh rather than
    keeping its own copy."""
    remote_sh = (REPO_ROOT / "infrastructure/edge/remote.sh").read_text()
    line = next(l for l in remote_sh.splitlines() if l.startswith("SERVICES_WITH_SCHEMA="))
    services = line[len("SERVICES_WITH_SCHEMA=(") : -1].split()
    assert "leave" in services
    sql = role_reconcile_sql(*services)
    for svc in services:
        assert f"opsgrids_{svc}" in sql
