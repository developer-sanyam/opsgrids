"""The payment provider switches and keys, as the box receives them (ADR-0032).

Four promises, each a way this goes wrong on a production VM that nobody sees until deploy:

* **Only `payment` and `payment-worker` get any provider key.** Rendered the way compose renders it
  (`yaml.safe_load` resolves the `<<: *service-env` merges), so a key added to the shared
  `x-service-env` block — which every service, the public gateway included, inherits — fails here.
  The model is `test_compose_hop_key.py`.
* **An unconfigured box deploys.** None of the new keys is a required key (they are COMMENTED in
  `.env.example`, the warehouse-map key's precedent), and every compose default is the safe side:
  Razorpay, stub wire, stub environment, no live keys, empty credentials.
* **Preflight refuses what `payment` would refuse at start-up** (`lib/payment-switches.sh` mirrors
  `services/payment/app/config.py`), so a bad combination fails the deploy before anything changes
  instead of crash-looping the service and the sweeps folded into it.
* **No message prints a value.**
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
COMPOSE_FILE = EDGE_DIR / "compose.prod.yml"
ENV_EXAMPLE = EDGE_DIR / ".env.example"
PREFLIGHT = EDGE_DIR / "preflight.sh"
LIB = EDGE_DIR / "lib/payment-switches.sh"
MATRIX = REPO_ROOT / "docs/deploy/ENV_SOURCE_MATRIX.md"
RUNBOOK = EDGE_DIR / "RUNBOOK.md"

SECRET_KEYS = ("RAZORPAY_KEY_SECRET", "CASHFREE_SECRET_KEY", "CASHFREE_APP_ID")
SWITCHES = {
    "PAYMENT_PROVIDER": "${PAYMENT_PROVIDER:-razorpay}",
    "PAYMENT_WIRE": "${PAYMENT_WIRE:-stub}",
    "PAYMENT_ALLOW_LIVE_KEYS": "${PAYMENT_ALLOW_LIVE_KEYS:-false}",
    "PAYMENT_ALLOW_TEST_KEYS": "${PAYMENT_ALLOW_TEST_KEYS:-false}",
    "PAYMENT_REQUIRE_LIVE": "${PAYMENT_REQUIRE_LIVE:-false}",
    "CASHFREE_ENV": "${CASHFREE_ENV:-stub}",
    "CASHFREE_APP_ID": "${CASHFREE_APP_ID:-}",
    "CASHFREE_SECRET_KEY": "${CASHFREE_SECRET_KEY:-}",
}
ALL_KEYS = ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", *SWITCHES)


def _rendered() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text())


def _environment(service: str) -> dict[str, object]:
    env = _rendered()["services"][service].get("environment") or {}
    assert isinstance(env, dict)
    return env


def _example_keys() -> tuple[set[str], set[str]]:
    """(uncommented keys, commented-out keys) of `.env.example`."""
    live, commented = set(), set()
    for line in ENV_EXAMPLE.read_text().splitlines():
        if line.startswith("# ") and "=" in line and line[2:].split("=", 1)[0].isidentifier():
            commented.add(line[2:].split("=", 1)[0])
        elif line and not line.startswith("#") and "=" in line:
            live.add(line.split("=", 1)[0])
    return live, commented


class TestComposeParity:
    @pytest.mark.parametrize("service", ["payment", "payment-worker"])
    def test_payment_and_its_worker_receive_every_switch_with_the_safe_default(
        self, service: str
    ) -> None:
        env = _environment(service)
        for key, default in SWITCHES.items():
            assert env.get(key) == default, (service, key)

    def test_the_worker_shares_the_payment_environment_by_anchor(self) -> None:
        """One anchor, so the worker (the `split-workers` rollback) can never drift from the API."""
        assert _environment("payment-worker") == _environment("payment")

    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_no_other_service_carries_a_provider_key(self, key: str) -> None:
        carriers = sorted(
            name
            for name, cfg in _rendered()["services"].items()
            if isinstance(cfg.get("environment"), dict) and key in cfg["environment"]
        )
        assert carriers == ["payment", "payment-worker"], (key, carriers)

    @pytest.mark.parametrize("key", [*ALL_KEYS, "RAZORPAY_WEBHOOK_SECRET"])
    def test_the_shared_service_env_block_does_not_carry_any(self, key: str) -> None:
        assert key not in _rendered()["x-service-env"]

    def test_a_missing_credential_does_not_break_every_compose_command(self) -> None:
        """`${KEY:-}`, never `${KEY:?}`: a `:?` aborts backup.sh's pg_dump and a rollback too."""
        env = _environment("payment")
        for key in (
            "RAZORPAY_KEY_ID",
            "RAZORPAY_KEY_SECRET",
            "CASHFREE_APP_ID",
            "CASHFREE_SECRET_KEY",
        ):
            assert env[key] == f"${{{key}:-}}"


class TestEnvExample:
    def test_the_new_keys_are_commented_so_none_is_required(self) -> None:
        live, commented = _example_keys()
        for key in SWITCHES:
            assert key not in live, f"{key} became a required key"
            assert key in commented, f"{key} is not documented in .env.example"

    def test_the_razorpay_pair_stays_optional_empty_and_present(self) -> None:
        live, _ = _example_keys()
        assert {"RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"} <= live

    def test_every_new_key_has_a_documented_block_that_names_its_consumer_and_default(self) -> None:
        text = ENV_EXAMPLE.read_text()
        for key in SWITCHES:
            block = text.split(f"# >> {key}", 1)[1].split("# >> ", 1)[0]
            assert "consumer: payment, payment-worker" in block, key
            assert "COMMENTED ON PURPOSE" in text.split(f"# >> {key}", 1)[1].split("\n", 1)[0]

    def test_the_example_holds_no_value_for_a_credential(self) -> None:
        for line in ENV_EXAMPLE.read_text().splitlines():
            for key in ("CASHFREE_APP_ID", "CASHFREE_SECRET_KEY", "RAZORPAY_KEY_SECRET"):
                if line.lstrip("# ").startswith(f"{key}="):
                    assert line.split("=", 1)[1].strip() == "", line

    def test_the_documentation_no_longer_calls_the_keys_inert(self) -> None:
        """They were inert while the wire was a stub. They are not any more — but only behind an
        explicit switch, which is what the text must say."""
        text = ENV_EXAMPLE.read_text()
        assert "payments inert" not in text and "never opens a socket (providers/" not in text
        assert "Keys alone never switch" in text or "keys alone never switch" in text.lower()


class TestPreflightIsWired:
    def test_the_keys_are_optional_empty_and_the_library_is_sourced(self) -> None:
        text = PREFLIGHT.read_text()
        line = next(
            ln
            for ln in text.splitlines()
            if ln.startswith('optional_empty="$optional_empty PAYMENT_PROVIDER')
        )
        for key in (
            "PAYMENT_PROVIDER",
            "PAYMENT_WIRE",
            "PAYMENT_ALLOW_LIVE_KEYS",
            "PAYMENT_ALLOW_TEST_KEYS",
            "PAYMENT_REQUIRE_LIVE",
            "CASHFREE_ENV",
            "CASHFREE_APP_ID",
            "CASHFREE_SECRET_KEY",
        ):
            assert key in line
        assert ". ./lib/payment-switches.sh" in text and "payment_switch_problems" in text
        assert 'bad "$problem"' in text

    def test_the_old_inert_message_is_gone_and_says_what_the_box_will_do(self) -> None:
        text = PREFLIGHT.read_text()
        assert "payments are inert" not in text
        assert "in-process stub" in text and "Keys alone change nothing" in text


def _problems(**env: str) -> list[str]:
    script = f'source "{LIB}"; payment_switch_problems'
    clean = {k: v for k, v in os.environ.items() if k not in ALL_KEYS}
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True, env={**clean, **env}
    )
    return [line for line in result.stdout.splitlines() if line]


RZP_TEST = "rzp_test_Devonly000001"
RZP_LIVE = "rzp_live_Devonly000001"
SECRET = "dev-only-switch-secret-value"  # pragma: allowlist secret
CF_TEST_APP = "TESTdev-only-app"  # pragma: allowlist secret
CF_TEST_SECRET = "cfsk_ma_test_dev-only-0001"  # pragma: allowlist secret
CF_PROD_APP = "dev-only-prod-app"  # pragma: allowlist secret
CF_PROD_SECRET = "cfsk_ma_prod_dev-only-0001"  # pragma: allowlist secret


class TestThePreflightRules:
    def test_an_unconfigured_box_is_fine(self) -> None:
        assert _problems() == []

    def test_empty_values_are_the_safe_defaults(self) -> None:
        assert (
            _problems(
                PAYMENT_PROVIDER="",
                PAYMENT_WIRE="",
                CASHFREE_ENV="",
                PAYMENT_ALLOW_LIVE_KEYS="",
                RAZORPAY_KEY_ID="",
                RAZORPAY_KEY_SECRET="",
                CASHFREE_APP_ID="",
                CASHFREE_SECRET_KEY="",
            )
            == []
        )

    def test_test_keys_with_the_stub_wire_are_fine_everywhere(self) -> None:
        assert _problems(RAZORPAY_KEY_ID=RZP_TEST, RAZORPAY_KEY_SECRET=SECRET) == []
        assert _problems(CASHFREE_APP_ID=CF_TEST_APP, CASHFREE_SECRET_KEY=CF_TEST_SECRET) == []

    @pytest.mark.parametrize("name", ["PAYMENT_PROVIDER", "PAYMENT_WIRE", "CASHFREE_ENV"])
    def test_an_unknown_switch_value_is_refused_by_name(self, name: str) -> None:
        problems = _problems(**{name: "stripe"})
        assert any(p.startswith(name) for p in problems), problems

    def test_a_half_set_pair_is_refused(self) -> None:
        assert any(
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET" in p
            for p in _problems(RAZORPAY_KEY_ID=RZP_TEST)
        )
        assert any(
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET" in p
            for p in _problems(RAZORPAY_KEY_SECRET=SECRET)
        )
        assert any(
            "CASHFREE_APP_ID and CASHFREE_SECRET_KEY" in p
            for p in _problems(CASHFREE_APP_ID=CF_TEST_APP)
        )

    def test_the_live_wire_needs_a_razorpay_pair_and_a_razorpay_shaped_id(self) -> None:
        assert any("PAYMENT_WIRE=live needs" in p for p in _problems(PAYMENT_WIRE="live"))
        assert any(
            "must be a Razorpay key id" in p
            for p in _problems(
                PAYMENT_WIRE="live", RAZORPAY_KEY_ID="nope", RAZORPAY_KEY_SECRET=SECRET
            )
        )

    def test_test_keys_never_reach_a_live_wire_on_a_production_box(self) -> None:
        problems = _problems(
            PAYMENT_WIRE="live", RAZORPAY_KEY_ID=RZP_TEST, RAZORPAY_KEY_SECRET=SECRET
        )
        assert any("test keys" in p and "production box" in p for p in problems)

    def test_live_keys_need_the_allow_flag_and_then_pass(self) -> None:
        refused = _problems(
            PAYMENT_WIRE="live", RAZORPAY_KEY_ID=RZP_LIVE, RAZORPAY_KEY_SECRET=SECRET
        )
        assert any("PAYMENT_ALLOW_LIVE_KEYS" in p for p in refused)
        assert (
            _problems(
                PAYMENT_WIRE="live",
                PAYMENT_ALLOW_LIVE_KEYS="true",
                RAZORPAY_KEY_ID=RZP_LIVE,
                RAZORPAY_KEY_SECRET=SECRET,
            )
            == []
        )

    def test_sandbox_never_reaches_this_production_box(self) -> None:
        problems = _problems(
            CASHFREE_ENV="sandbox", CASHFREE_APP_ID=CF_TEST_APP, CASHFREE_SECRET_KEY=CF_TEST_SECRET
        )
        assert any("sandbox is refused on a production box" in p for p in problems)

    def test_cashfree_production_needs_keys_the_flag_and_non_test_keys(self) -> None:
        assert any("needs CASHFREE_APP_ID" in p for p in _problems(CASHFREE_ENV="production"))
        flag = _problems(
            CASHFREE_ENV="production",
            CASHFREE_APP_ID=CF_PROD_APP,
            CASHFREE_SECRET_KEY=CF_PROD_SECRET,
        )
        assert any("PAYMENT_ALLOW_LIVE_KEYS" in p for p in flag)
        test_keys = _problems(
            CASHFREE_ENV="production",
            PAYMENT_ALLOW_LIVE_KEYS="true",
            CASHFREE_APP_ID=CF_TEST_APP,
            CASHFREE_SECRET_KEY=CF_TEST_SECRET,
        )
        assert any("test-shaped" in p for p in test_keys)
        assert (
            _problems(
                CASHFREE_ENV="production",
                PAYMENT_ALLOW_LIVE_KEYS="true",
                PAYMENT_PROVIDER="cashfree",
                CASHFREE_APP_ID=CF_PROD_APP,
                CASHFREE_SECRET_KEY=CF_PROD_SECRET,
            )
            == []
        )

    def test_a_production_shaped_cashfree_key_is_refused_unless_allowed_on_any_switch(self) -> None:
        problems = _problems(CASHFREE_APP_ID=CF_PROD_APP, CASHFREE_SECRET_KEY=CF_PROD_SECRET)
        assert any("production key" in p and "PAYMENT_ALLOW_LIVE_KEYS" in p for p in problems)

    @pytest.mark.parametrize("cf_env", ["stub", "sandbox"])
    def test_cashfree_as_the_default_needs_the_real_environment(self, cf_env: str) -> None:
        problems = _problems(PAYMENT_PROVIDER="cashfree", CASHFREE_ENV=cf_env)
        assert any(
            "PAYMENT_PROVIDER=cashfree" in p and "CASHFREE_ENV=production" in p for p in problems
        )

    def test_no_problem_ever_contains_a_value(self) -> None:
        problems = _problems(
            PAYMENT_WIRE="live",
            PAYMENT_PROVIDER="cashfree",
            CASHFREE_ENV="sandbox",
            RAZORPAY_KEY_ID=RZP_TEST,
            RAZORPAY_KEY_SECRET=SECRET,
            CASHFREE_APP_ID=CF_TEST_APP,
            CASHFREE_SECRET_KEY=CF_TEST_SECRET,
        )
        assert problems, "the scenario is meant to be refused"
        text = "\n".join(problems)
        for value in (SECRET, RZP_TEST, CF_TEST_APP, CF_TEST_SECRET):
            assert value not in text


#: The staging box's test-mode lines (RUNBOOK.md, 'Payments: test mode on a staging box').
STAGING_TEST_MODE = {
    "RAZORPAY_KEY_ID": RZP_TEST,
    "RAZORPAY_KEY_SECRET": SECRET,
    "PAYMENT_WIRE": "live",
    "CASHFREE_APP_ID": CF_TEST_APP,
    "CASHFREE_SECRET_KEY": CF_TEST_SECRET,
    "CASHFREE_ENV": "sandbox",
    "PAYMENT_ALLOW_TEST_KEYS": "true",
}


class TestTheStagingTestKeysOverride:
    """`PAYMENT_ALLOW_TEST_KEYS` (2026-10-06): preflight mirrors the service — off, nothing changes;
    on, test keys and the sandbox pass on this production-labelled box; beside live money,
    refused."""

    @pytest.mark.parametrize("provider", ["razorpay", "cashfree"])
    def test_the_runbooks_staging_lines_pass_for_either_default_provider(
        self, provider: str
    ) -> None:
        assert _problems(**STAGING_TEST_MODE, PAYMENT_PROVIDER=provider) == []

    @pytest.mark.parametrize("flag", ["", "false", "False", "0", "no", "off"])
    def test_off_the_test_keys_are_refused_exactly_as_before(self, flag: str) -> None:
        problems = _problems(**{**STAGING_TEST_MODE, "PAYMENT_ALLOW_TEST_KEYS": flag})
        assert any("test keys" in p and "production box" in p for p in problems), problems
        assert any("sandbox is refused on a production box" in p for p in problems), problems

    @pytest.mark.parametrize("flag", ["true", "True", "1", "yes", "on"])
    def test_on_is_read_the_way_pydantic_reads_a_boolean(self, flag: str) -> None:
        assert _problems(**{**STAGING_TEST_MODE, "PAYMENT_ALLOW_TEST_KEYS": flag}) == []

    def test_a_value_that_is_not_a_boolean_is_refused_by_name(self) -> None:
        problems = _problems(PAYMENT_ALLOW_TEST_KEYS="maybe")
        assert problems == ["PAYMENT_ALLOW_TEST_KEYS must be true or false"]

    def test_the_flag_without_keys_is_harmless(self) -> None:
        assert _problems(PAYMENT_ALLOW_TEST_KEYS="true") == []

    @pytest.mark.parametrize(
        "live",
        [
            pytest.param({"PAYMENT_ALLOW_LIVE_KEYS": "true"}, id="both-flags"),
            pytest.param({"PAYMENT_ALLOW_LIVE_KEYS": "True"}, id="both-flags-as-pydantic-reads"),
            pytest.param({"RAZORPAY_KEY_ID": RZP_LIVE, "RAZORPAY_KEY_SECRET": SECRET}, id="rzp"),
            pytest.param(
                {"CASHFREE_APP_ID": CF_PROD_APP, "CASHFREE_SECRET_KEY": CF_PROD_SECRET}, id="cf"
            ),
            pytest.param(
                {
                    "CASHFREE_ENV": "production",
                    "PAYMENT_ALLOW_LIVE_KEYS": "true",
                    "CASHFREE_APP_ID": CF_PROD_APP,
                    "CASHFREE_SECRET_KEY": CF_PROD_SECRET,
                },
                id="cf-production",
            ),
        ],
    )
    def test_the_flag_beside_live_money_is_refused(self, live: dict[str, str]) -> None:
        problems = _problems(PAYMENT_ALLOW_TEST_KEYS="true", **live)
        assert any(p.startswith("PAYMENT_ALLOW_TEST_KEYS") for p in problems), problems

    def test_the_flag_relaxes_nothing_else(self) -> None:
        flag = {"PAYMENT_ALLOW_TEST_KEYS": "true"}
        assert any(
            "PAYMENT_PROVIDER=cashfree" in p
            for p in _problems(**flag, PAYMENT_PROVIDER="cashfree", CASHFREE_ENV="stub")
        )
        assert any("must be set together" in p for p in _problems(**flag, RAZORPAY_KEY_ID=RZP_TEST))
        assert any("PAYMENT_WIRE=live needs" in p for p in _problems(**flag, PAYMENT_WIRE="live"))
        assert any("needs CASHFREE_APP_ID" in p for p in _problems(**flag, CASHFREE_ENV="sandbox"))

    def test_no_problem_ever_contains_a_value(self) -> None:
        problems = _problems(
            **STAGING_TEST_MODE,
            PAYMENT_ALLOW_LIVE_KEYS="true",
            PAYMENT_PROVIDER="cashfree",
        )
        assert problems
        text = "\n".join(problems)
        for value in (SECRET, RZP_TEST, CF_TEST_APP, CF_TEST_SECRET):
            assert value not in text


LIVE_RAZORPAY = {
    "PAYMENT_REQUIRE_LIVE": "true",
    "PAYMENT_ALLOW_LIVE_KEYS": "true",
    "PAYMENT_WIRE": "live",
    "RAZORPAY_KEY_ID": RZP_LIVE,
    "RAZORPAY_KEY_SECRET": SECRET,
}


class TestRequireLive:
    """`PAYMENT_REQUIRE_LIVE` (PLATFORM-23): preflight FAILS (a problem line goes through `bad`) on
    the stub, on test keys and with the test-mode override; it passes live money."""

    def test_off_the_stub_still_passes(self) -> None:
        assert _problems(PAYMENT_REQUIRE_LIVE="false") == []

    def test_live_money_passes(self) -> None:
        assert _problems(**LIVE_RAZORPAY) == []

    def test_the_stub_wire_fails(self) -> None:
        assert any("needs PAYMENT_WIRE=live" in p for p in _problems(PAYMENT_REQUIRE_LIVE="true"))
        cashfree = _problems(PAYMENT_REQUIRE_LIVE="true", PAYMENT_PROVIDER="cashfree")
        assert any("PAYMENT_REQUIRE_LIVE=true needs CASHFREE_ENV=production" in p for p in cashfree)

    def test_test_keys_fail(self) -> None:
        problems = _problems(
            **LIVE_RAZORPAY, CASHFREE_APP_ID=CF_TEST_APP, CASHFREE_SECRET_KEY=CF_TEST_SECRET
        )
        assert any("refuses test keys" in p for p in problems), problems

    def test_the_test_mode_override_fails(self) -> None:
        problems = _problems(**STAGING_TEST_MODE, PAYMENT_REQUIRE_LIVE="true")
        assert any("refuses PAYMENT_ALLOW_TEST_KEYS" in p for p in problems), problems
        assert any("refuses test keys" in p for p in problems), problems

    def test_a_value_that_is_not_a_boolean_is_refused_by_name(self) -> None:
        problems = _problems(PAYMENT_REQUIRE_LIVE="maybe")
        assert problems == ["PAYMENT_REQUIRE_LIVE must be true or false"]


def _test_mode_warning_block() -> str:
    text = PREFLIGHT.read_text()
    block = text[text.index("if payment_test_keys_allowed; then") :]
    return block[: block.index("\nfi\n") + 4]


def _preflight_warning(**env: str) -> subprocess.CompletedProcess[str]:
    """Preflight's test-mode block, run as preflight runs it (`set -euo pipefail`, the library
    sourced), with only `env` set."""
    script = f'set -euo pipefail\nsource "{LIB}"\n{_test_mode_warning_block()}'
    clean = {k: v for k, v in os.environ.items() if k not in ALL_KEYS}
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={**clean, **env}
    )


class TestPreflightWarnsLoudlyInTestMode:
    def test_on_it_warns_loudly_and_never_fails(self) -> None:
        done = _preflight_warning(**STAGING_TEST_MODE)
        assert done.returncode == 0, done.stderr
        assert "warn" in done.stderr and "TEST MODE" in done.stderr
        assert "PAYMENT_ALLOW_TEST_KEYS is ON" in done.stderr and "FAIL" not in done.stderr
        assert "Payments: test mode on a staging box" in done.stderr
        for value in (SECRET, RZP_TEST, CF_TEST_APP, CF_TEST_SECRET):
            assert value not in done.stderr + done.stdout

    @pytest.mark.parametrize("flag", [None, "", "false", "0"])
    def test_off_or_unset_it_says_nothing(self, flag: str | None) -> None:
        env = {} if flag is None else {"PAYMENT_ALLOW_TEST_KEYS": flag}
        done = _preflight_warning(**env)
        assert done.returncode == 0 and done.stderr == "" and done.stdout == ""

    def test_the_warning_is_wired_after_the_rules(self) -> None:
        text = PREFLIGHT.read_text()
        assert text.index("payment_switch_problems)") < text.index("if payment_test_keys_allowed")


class TestTheMatrixDocumentsEveryKey:
    @pytest.mark.parametrize("key", [*SWITCHES, "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"])
    def test_the_source_matrix_has_a_row_and_no_key_is_called_inert(self, key: str) -> None:
        rows = [ln for ln in MATRIX.read_text().splitlines() if ln.startswith(f"| `{key}`")]
        assert rows, f"{key} has no row in ENV_SOURCE_MATRIX.md"
        for row in rows:
            # They were "inert" while the wire was a stub; the matrix must not still say so.
            for stale in (
                "payments inert",
                "changes nothing but the preflight warning",
                "NOT USED FOR ANY CALL",
            ):
                assert stale not in row, (key, stale)


def _runbook_section(title: str) -> str:
    text = RUNBOOK.read_text()
    body = text.split(f"### {title}\n", 1)[1]
    return body.split("\n### ", 1)[0]


class TestTheRunbookSaysExactlyWhatToSet:
    """The owner sets test mode by hand on the box, from this section alone."""

    SECTION = "Payments: test mode on a staging box"

    def test_the_turn_on_lines_are_exactly_the_staging_set(self) -> None:
        section = _runbook_section(self.SECTION)
        turn_on = section.split("**Turning it on.**", 1)[1].split("**Turning it off**", 1)[0]
        block = turn_on.split("```bash\n", 1)[1].split("```", 1)[0]
        names = {line.split("=", 1)[0] for line in block.splitlines() if "=" in line}
        assert names == {*STAGING_TEST_MODE, "PAYMENT_PROVIDER"}
        for exact in (
            "PAYMENT_ALLOW_TEST_KEYS=true",
            "PAYMENT_WIRE=live",
            "CASHFREE_ENV=sandbox",
        ):
            assert exact in block.splitlines(), exact

    def test_it_says_how_to_turn_it_off_and_never_holds_a_key(self) -> None:
        section = _runbook_section(self.SECTION)
        turn_off = section.split("**Turning it off**", 1)[1]
        for line in ("PAYMENT_WIRE=stub", "CASHFREE_ENV=stub", "PAYMENT_ALLOW_TEST_KEYS"):
            assert line in turn_off, line
        assert "PAYMENT_ALLOW_LIVE_KEYS" in section and "never both" in section.lower()
        # Placeholders only: the secret-shape scan guards real shapes; this guards the obvious.
        for line in section.splitlines():
            for key in ("RAZORPAY_KEY_SECRET=", "CASHFREE_SECRET_KEY=", "CASHFREE_APP_ID="):
                if line.startswith(key):
                    value = line.split("=", 1)[1]
                    assert value == "" or value.startswith("<"), line

    def test_the_turning_a_provider_on_section_points_at_it(self) -> None:
        assert self.SECTION in _runbook_section("Payments: turning a provider on")
