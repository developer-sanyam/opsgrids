"""Item 4 of the final security review: preflight checks the secrets' strength the way the
services do at startup (`lib/secret-strength.sh`), so a short or reused key stops the deploy
*before* anything changes instead of surfacing as an auth container that will not start.

The rules mirrored (read from `platform_core.config` and `services/auth/app/config.py`):
`INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY` and `ADMIN_GATEWAY_HOP_KEY` each at least 32
*bytes*, and the hop key must differ from the internal token. A missing key is preflight's
generic "every key in .env.example is set" check — asserted below to cover this one.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
LIB = EDGE_DIR / "lib/secret-strength.sh"
PREFLIGHT = EDGE_DIR / "preflight.sh"

GOOD_INTERNAL = "i" * 40
GOOD_PII = "p" * 40
GOOD_HOP = "h" * 40


def _problems(**env: str) -> list[str]:
    script = f'source "{LIB}"; secret_strength_problems'
    clean = {
        k: v
        for k, v in os.environ.items()
        if k not in {"INTERNAL_SERVICE_TOKEN", "PII_ENCRYPTION_KEY", "ADMIN_GATEWAY_HOP_KEY"}
    }
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True, env={**clean, **env}
    )
    return [line for line in result.stdout.splitlines() if line]


def test_three_good_distinct_secrets_have_no_problems() -> None:
    assert (
        _problems(
            INTERNAL_SERVICE_TOKEN=GOOD_INTERNAL,
            PII_ENCRYPTION_KEY=GOOD_PII,
            ADMIN_GATEWAY_HOP_KEY=GOOD_HOP,
        )
        == []
    )


@pytest.mark.parametrize(
    "key", ["INTERNAL_SERVICE_TOKEN", "PII_ENCRYPTION_KEY", "ADMIN_GATEWAY_HOP_KEY"]
)
def test_a_secret_under_32_bytes_is_refused_by_name(key: str) -> None:
    env = {
        "INTERNAL_SERVICE_TOKEN": GOOD_INTERNAL,
        "PII_ENCRYPTION_KEY": GOOD_PII,
        "ADMIN_GATEWAY_HOP_KEY": GOOD_HOP,
        key: "s" * 31,
    }
    problems = _problems(**env)
    assert problems == [f"{key} is 31 bytes: it must be at least 32"]


def test_exactly_32_bytes_is_accepted() -> None:
    assert (
        _problems(
            INTERNAL_SERVICE_TOKEN="i" * 32,
            PII_ENCRYPTION_KEY=GOOD_PII,
            ADMIN_GATEWAY_HOP_KEY=GOOD_HOP,
        )
        == []
    )


def test_the_length_is_counted_in_bytes_not_characters() -> None:
    """`len(value.encode())` in the services: sixteen two-byte characters are 32 bytes, so they
    pass — and sixteen ASCII characters are 16, so they do not."""
    two_byte = "é" * 16
    assert (
        _problems(
            INTERNAL_SERVICE_TOKEN=two_byte,
            PII_ENCRYPTION_KEY=GOOD_PII,
            ADMIN_GATEWAY_HOP_KEY=GOOD_HOP,
        )
        == []
    )
    assert _problems(
        INTERNAL_SERVICE_TOKEN="a" * 16, PII_ENCRYPTION_KEY=GOOD_PII, ADMIN_GATEWAY_HOP_KEY=GOOD_HOP
    ) == ["INTERNAL_SERVICE_TOKEN is 16 bytes: it must be at least 32"]


def test_a_hop_key_equal_to_the_internal_token_is_refused() -> None:
    shared = "x" * 40
    problems = _problems(
        INTERNAL_SERVICE_TOKEN=shared, PII_ENCRYPTION_KEY=GOOD_PII, ADMIN_GATEWAY_HOP_KEY=shared
    )
    assert problems == ["ADMIN_GATEWAY_HOP_KEY must differ from INTERNAL_SERVICE_TOKEN"]


def test_empty_values_are_left_to_the_missing_keys_check() -> None:
    """No second complaint about a key that is already reported as missing — and two empty
    values are not 'equal' in any sense worth a message."""
    assert (
        _problems(INTERNAL_SERVICE_TOKEN="", PII_ENCRYPTION_KEY="", ADMIN_GATEWAY_HOP_KEY="") == []
    )


def test_preflight_sources_the_library_and_reports_each_problem() -> None:
    text = PREFLIGHT.read_text()
    assert ". ./lib/secret-strength.sh" in text
    assert "secret_strength_problems" in text
    # Each problem line is reported through `bad`, so it fails the preflight.
    assert 'bad "$problem"' in text


def test_the_hop_key_is_a_required_key_so_a_missing_one_is_caught_here() -> None:
    """Preflight derives its required keys from `.env.example` and only lets the ones in
    `optional_empty` be blank; the hop key must be the former, not the latter."""
    example = (EDGE_DIR / ".env.example").read_text().splitlines()
    assert any(line.startswith("ADMIN_GATEWAY_HOP_KEY=") for line in example)
    optional_lines = [
        line for line in PREFLIGHT.read_text().splitlines() if line.startswith("optional_empty=")
    ]
    assert optional_lines, "the allow-list moved — this test needs to follow it"
    assert all("ADMIN_GATEWAY_HOP_KEY" not in line for line in optional_lines)
