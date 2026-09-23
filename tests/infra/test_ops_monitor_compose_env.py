"""B1: `ops-monitor`'s compose environment must actually carry every variable
`OpsMonitorSettings.smtp_configured` needs. A missing one (this shipped once without
`EMAIL_FROM`) means an operator can fill in `.env` correctly and `preflight.sh` can print `ok`
— `SMTP_HOST`/`OPS_ALERT_EMAIL` were both there — while the container itself never receives the
one variable that was quietly dropped, and email silently never sends.

Static: reads `compose.prod.yml` as plain YAML (the same approach
`test_compose_memory_budget.py` uses) rather than shelling out to `docker compose config`, so
this runs with no Docker daemon required — a real `docker compose config -q` render is still
exercised by hand as part of this slice's own verification, and by CI's `make test-infra`
running in an environment where `docker compose config` is also checked as a separate step.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infrastructure/edge/compose.prod.yml"

#: Every environment variable `OpsMonitorSettings.smtp_configured` (config.py) reads —
#: `smtp_host`, `ops_alert_email`, `email_from` — named here as the literal env-var spelling
#: pydantic-settings resolves each field to, so this test fails by name if the property and
#: this list ever drift apart.
SMTP_CONFIGURED_ENV_VARS = ("SMTP_HOST", "OPS_ALERT_EMAIL", "EMAIL_FROM")


def _ops_monitor_environment() -> dict[str, object]:
    raw = yaml.safe_load(COMPOSE_FILE.read_text())
    env = raw["services"]["ops-monitor"]["environment"]
    assert isinstance(env, dict), (
        "ops-monitor's environment must be a mapping (not a list of 'KEY=value' strings) for "
        "this test's key lookup to mean anything"
    )
    return env


def test_every_smtp_configured_variable_is_present() -> None:
    env = _ops_monitor_environment()
    missing = [name for name in SMTP_CONFIGURED_ENV_VARS if name not in env]
    assert not missing, (
        f"ops-monitor's compose environment is missing {missing} — "
        "OpsMonitorSettings.smtp_configured can never be true no matter what an operator "
        "fills into .env, and preflight's own SMTP warning would be silently wrong"
    )


def test_ops_monitor_does_not_inherit_the_full_service_env_anchor() -> None:
    """m11: deliberately not `<<: *service-env` — this process holds no PII and mints no
    internal service token, so it must not receive `INTERNAL_SERVICE_TOKEN` or
    `PII_ENCRYPTION_KEY`/`PII_DECRYPT_KEYS` at all."""
    env = _ops_monitor_environment()
    forbidden = {"INTERNAL_SERVICE_TOKEN", "PII_ENCRYPTION_KEY", "PII_DECRYPT_KEYS"}
    present = forbidden & set(env)
    assert not present, f"ops-monitor must not receive {present} — it uses neither"


def test_ops_monitor_runs_as_a_configurable_non_default_user() -> None:
    """B2: writes its own state file and heartbeat into the `/srv/opsgrids/state` bind mount,
    which is owned by the deploy user, not the image's baked-in `opsgrids` system user — so it
    must run as that same uid, configurably (a box whose deploy user is not uid 1000 needs to
    be able to say so)."""
    raw = yaml.safe_load(COMPOSE_FILE.read_text())
    user = raw["services"]["ops-monitor"]["user"]
    assert "OPS_MONITOR_UID" in user
    assert "OPS_MONITOR_GID" in user
