"""NOTIF-A fix round: preflight catches the two SMTP TLS combinations notification-service's own
`Settings.model_post_init` and `SmtpTransport.__init__` refuse to start with, the same way
`test_preflight_secrets.py` pins the secret-strength rules (`lib/smtp-tls.sh`).

Before this, the box could carry `SMTP_USE_TLS=true` together with `SMTP_USE_SSL=true` — SMTP_USE_
SSL was ops-monitor's setting only and notification ignored it — or set `EMAIL_TRANSPORT=smtp`
with a credential and neither flag, and nothing on the box said so until the notification worker
container failed to start.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
LIB = EDGE_DIR / "lib/smtp-tls.sh"
PREFLIGHT = EDGE_DIR / "preflight.sh"

_ENV_KEYS = {"EMAIL_TRANSPORT", "SMTP_USE_TLS", "SMTP_USE_SSL", "SMTP_USERNAME", "SMTP_PASSWORD"}


def _problems(**env: str) -> list[str]:
    script = f'source "{LIB}"; smtp_tls_problems'
    clean = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True, env={**clean, **env}
    )
    return [line for line in result.stdout.splitlines() if line]


# --------------------------------------------------------------------- is_true


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "Yes", "on", "y", "t"])
def test_is_true_accepts_every_pydantic_truthy_spelling(value: str) -> None:
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; is_true "{value}" false && echo yes || echo no'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "yes"


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "n", "f", "garbage"])
def test_is_true_rejects_everything_else(value: str) -> None:
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; is_true "{value}" true && echo yes || echo no'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "no"


def test_is_true_falls_back_to_the_default_when_the_value_is_empty() -> None:
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; is_true "" true && echo yes || echo no'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "yes"


# --------------------------------------------------------------------- smtp_tls_problems


def test_the_ordinary_default_box_has_no_problems() -> None:
    """Nothing set at all: `EMAIL_TRANSPORT` defaults to `log`, which sends nothing and needs no
    TLS decision — the state of a box that has not configured email yet."""
    assert _problems() == []


def test_compose_defaults_starttls_and_no_ssl_has_no_problems() -> None:
    """`compose.prod.yml`'s own `${SMTP_USE_TLS:-true}` / `${SMTP_USE_SSL:-false}`, with a real
    SMTP setup — an unset key on the box must behave exactly as that default will."""
    assert (
        _problems(
            EMAIL_TRANSPORT="smtp",
            SMTP_USERNAME="info@opsgrids.com",
            SMTP_PASSWORD="app-password",  # pragma: allowlist secret
        )
        == []
    )


def test_both_tls_and_ssl_true_is_a_problem() -> None:
    problems = _problems(SMTP_USE_TLS="true", SMTP_USE_SSL="true")
    assert len(problems) == 1
    assert "both true" in problems[0]
    assert "SMTP_USE_TLS" in problems[0]
    assert "SMTP_USE_SSL" in problems[0]


def test_both_true_is_a_problem_even_when_ssl_relies_on_its_own_default() -> None:
    """`SMTP_USE_TLS` explicit, `SMTP_USE_SSL` unset — but the *TLS* default is also true, so an
    operator who sets only `SMTP_USE_SSL=true` on top of the box's own default still collides."""
    assert _problems(SMTP_USE_SSL="true") == _problems(SMTP_USE_TLS="true", SMTP_USE_SSL="true")


def test_smtp_with_a_credential_and_neither_flag_is_a_problem() -> None:
    problems = _problems(
        EMAIL_TRANSPORT="smtp",
        SMTP_USE_TLS="false",
        SMTP_USE_SSL="false",
        SMTP_USERNAME="info@opsgrids.com",
        SMTP_PASSWORD="app-password",  # pragma: allowlist secret
    )
    assert len(problems) == 1
    assert "neither" in problems[0]
    assert "unencrypted" in problems[0]


def test_smtp_with_only_a_username_and_neither_flag_is_still_a_problem() -> None:
    """`SmtpTransport` refuses on `username and password` together, but a box with a username and
    no password yet is mid-setup, not safe — and a warning that waits for both to be filled in is
    a warning that never fires while the credential is still half-typed."""
    assert (
        _problems(
            EMAIL_TRANSPORT="smtp",
            SMTP_USE_TLS="false",
            SMTP_USE_SSL="false",
            SMTP_USERNAME="info@opsgrids.com",
        )
        != []
    )


def test_smtp_with_ssl_true_and_a_credential_has_no_problem() -> None:
    assert (
        _problems(
            EMAIL_TRANSPORT="smtp",
            SMTP_USE_TLS="false",
            SMTP_USE_SSL="true",
            SMTP_USERNAME="info@opsgrids.com",
            SMTP_PASSWORD="app-password",  # pragma: allowlist secret
        )
        == []
    )


def test_smtp_with_no_credential_and_neither_flag_has_no_problem() -> None:
    """A local relay that authenticates by network position — `SmtpTransport` itself allows this;
    preflight must not invent a rule the service does not enforce."""
    assert _problems(EMAIL_TRANSPORT="smtp", SMTP_USE_TLS="false", SMTP_USE_SSL="false") == []


def test_a_non_smtp_transport_with_stray_credentials_and_neither_flag_has_no_problem() -> None:
    """`EMAIL_TRANSPORT=log` never opens a connection at all — an unused `SMTP_USERNAME` left over
    from a previous configuration is not this check's business."""
    assert (
        _problems(
            EMAIL_TRANSPORT="log",
            SMTP_USE_TLS="false",
            SMTP_USE_SSL="false",
            SMTP_USERNAME="leftover@opsgrids.com",
            SMTP_PASSWORD="leftover",  # pragma: allowlist secret
        )
        == []
    )


# --------------------------------------------------------------------- wiring


def test_preflight_sources_the_library_and_reports_each_problem() -> None:
    text = PREFLIGHT.read_text()
    assert ". ./lib/smtp-tls.sh" in text
    assert "smtp_tls_problems" in text
    assert 'bad "$problem"' in text


def test_the_library_has_no_side_effects_of_its_own() -> None:
    """The same discipline `lib/secret-strength.sh`'s header states: no `cd`, no `set -e` — a
    library sourced into preflight's own shell must not change its caller's behaviour by being
    sourced, only by being called."""
    code_lines = [line for line in LIB.read_text().splitlines() if not line.strip().startswith("#")]
    assert not any(line.strip() == "cd" or line.strip().startswith("cd ") for line in code_lines)
    assert not any(line.strip().startswith("set -e") for line in code_lines)
