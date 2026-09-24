"""R1: the edge wiring that lets app-gateway rate limit the *caller* instead of Caddy.

The behaviour is tested where it lives (`libs/platform-core/tests/test_gateway_client_address.py`,
`services/app-gateway/tests/test_webhook_public_paths.py`). What none of those can see is whether
the box is actually *configured* to use it, and that is where this fix fails silently: a gateway
that trusts nobody works perfectly and rate limits the whole internet as one caller. So this file
pins the configuration, statically, the way `test_compose_hop_key.py` pins its key:

* `TRUSTED_PROXY_CIDRS` reaches app-gateway and **only** app-gateway — trusting `X-Forwarded-For`
  anywhere else lets a caller name its own address;
* the default it ships with covers Docker's own address pool and no public address;
* the Caddyfile still leaves `X-Forwarded-For` to Caddy's default, which *replaces* a client's
  header, and never turns on `trusted_proxies` (which makes Caddy append instead);
* nothing sets uvicorn's own `FORWARDED_ALLOW_IPS` (a second, coarser way to trust the header);
* the new `.env` keys are optional — commented out in `.env.example`, because an uncommented key
  there is required on every box by `preflight.sh`.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from platform_core.gateway import parse_trusted_proxies

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infrastructure/edge/compose.prod.yml"
CADDYFILE = REPO_ROOT / "infrastructure/edge/caddy/Caddyfile"
ENV_EXAMPLE = REPO_ROOT / "infrastructure/edge/.env.example"

KEY = "TRUSTED_PROXY_CIDRS"
#: Every knob this fix added to the compose environment.
KEYS = (
    KEY,
    "ANONYMOUS_RATE_LIMIT_PER_MINUTE",
    "ANONYMOUS_GLOBAL_RATE_LIMIT_PER_MINUTE",
    "WEBHOOK_RATE_LIMIT_PER_MINUTE",
    "WEBHOOK_GLOBAL_RATE_LIMIT_PER_MINUTE",
)


def _compose() -> dict[str, Any]:
    return dict(yaml.safe_load(COMPOSE_FILE.read_text()))


def _environment(service: str) -> dict[str, object]:
    env = _compose()["services"][service].get("environment") or {}
    assert isinstance(env, dict), f"{service}: expected a mapping environment"
    return env


def _default_of(value: object) -> str:
    """`${TRUSTED_PROXY_CIDRS:-a,b}` -> `a,b`."""
    match = re.fullmatch(r"\$\{[A-Z0-9_]+:-(.*)\}", str(value))
    assert match, f"not a `${{VAR:-default}}` reference: {value!r}"
    return match.group(1)


def _caddyfile_directives() -> list[str]:
    """The Caddyfile with comments removed, so a warning *about* a directive is not mistaken for
    the directive."""
    return [
        line.strip()
        for line in CADDYFILE.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# ---------------------------------------------------------------------------------- compose


def test_app_gateway_is_told_which_proxy_to_believe() -> None:
    assert _environment("app-gateway").get(KEY, "").startswith("${TRUSTED_PROXY_CIDRS:-")


def test_no_service_other_than_app_gateway_is_told_to_trust_a_forwarded_address() -> None:
    """A service behind the gateway sees the gateway as its peer. Trusting `X-Forwarded-For`
    there would let whoever can reach it choose the address it logs and limits on."""
    carriers = sorted(
        name
        for name, cfg in _compose()["services"].items()
        if isinstance(cfg.get("environment"), dict)
        and any(k in cfg["environment"] for k in (KEY, "FORWARDED_ALLOW_IPS"))
    )
    assert carriers == ["app-gateway"], carriers


def test_the_shared_service_env_anchor_does_not_carry_the_setting() -> None:
    """Every service merges this block; putting the key in it would hand it to all of them."""
    anchor = _compose()["x-service-env"]
    assert not any(k in anchor for k in (*KEYS, "FORWARDED_ALLOW_IPS"))


def test_nothing_sets_uvicorns_own_forwarded_allow_ips() -> None:
    """uvicorn has its own switch for trusting `X-Forwarded-For` (read from the environment),
    coarser than the gateway's and set nowhere. If it is ever set to `*` the header is believed
    from any peer — the spoof this whole fix exists to close."""
    assert "FORWARDED_ALLOW_IPS" not in COMPOSE_FILE.read_text()
    for entrypoint in (
        REPO_ROOT / "services/app-gateway/bin/api",
        REPO_ROOT / "services/app-gateway/Dockerfile",
    ):
        text = entrypoint.read_text()
        assert "forwarded-allow-ips" not in text and "proxy-headers" not in text, entrypoint


def test_the_default_trusts_dockers_address_pool_and_no_public_address() -> None:
    """Compose allocates `front` from Docker's own pool, so its subnet is not fixed and the
    default has to cover it — while trusting nothing routable, because a public address in this
    list is a peer that can name its own bucket."""
    networks = parse_trusted_proxies(_default_of(_environment("app-gateway")[KEY]).split(","))
    assert networks, "an empty default trusts nobody: the limiter goes back to keying on Caddy"

    def trusted(address: str) -> bool:
        ip = ipaddress.ip_address(address)
        return any(ip in network for network in networks)

    # Docker's default address pools: 172.17.0.0/16 .. 172.31.0.0/16, then 192.168.0.0/16.
    assert all(trusted(f"172.{second}.0.2") for second in range(17, 32))
    assert trusted("192.168.16.2")
    for public in ("8.8.8.8", "203.0.113.7", "1.1.1.1", "172.32.0.1", "172.15.0.1"):
        assert not trusted(public), public


def test_the_rate_limit_numbers_are_not_repeated_in_compose() -> None:
    """Empty falls through to the service's own default, so there is one number to change and it
    is in `services/app-gateway/app/config.py`, not a second copy here that drifts."""
    env = _environment("app-gateway")
    for key in KEYS[1:]:
        assert env.get(key) == f"${{{key}:-}}", key


# ------------------------------------------------------------------ environment -> policy

GATEWAY_DIR = REPO_ROOT / "services/app-gateway"

_POLICY_SCRIPT = (
    "import json\n"
    "from app.main import policy\n"
    "print(json.dumps({\n"
    "    'trusted': [str(n) for n in policy.trusted_proxies],\n"
    "    'anon': policy.anonymous_rate_limit,\n"
    "    'anon_global': policy.anonymous_global_rate_limit,\n"
    "    'hook': policy.webhook_rate_limit,\n"
    "    'hook_global': policy.webhook_global_rate_limit,\n"
    "}))\n"
)


def _gateway_policy(env: dict[str, str]) -> dict[str, Any]:
    """`app.main` reads its settings once, at import, and `import app` is ambiguous across
    services — so what the policy carries for a given environment is read in a fresh interpreter
    inside the gateway's own directory."""
    result = subprocess.run(
        [sys.executable, "-c", _POLICY_SCRIPT],
        cwd=GATEWAY_DIR,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(json.loads(result.stdout.strip().splitlines()[-1]))


def test_the_named_proxies_and_limits_are_what_the_gateway_policy_carries() -> None:
    """The wiring from the container's environment to the policy — the one step neither the
    behaviour tests (which build a policy by hand) nor the compose tests (which stop at the
    environment) can see. Dropping `trusted_proxies=` from `app/main.py` passes both and leaves
    every anonymous caller in Caddy's bucket."""
    carried = _gateway_policy(
        {
            "TRUSTED_PROXY_CIDRS": "172.16.0.0/12, 10.0.0.0/8",
            "ANONYMOUS_RATE_LIMIT_PER_MINUTE": "11",
            "ANONYMOUS_GLOBAL_RATE_LIMIT_PER_MINUTE": "222",
            "WEBHOOK_RATE_LIMIT_PER_MINUTE": "33",
            "WEBHOOK_GLOBAL_RATE_LIMIT_PER_MINUTE": "444",
        }
    )
    assert carried == {
        "trusted": ["172.16.0.0/12", "10.0.0.0/8"],
        "anon": 11,
        "anon_global": 222,
        "hook": 33,
        "hook_global": 444,
    }


def test_the_compose_default_is_what_the_gateway_ends_up_trusting() -> None:
    """The exact value the box gets when its `.env` says nothing, end to end."""
    default = _default_of(_environment("app-gateway")[KEY])
    carried = _gateway_policy({KEY: default})
    assert carried["trusted"] == [str(n) for n in parse_trusted_proxies(default.split(","))]
    assert carried["trusted"], "the shipped default trusts nobody"


def test_a_gateway_told_nothing_trusts_nobody() -> None:
    assert _gateway_policy({KEY: ""})["trusted"] == []


def test_a_catch_all_stops_the_gateway_booting() -> None:
    """A container that will not start is how a bad `.env` line becomes visible."""
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=GATEWAY_DIR,
        env={**os.environ, KEY: "0.0.0.0/0"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "matches every address" in result.stderr


# ---------------------------------------------------------------------------------- Caddy


def test_caddy_does_not_turn_on_trusted_proxies() -> None:
    """With no `trusted_proxies`, Caddy *replaces* a client-sent `X-Forwarded-For` with the
    address it accepted the connection from (verified against `caddy:2.10.0`). Turning it on makes
    Caddy trust and append to the client's header instead, and the gateway's rightmost-untrusted
    read would then need the same ranges — a change to make on purpose, in both places."""
    forbidden = [
        line
        for line in _caddyfile_directives()
        if re.match(r"(trusted_proxies|client_ip_headers|trusted_proxies_strict)\b", line)
        or re.match(r"header_up\s+X-Forwarded-For\b", line, re.IGNORECASE)
    ]
    assert forbidden == [], forbidden


def test_caddy_sends_api_traffic_only_to_the_gateway_that_holds_the_trust_list() -> None:
    """The trust list is app-gateway's. If `/api/*` were ever pointed elsewhere the fix would
    silently stop applying to it."""
    text = "\n".join(_caddyfile_directives())
    assert re.search(r"handle /api/\* \{\s*reverse_proxy app-gateway:8000", text), text


# ------------------------------------------------------------------------------ .env.example


def test_the_new_keys_are_optional_because_they_are_commented_out_in_env_example() -> None:
    """`preflight.sh` requires every *uncommented* key in `.env.example` to be present on the
    box. These have compose defaults and must not become a deploy prerequisite."""
    lines = ENV_EXAMPLE.read_text().splitlines()
    uncommented = {line.split("=", 1)[0] for line in lines if line and not line.startswith("#")}
    assert not uncommented & set(KEYS), uncommented & set(KEYS)
    documented = {
        line.lstrip("# ").split("=", 1)[0]
        for line in lines
        if line.startswith("# ") and "=" in line
    }
    assert set(KEYS) <= documented, set(KEYS) - documented


def test_env_example_never_suggests_trusting_every_address() -> None:
    for line in ENV_EXAMPLE.read_text().splitlines():
        if KEY in line and "=" in line:
            assert "0.0.0.0/0" not in line and "::/0" not in line, line
