"""Pins the decisions inside the gate's own tooling: the licence expression parser, the audit
allow-list's expiry rule, and the frontend port check.

These are the parts of the gate that are logic rather than a call to someone else's tool, so
they are the parts that can be wrong while every row stays green. No network, no pnpm, no
database — each test drives the real function against inputs it builds itself.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their annotations through sys.modules
    spec.loader.exec_module(module)
    return module


licenses = _load("licenses")
npm_audit = _load("npm_audit")
frontend_ports = _load("frontend_ports")

ALLOWED = {"MIT", "Apache-2.0", "CC0-1.0", "ISC License (ISCL)", "MPL-2.0"}


# ---------------------------------------------------------------- licences


def test_an_npm_parenthesised_choice_passes_when_one_side_is_allowed() -> None:
    assert licenses.permitted("(MIT OR GPL-3.0)", ALLOWED)


def test_an_npm_parenthesised_conjunction_needs_both_sides() -> None:
    assert not licenses.permitted("(MIT AND GPL-3.0)", ALLOWED)


def test_a_classifier_carrying_its_own_brackets_still_matches() -> None:
    # The Python side spells ISC this way; stripping brackets first would break it.
    assert licenses.permitted("ISC License (ISCL)", ALLOWED)


def test_copyleft_alone_is_refused() -> None:
    assert not licenses.permitted("AGPL-3.0-or-later", ALLOWED)


# ---------------------------------------------------------------- audit allow-list

TODAY = dt.date(2026, 9, 23)
HIGH = npm_audit.Advisory("GHSA-aaaa", "left-pad", "high", "bad", "https://example.test/a")
MODERATE = npm_audit.Advisory("GHSA-bbbb", "right-pad", "moderate", "meh", "https://example.test/b")


def test_an_unaccepted_high_advisory_fails() -> None:
    failures, _ = npm_audit.evaluate([HIGH], [], TODAY)
    assert len(failures) == 1
    assert "GHSA-aaaa" in failures[0]


def test_a_moderate_advisory_is_below_the_threshold() -> None:
    failures, _ = npm_audit.evaluate([MODERATE], [], TODAY)
    assert failures == []


def test_an_accepted_advisory_passes_until_its_expiry() -> None:
    entry = npm_audit.Accepted("GHSA-aaaa", "left-pad", "no runtime path", TODAY)
    failures, notes = npm_audit.evaluate([HIGH], [entry], TODAY)
    assert failures == []
    assert any("accepted until" in note for note in notes)


def test_an_expired_acceptance_fails_again() -> None:
    entry = npm_audit.Accepted("GHSA-aaaa", "left-pad", "no runtime path", TODAY - dt.timedelta(1))
    failures, _ = npm_audit.evaluate([HIGH], [entry], TODAY)
    assert any("expired" in failure for failure in failures)


def test_an_acceptance_for_another_package_does_not_cover_this_one() -> None:
    entry = npm_audit.Accepted("GHSA-aaaa", "other-package", "no runtime path", TODAY)
    failures, _ = npm_audit.evaluate([HIGH], [entry], TODAY)
    assert len(failures) == 1


def test_an_unknown_severity_is_treated_as_the_worst_case() -> None:
    odd = npm_audit.Advisory("GHSA-cccc", "x", "catastrophic", "?", "")
    failures, _ = npm_audit.evaluate([odd], [], TODAY)
    assert len(failures) == 1


# ---------------------------------------------------------------- frontend ports


def _app(root: Path, name: str, dev: int, preview: int, config_port: int | None = None) -> None:
    app = root / name
    app.mkdir()
    scripts = {
        "dev": f"vite --port {dev} --strictPort",
        "preview": f"vite preview --port {preview}",
    }
    (app / "package.json").write_text(json.dumps({"scripts": scripts}))
    if config_port is not None:
        (app / "vite.config.ts").write_text(
            f"export default {{ server: {{\n    port: {config_port},\n}} }};\n"
        )


def test_distinct_ports_pass(tmp_path: Path) -> None:
    _app(tmp_path, "product-a", 4310, 4310, 4310)
    _app(tmp_path, "product-b", 4320, 4320, 4320)
    assert frontend_ports.problems(tmp_path) == []


def test_a_shared_preview_port_fails(tmp_path: Path) -> None:
    _app(tmp_path, "product-a", 4310, 4340)
    _app(tmp_path, "product-b", 4320, 4340)
    assert frontend_ports.problems(tmp_path) == [
        "product-b: preview port 4340 is already product-a's"
    ]


def test_a_config_that_disagrees_with_the_script_fails(tmp_path: Path) -> None:
    _app(tmp_path, "product-a", 4310, 4310, 4311)
    assert len(frontend_ports.problems(tmp_path)) == 1
