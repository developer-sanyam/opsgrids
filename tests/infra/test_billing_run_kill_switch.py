"""The billing run is off on the edge unless `.env` turns it on (ADR-0005 addendum, review fix 6).

Its first passes on a box with history convert every expired trial and bill every overdue period,
so the default everywhere a deploy reads it is `false`: the compose interpolation default, the
committed example (which documents it commented out, so preflight does not require it), and
preflight's warning when it is anything but `true`. Checked here, in CI's static job, because a
default flipped back to `true` would ship silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

EDGE_DIR = Path(__file__).resolve().parents[2] / "infrastructure/edge"


def test_compose_defaults_the_billing_run_off() -> None:
    compose = yaml.safe_load((EDGE_DIR / "compose.prod.yml").read_text())
    env = compose["services"]["subscription-worker"]["environment"]
    assert env["BILLING_RUN_ENABLED"] == "${BILLING_RUN_ENABLED:-false}"


def test_the_example_documents_it_without_requiring_it() -> None:
    example = (EDGE_DIR / ".env.example").read_text()
    assert "# BILLING_RUN_ENABLED=true" in example
    # preflight requires every uncommented key; this one must stay optional.
    assert not re.search(r"^BILLING_RUN_ENABLED=", example, flags=re.MULTILINE)


def test_preflight_warns_unless_it_is_on() -> None:
    preflight = (EDGE_DIR / "preflight.sh").read_text()
    assert '"${BILLING_RUN_ENABLED:-}" != "true"' in preflight
    assert "the billing run is OFF" in preflight


# --- the cutover (final review, fix 3) ------------------------------------------------------


def test_compose_passes_the_cutover_to_the_worker() -> None:
    compose = yaml.safe_load((EDGE_DIR / "compose.prod.yml").read_text())
    env = compose["services"]["subscription-worker"]["environment"]
    assert env["BILLING_RUN_NOT_BEFORE"] == "${BILLING_RUN_NOT_BEFORE:-}"


def test_the_example_documents_the_cutover_without_requiring_it() -> None:
    example = (EDGE_DIR / ".env.example").read_text()
    assert re.search(
        r"^# BILLING_RUN_NOT_BEFORE=\d{4}-\d{2}-\d{2}T.*[+-]\d{2}:\d{2}$", example, re.M
    )
    assert not re.search(r"^BILLING_RUN_NOT_BEFORE=", example, flags=re.MULTILINE)


def test_preflight_fails_an_enabled_run_without_a_well_formed_cutover() -> None:
    preflight = (EDGE_DIR / "preflight.sh").read_text()
    block = preflight[preflight.index('if [ "${BILLING_RUN_ENABLED:-}" != "true" ]') :]
    block = block[: block.index("\nfi\n") + 4]
    assert 'bad "BILLING_RUN_ENABLED=true but BILLING_RUN_NOT_BEFORE is empty' in block
    assert 'bad "BILLING_RUN_NOT_BEFORE must be an ISO instant with a UTC offset' in block
    # The shape check it applies accepts an offset or Z and refuses a naive time.
    pattern = re.search(r"grep -Eq '([^']+)'", block)
    assert pattern is not None
    shape = re.compile(pattern.group(1))
    assert shape.match("2026-10-01T09:00:00+05:30")
    assert shape.match("2026-10-01T03:30Z")
    assert not shape.match("2026-10-01T09:00:00")
    assert not shape.match("2026-10-01")
