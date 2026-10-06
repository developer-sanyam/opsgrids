"""Shared rules for the owner-run payment smoke tools (`payments_smoke_razorpay.py`,
`payments_smoke_cashfree.py`) — ADR-0032, and "Payments: turning a provider on" in the RUNBOOK.

These tools exist to settle what the design could only *assume* about a provider (the real HTTP
status of a bad key, how a duplicate receipt is refused, which payment fields carry a person, what
header names a webhook delivery really uses). They call a **real** provider, in **test mode**, from
the owner's own machine, and they are the one place in this repository that does. So the rules are
the point, and they live here once rather than being re-decided per tool:

* **Opt-in.** Nothing is called without `--confirm`. Without it the tool prints what it would do and
  exits 2. A smoke tool that runs by accident is a live call by accident.
* **Never in CI, never in a test run, never against production.** Refused when `CI` or
  `GITHUB_ACTIONS` is set, when running under pytest, and when `ENVIRONMENT` names a deployed
  environment (`production`, `staging`, `uat`, `ci`). Run it on a laptop, with `uv run`, never
  through `make` (the Makefile re-exports the root `.env`).
* **Keys come from the process environment and nowhere else** — not from `.env`, not from the
  service `Settings`, not from an argument (an argument lands in shell history). Test keys only: a
  key that is not provably test-mode is refused before a single request is built.
* **It prints only identifiers, HTTP statuses, stable error codes, counts, booleans and field
  *names*.** Never a response body, a header value, a description or message, an amount tied to a
  person, a customer detail or a credential. Every value goes through `Reporter.value`, which lets
  through only short strings of `[A-Za-z0-9_.:-]` and replaces anything else — and any string that
  contains a credential this run was given — with `<redacted>`. Identifiers are truncated.
* **A unique receipt or order id per run**, so a re-run never collides with an earlier one, and
  amounts are INR and at least 100 paise (Re 1) — real Orders are created, in test mode, and cost
  nothing.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable, Mapping
from typing import Any, TextIO

#: Environments a smoke tool refuses to run in: anything deployed.
DEPLOYED_ENVIRONMENTS = frozenset({"production", "staging", "uat", "ci"})
#: What `Reporter.value` lets through verbatim.
_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{0,64}$")
#: What `Reporter.names` lets through: a plain field or header name.
_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")


class Refused(Exception):
    """A guard stopped the run before anything was built or sent. The message names the rule and
    never a value."""


def guard(
    environ: Mapping[str, str],
    *,
    confirmed: bool,
    modules: Mapping[str, Any] | None = None,
) -> None:
    """Raise `Refused` unless this run may call a provider. Pure: reads only what it is given."""
    loaded = sys.modules if modules is None else modules
    if environ.get("CI") or environ.get("GITHUB_ACTIONS"):
        raise Refused("refused: this is a CI environment (CI or GITHUB_ACTIONS is set)")
    if "pytest" in loaded or environ.get("PYTEST_CURRENT_TEST"):
        raise Refused("refused: smoke tools never run under pytest")
    deployed = (environ.get("ENVIRONMENT") or "").strip().lower()
    if deployed in DEPLOYED_ENVIRONMENTS:
        raise Refused("refused: ENVIRONMENT names a deployed environment")
    if not confirmed:
        raise Refused("refused: pass --confirm to call the provider (nothing was called)")


def short(identifier: object) -> str:
    """An identifier, truncated: enough to find it in the provider's dashboard, never the whole."""
    text = str(identifier or "")
    return text if len(text) <= 14 else text[:14] + "..."


class Reporter:
    """Writes one `step=... key=value ...` line per observation, and nothing else."""

    def __init__(self, out: TextIO, *, secrets: list[str]) -> None:
        self._out = out
        self._secrets = [s for s in secrets if s]

    def value(self, value: object) -> str:
        if value is None:
            return "none"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return str(value)
        text = str(value)
        if any(secret in text for secret in self._secrets) or not _SAFE.match(text):
            return "<redacted>"
        return text

    def line(self, step: str, **fields: object) -> None:
        shown = " ".join(f"{name}={self.value(v)}" for name, v in fields.items())
        self._out.write(f"{step} {shown}".rstrip() + "\n")
        self._out.flush()

    def names(self, step: str, fields: Iterable[object]) -> None:
        """The *names* of a payload's keys, sorted — which fields a provider's entity carries is the
        question; what they hold is never printed. A name that is not a plain identifier is counted
        but not shown."""
        shown: list[str] = []
        hidden = 0
        for candidate in fields:
            name = str(candidate)
            if _FIELD_NAME.match(name) and not any(s in name for s in self._secrets):
                shown.append(name)
            else:
                hidden += 1
        self._out.write(f"{step} count={len(shown) + hidden} names={','.join(sorted(shown))}\n")
        self._out.flush()

    def text(self, message: str) -> None:
        """A fixed, author-written sentence: an instruction or a verdict, never a response."""
        self._out.write(message + "\n")
        self._out.flush()


def read_env(environ: Mapping[str, str], *names: str) -> dict[str, str]:
    """The named variables from the process environment, or a `Refused` naming the missing ones —
    never their values."""
    found = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name, value in found.items() if not value]
    if missing:
        raise Refused("refused: set " + ", ".join(missing) + " in the process environment")
    return found


def default_environ() -> Mapping[str, str]:
    return os.environ
