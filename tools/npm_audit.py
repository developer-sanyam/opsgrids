#!/usr/bin/env python3
"""Refuse a known high or critical vulnerability in what the frontend ships.

`pnpm audit --prod --audit-level high` does the looking; this adds the one thing it lacks — an
allow-list where every accepted advisory carries a reason and an expiry date. pnpm's own
`auditConfig.ignoreGhsas` has neither, so an advisory ignored "until the fix lands" is ignored
for ever. Here an accepted advisory fails the gate again the day its entry expires, and
somebody has to look at it and decide a second time.

The allow-list is frontend/audit-allowlist.toml:

    [[accepted]]
    advisory = "GHSA-xxxx-xxxx-xxxx"
    package  = "some-package"
    reason   = "Reachable only from the build; no runtime path. Fixed upstream in 2.1."
    expires  = 2026-12-31

`--prod` because the question is what reaches a browser. An advisory in a test runner or a
linter is real, but it is not a vulnerability in the product, and `pnpm audit` without
`--prod` is there for anyone who wants to look at the dev tree.

    python tools/npm_audit.py        # make frontend-audit
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
ALLOWLIST = FRONTEND / "audit-allowlist.toml"

# pnpm's severities, lowest first. The gate refuses this level and everything above it.
SEVERITIES = ["info", "low", "moderate", "high", "critical"]
THRESHOLD = "high"


@dataclass(frozen=True)
class Advisory:
    ghsa: str
    package: str
    severity: str
    title: str
    url: str


@dataclass(frozen=True)
class Accepted:
    advisory: str
    package: str
    reason: str
    expires: dt.date


def at_or_above_threshold(severity: str) -> bool:
    try:
        return SEVERITIES.index(severity) >= SEVERITIES.index(THRESHOLD)
    except ValueError:
        # A severity pnpm has never used before is treated as the worst case, not ignored.
        return True


def parse_advisories(report: dict[str, Any]) -> list[Advisory]:
    return [
        Advisory(
            ghsa=str(entry.get("github_advisory_id") or entry.get("id")),
            package=str(entry.get("module_name", "?")),
            severity=str(entry.get("severity", "critical")),
            title=str(entry.get("title", "")),
            url=str(entry.get("url", "")),
        )
        for entry in report.get("advisories", {}).values()
    ]


def load_allowlist(path: Path) -> list[Accepted]:
    if not path.exists():
        return []
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    accepted: list[Accepted] = []
    for entry in raw.get("accepted", []):
        missing = {"advisory", "package", "reason", "expires"} - entry.keys()
        if missing:
            raise SystemExit(f"{path.name}: an entry is missing {sorted(missing)}: {entry}")
        if not isinstance(entry["expires"], dt.date):
            raise SystemExit(f"{path.name}: `expires` must be a TOML date (2026-12-31): {entry}")
        if not str(entry["reason"]).strip():
            raise SystemExit(f"{path.name}: {entry['advisory']} has an empty reason")
        accepted.append(
            Accepted(entry["advisory"], entry["package"], entry["reason"], entry["expires"])
        )
    return accepted


def evaluate(
    advisories: list[Advisory], accepted: list[Accepted], today: dt.date
) -> tuple[list[str], list[str]]:
    """(failures, notes). Pure, so the rules are testable without the network."""
    failures: list[str] = []
    notes: list[str] = []
    by_key = {(a.advisory, a.package): a for a in accepted}

    for entry in accepted:
        if entry.expires < today:
            failures.append(
                f"allow-list entry {entry.advisory} ({entry.package}) expired on "
                f"{entry.expires.isoformat()} — fix it, or re-accept it with a new reason and date"
            )

    reported: set[tuple[str, str]] = set()
    for advisory in advisories:
        key = (advisory.ghsa, advisory.package)
        reported.add(key)
        if not at_or_above_threshold(advisory.severity):
            continue
        match = by_key.get(key)
        if match is None:
            failures.append(
                f"{advisory.severity}: {advisory.package} — {advisory.title} "
                f"({advisory.ghsa}) {advisory.url}"
            )
        elif match.expires >= today:
            notes.append(
                f"accepted until {match.expires.isoformat()}: {advisory.package} "
                f"{advisory.ghsa} — {match.reason}"
            )

    for key, entry in by_key.items():
        if key not in reported:
            notes.append(
                f"stale allow-list entry {entry.advisory} ({entry.package}): no longer reported, "
                "delete it"
            )
    return failures, notes


def run_audit() -> dict[str, Any]:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise SystemExit("pnpm is not on PATH — install it (corepack enable) and run pnpm install")
    # pnpm exits non-zero whenever it finds anything, so the exit code is not the answer; a
    # report that does not parse is, and it fails closed.
    result = subprocess.run(  # noqa: S603 — a fixed argv, the binary resolved just above
        [pnpm, "audit", "--prod", "--json"],
        cwd=FRONTEND,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        report: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"pnpm audit returned no report:\n{result.stdout}{result.stderr}") from exc
    if "error" in report:
        raise SystemExit(f"pnpm audit failed: {json.dumps(report['error'])}")
    return report


def main() -> int:
    report = run_audit()
    advisories = parse_advisories(report)
    failures, notes = evaluate(
        advisories, load_allowlist(ALLOWLIST), dt.datetime.now(dt.UTC).date()
    )
    for note in notes:
        print(f"  note  {note}")
    for failure in failures:
        print(f"  FAIL  {failure}")
    total = report.get("metadata", {}).get("totalDependencies", "?")
    if failures:
        print(
            f"\n{len(failures)} problem(s) at or above {THRESHOLD} in {total} production "
            f"packages. Upgrade, or accept it in {ALLOWLIST.relative_to(ROOT)} with a reason "
            "and an expiry."
        )
        return 1
    print(f"{total} production packages, nothing at or above {THRESHOLD} unaccepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
