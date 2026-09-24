"""R1: the edge wiring that lets app-gateway rate limit the *caller* instead of Caddy.

The behaviour is tested where it lives (`libs/platform-core/tests/test_gateway_client_address.py`,
`services/app-gateway/tests/test_webhook_public_paths.py`). What none of those can see is whether
the box is actually *configured* to use it, and that is where this fix fails silently: a gateway
that trusts nobody works perfectly and rate limits the whole internet as one caller. So this file
pins the configuration, statically, the way `test_compose_hop_key.py` pins its key:

* `TRUSTED_PROXY_CIDRS` reaches app-gateway and **only** app-gateway — trusting `X-Forwarded-For`
  anywhere else lets a caller name its own address;
* the default it ships with covers Docker's own address pool and no public address, and the
  gateway refuses anything outside private space at boot (and says so, naming the entry);
* the Caddyfile still leaves `X-Forwarded-For` to Caddy's default, which *replaces* a client's
  header, and never turns on `trusted_proxies` (which makes Caddy append instead);
* Caddy caps the two webhook bodies at the size the route table allows, and sets no global
  `read_body` timeout (slow authenticated uploads are legitimate; the gateway bounds slow
  unauthenticated bodies itself);
* nothing sets uvicorn's own `FORWARDED_ALLOW_IPS` (a second, coarser way to trust the header);
* the new `.env` keys are optional — commented out in `.env.example`, because an uncommented key
  there is required on every box by `preflight.sh` — and the knobs the failure-only redesign
  removed are gone everywhere;
* the runbook says what the trust range really trusts and how to narrow it to Caddy's address.
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

import pytest
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
    "ANONYMOUS_CONTENT_GLOBAL_RATE_LIMIT_PER_MINUTE",
    "WEBHOOK_FAILURE_LIMIT_PER_MINUTE",
    "UNAUTH_BODY_READ_TIMEOUT_SECONDS",
    "UNAUTH_BODY_MAX_CONCURRENT_PER_ADDRESS",
    "UNAUTH_BODY_MAX_CONCURRENT_GLOBAL",
)
#: Knobs the first version of this fix shipped and the failure-only redesign removed. They must
#: not linger anywhere the box's environment is documented or passed through: a dead knob is a
#: number an operator tunes to no effect.
REMOVED_KEYS = ("WEBHOOK_RATE_LIMIT_PER_MINUTE", "WEBHOOK_GLOBAL_RATE_LIMIT_PER_MINUTE")


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
    "    'anon_content_global': policy.anonymous_content_global_rate_limit,\n"
    "    'hook_failures': policy.webhook_failure_limit,\n"
    "    'body_timeout': policy.unauth_body_read_timeout,\n"
    "    'slots_per_address': policy.unauth_body_slots_per_address,\n"
    "    'slots_global': policy.unauth_body_slots_global,\n"
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
            "ANONYMOUS_CONTENT_GLOBAL_RATE_LIMIT_PER_MINUTE": "3333",
            "WEBHOOK_FAILURE_LIMIT_PER_MINUTE": "44",
            "UNAUTH_BODY_READ_TIMEOUT_SECONDS": "2.5",
            "UNAUTH_BODY_MAX_CONCURRENT_PER_ADDRESS": "5",
            "UNAUTH_BODY_MAX_CONCURRENT_GLOBAL": "66",
        }
    )
    assert carried == {
        "trusted": ["172.16.0.0/12", "10.0.0.0/8"],
        "anon": 11,
        "anon_global": 222,
        "anon_content_global": 3333,
        "hook_failures": 44,
        "body_timeout": 2.5,
        "slots_per_address": 5,
        "slots_global": 66,
    }


def test_the_box_defaults_are_the_documented_ones_and_the_ceilings_are_on() -> None:
    """A box whose `.env` sets none of the knobs (compose passes each as empty) ends up with every
    limit switched on — an empty value must never read as `0`, which switches a ceiling off."""
    empty = {key: "" for key in KEYS if key != KEY}
    carried = _gateway_policy(empty)
    assert carried["anon"] == 60
    assert carried["anon_global"] == 6000
    assert carried["anon_content_global"] == 60000
    assert carried["hook_failures"] == 60
    assert carried["body_timeout"] == 10.0
    assert (carried["slots_per_address"], carried["slots_global"]) == (4, 32)


def test_the_compose_default_is_what_the_gateway_ends_up_trusting() -> None:
    """The exact value the box gets when its `.env` says nothing, end to end."""
    default = _default_of(_environment("app-gateway")[KEY])
    carried = _gateway_policy({KEY: default})
    assert carried["trusted"] == [str(n) for n in parse_trusted_proxies(default.split(","))]
    assert carried["trusted"], "the shipped default trusts nobody"


def test_a_gateway_told_nothing_trusts_nobody() -> None:
    assert _gateway_policy({KEY: ""})["trusted"] == []


@pytest.mark.parametrize(
    "value", ["0.0.0.0/0", "0.0.0.0/1,128.0.0.0/1", "2000::/3", "8.0.0.0/8", "203.0.113.7"]
)
def test_a_range_outside_private_space_stops_the_gateway_booting(value: str) -> None:
    """A container that will not start is how a bad `.env` line becomes visible — and the error
    names the entry, so the line can be found."""
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=GATEWAY_DIR,
        env={**os.environ, KEY: value},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "private, loopback, link-local or unique-local" in result.stderr
    assert value.split(",")[0] in result.stderr


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ANONYMOUS_RATE_LIMIT_PER_MINUTE", "0"),
        ("WEBHOOK_FAILURE_LIMIT_PER_MINUTE", "0"),
        ("ANONYMOUS_GLOBAL_RATE_LIMIT_PER_MINUTE", "-1"),
        ("UNAUTH_BODY_MAX_CONCURRENT_GLOBAL", "0"),
    ],
)
def test_a_limit_that_would_refuse_everyone_stops_the_gateway_booting(key: str, value: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=GATEWAY_DIR,
        env={**os.environ, key: value},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert key.lower() in result.stderr.lower()


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


def _api_handle_block() -> str:
    """The body of the Caddyfile's `handle /api/* { … }`, brace-matched, comments removed."""
    text = "\n".join(_caddyfile_directives())
    start = text.index("handle /api/* {") + len("handle /api/* {")
    depth, end = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        end += 1
    return text[start : end - 1]


def test_caddy_sends_api_traffic_only_to_the_gateway_that_holds_the_trust_list() -> None:
    """The trust list is app-gateway's. If `/api/*` were ever pointed elsewhere the fix would
    silently stop applying to it."""
    assert re.search(r"reverse_proxy app-gateway:8000 \{", _api_handle_block())


def _bytes(size: str) -> int:
    match = re.fullmatch(r"(\d+)(KiB|MiB|KB|MB|B)", size)
    assert match, f"unrecognised Caddy size {size!r}"
    unit = {"B": 1, "KB": 1000, "KiB": 1024, "MB": 1000**2, "MiB": 1024**2}[match.group(2)]
    return int(match.group(1)) * unit


def test_caddy_caps_each_webhook_body_at_the_size_the_route_table_allows() -> None:
    """The second fence. `request_body { max_size }` was checked against the pinned `caddy:2.10.0`:
    413 for a body over the cap, untouched under it, other paths unaffected. It must equal the
    gateway's own cap per route — larger would be no fence, smaller would refuse a genuine
    delivery the gateway is willing to take (Meta documents up to 3 MB)."""
    block = _api_handle_block()
    matchers = dict(re.findall(r"(@\w+) path (\S+)", block))
    caddy_caps = {
        matchers[name]: _bytes(size)
        for name, size in re.findall(r"request_body (@\w+) \{\s*max_size (\S+)\s*\}", block)
    }
    routes = json.loads((REPO_ROOT / "infrastructure/edge/routes.prod.json").read_text())["routes"]
    route_caps = {
        r["prefix"]: r["max_body_bytes"]
        for r in routes
        if r["prefix"].startswith("/api/v1/webhooks/")
    }
    assert route_caps, "the production table carries no webhook routes"
    assert caddy_caps == route_caps


def test_caddy_has_no_global_read_body_timeout() -> None:
    """Deliberate. `read_body` applies to every request on the server and would break slow,
    legitimate document uploads on authenticated paths; the slow-body risk is bounded per
    unauthenticated request in the gateway instead (a read deadline and a cap on how many are in
    flight)."""
    assert [line for line in _caddyfile_directives() if "read_body" in line] == []


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


# ---------------------------------------------------------------------------------- no dead knobs


def test_the_knobs_the_redesign_removed_are_gone_from_everywhere_the_box_is_configured() -> None:
    """`WEBHOOK_RATE_LIMIT_PER_MINUTE` and `WEBHOOK_GLOBAL_RATE_LIMIT_PER_MINUTE` limited a webhook
    by total requests, which is exactly what let five addresses starve every genuine delivery.
    They no longer exist; a leftover mention is a number an operator tunes to no effect."""
    documents = (
        COMPOSE_FILE,
        ENV_EXAMPLE,
        REPO_ROOT / "infrastructure/edge/RUNBOOK.md",
        REPO_ROOT / "services/app-gateway/README.md",
        REPO_ROOT / "services/app-gateway/app/config.py",
        REPO_ROOT / "services/app-gateway/app/main.py",
    )
    for path in documents:
        text = path.read_text()
        for key in REMOVED_KEYS:
            assert key not in text, f"{key} is still mentioned in {path.name}"


# ----------------------------------------------------------------------- the trust boundary, said


def test_the_compose_comment_no_longer_claims_the_setting_trusts_only_caddy() -> None:
    """The default range trusts every container on the Docker networks. The comment that said
    'Caddy's, and nobody else's' described the intent, not the behaviour."""
    assert "nobody else's" not in COMPOSE_FILE.read_text()
    assert "every container" in COMPOSE_FILE.read_text()


def test_the_runbook_says_how_to_narrow_the_trust_to_caddys_own_address() -> None:
    """The commands are only useful if they name the real project and network."""
    compose = _compose()
    project = compose["name"]
    assert "front" in compose["networks"]
    runbook = (REPO_ROOT / "infrastructure/edge/RUNBOOK.md").read_text()
    assert "### Narrowing the trusted proxy set" in runbook
    assert f"{project}_front" in runbook
    assert "docker inspect" in runbook and "TRUSTED_PROXY_CIDRS=172." in runbook
    assert "/32" in runbook


def test_the_runbook_does_not_say_empty_means_trust_nobody_on_the_box() -> None:
    """On the box compose's `:-` turns an empty value into the private-range default."""
    runbook = (REPO_ROOT / "infrastructure/edge/RUNBOOK.md").read_text()
    assert 'An empty value means "trust nobody"' not in runbook
    assert 'Empty does not mean "trust nobody" on the box' in runbook
