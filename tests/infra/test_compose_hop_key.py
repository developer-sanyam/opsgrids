"""M1(c)/(d) of the third-round security review: `ADMIN_GATEWAY_HOP_KEY` reaches auth, which
verifies the admin-gateway hop signature, and reaches nothing else.

Rendered the way compose renders it — `yaml.safe_load` resolves the `<<: *service-env` merges
and the anchors — so a key added to the shared `x-service-env` block (which every service
inherits, app-gateway included) shows up here as the failure it is. The signer is decided by
`GatewayPolicy.sign_admin_hop`, not by who holds the key; but there is still no reason for the
public product gateway to be handed a secret it has no use for.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infrastructure/edge/compose.prod.yml"
ENV_EXAMPLE = REPO_ROOT / "infrastructure/edge/.env.example"
KEY = "ADMIN_GATEWAY_HOP_KEY"


def _rendered() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text())


def _environment(service: str) -> dict[str, object]:
    env = _rendered()["services"][service].get("environment") or {}
    assert isinstance(env, dict), f"{service}: expected a mapping environment, got {type(env)}"
    return env


def test_auth_is_given_the_hop_key_but_a_missing_one_does_not_break_compose() -> None:
    """`${KEY:-}`, not `${KEY:?}`: a `:?` aborts *every* compose command on a box without the
    key — `backup.sh`'s `pg_dump` and a rollback included — and turns a missing secret into an
    outage of the whole box. The empty value reaches auth, which refuses to start for the right
    reason (`Settings`: no hop key in production/staging/uat), and preflight is where a missing
    key is caught before anything changes (`test_preflight_secrets.py`)."""
    assert _environment("auth").get(KEY) == "${ADMIN_GATEWAY_HOP_KEY:-}"


def test_app_gateway_is_not_given_the_hop_key() -> None:
    assert KEY not in _environment("app-gateway")


def test_the_shared_service_env_anchor_does_not_carry_the_key() -> None:
    """Every service merges this block, so putting the key in it would hand it to all of them."""
    assert KEY not in _rendered()["x-service-env"]


def test_no_service_other_than_auth_carries_the_key() -> None:
    carriers = sorted(
        name
        for name, cfg in _rendered()["services"].items()
        if isinstance(cfg.get("environment"), dict) and KEY in cfg["environment"]
    )
    assert carriers == ["auth"], carriers


def test_the_key_is_a_required_entry_in_the_edge_env_example() -> None:
    """Preflight derives the required keys from this file, so a key missing here is a key the
    deploy never checks for."""
    keys = {
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert KEY in keys
