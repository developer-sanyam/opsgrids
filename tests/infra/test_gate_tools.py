"""Pins the decisions inside the gate's own tooling: the licence expression parser, the audit
allow-list's expiry rule, the frontend port check and the frontend manifest check (tests are run
or absent, fonts are self-hosted, session slots are distinct).

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
frontend_manifest = _load("frontend_manifest")

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


def _app(
    root: Path,
    name: str,
    dev: int,
    preview: int,
    config_port: int | None = None,
    *,
    preview_config_port: int | None = None,
    strict_preview: bool = True,
) -> None:
    app = root / name
    app.mkdir()
    scripts = {
        "dev": f"vite --port {dev} --strictPort",
        "preview": f"vite preview --port {preview}" + (" --strictPort" if strict_preview else ""),
    }
    (app / "package.json").write_text(json.dumps({"scripts": scripts}))
    blocks = []
    if config_port is not None:
        blocks.append(f"server: {{\n    port: {config_port},\n    strictPort: true,\n  }}")
    if preview_config_port is not None:
        blocks.append(f"preview: {{\n    port: {preview_config_port},\n  }}")
    if blocks:
        (app / "vite.config.ts").write_text(f"export default {{ {', '.join(blocks)} }};\n")


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


def test_a_preview_block_that_disagrees_with_the_preview_script_fails(tmp_path: Path) -> None:
    _app(tmp_path, "product-a", 4310, 4310, 4310, preview_config_port=4999)
    assert frontend_ports.problems(tmp_path) == [
        "product-a: vite.config.ts preview serves on 4999 but `preview` passes --port 4310"
    ]


def test_a_preview_block_that_agrees_passes(tmp_path: Path) -> None:
    _app(tmp_path, "product-a", 4310, 4310, 4310, preview_config_port=4310)
    assert frontend_ports.problems(tmp_path) == []


def test_a_preview_without_strict_port_fails_because_a_busy_port_would_move_it(
    tmp_path: Path,
) -> None:
    """Without the flag Vite takes the next free port when its own is busy: two previews then
    both 'run', one of them somewhere nobody is looking — a collision that reports success."""
    _app(tmp_path, "product-a", 4310, 4310, strict_preview=False)
    assert frontend_ports.problems(tmp_path) == [
        "product-a: `preview` sets no --strictPort, so a busy port moves it somewhere else silently"
    ]


def test_a_preview_on_another_apps_dev_port_fails(tmp_path: Path) -> None:
    """A dev server on 4310 blocks a preview that wanted 4310 exactly as two previews would."""
    _app(tmp_path, "product-a", 4310, 4310)
    _app(tmp_path, "product-b", 4320, 4310)
    found = frontend_ports.problems(tmp_path)
    assert "product-b: preview port 4310 is already product-a's" in found
    assert "product-b: preview port 4310 is product-a's dev port" in found


def test_an_apps_own_dev_and_preview_may_share_a_number(tmp_path: Path) -> None:
    """One is run or the other, never both, so the number is the app's, not a collision."""
    _app(tmp_path, "product-a", 4310, 4310)
    assert frontend_ports.problems(tmp_path) == []


def test_the_real_workspace_passes() -> None:
    """The check is not only correct in the abstract: every app on disk satisfies it."""
    assert frontend_ports.problems(REPO_ROOT / "frontend") == []


# ---------------------------------------------------------------- frontend manifest (R32, C13)


def _package(
    root: Path,
    name: str,
    *,
    test_script: str | None = "vitest run",
    tests: list[str] | None = None,
    dependencies: dict[str, str] | None = None,
    main: str | None = None,
    index_html: str | None = None,
    extra: dict[str, str] | None = None,
) -> Path:
    """One frontend package on disk, as little of it as the check under test reads."""
    package = root / name
    (package / "src").mkdir(parents=True)
    scripts = {"test": test_script} if test_script is not None else {}
    (package / "package.json").write_text(
        json.dumps({"scripts": scripts, "dependencies": dependencies or {}})
    )
    for test in tests or []:
        (package / "src" / test).write_text("it('runs', () => {})\n")
    if main is not None:
        (package / "src" / "main.tsx").write_text(main)
    if index_html is not None:
        (package / "index.html").write_text(index_html)
    for relative, text in (extra or {}).items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return package


def test_a_test_script_that_passes_with_no_tests_fails(tmp_path: Path) -> None:
    """The flag that let opsgrids-internal go green with zero tests for months."""
    _package(
        tmp_path, "opsgrids-a", test_script="vitest run --passWithNoTests", tests=["a.test.ts"]
    )
    found = frontend_manifest.test_problems(tmp_path)
    assert len(found) == 1
    assert "passes with no tests" in found[0]


def test_a_vitest_config_that_passes_with_no_tests_fails(tmp_path: Path) -> None:
    package = _package(tmp_path, "opsgrids-a", tests=["a.test.ts"])
    (package / "vitest.config.ts").write_text(
        "export default { test: { passWithNoTests: true } };\n"
    )
    assert frontend_manifest.test_problems(tmp_path) == [
        "opsgrids-a: vitest.config.ts sets passWithNoTests; remove it."
    ]


def test_a_package_with_tests_and_a_plain_script_passes(tmp_path: Path) -> None:
    _package(tmp_path, "opsgrids-a", tests=["a.test.ts", "b.test.tsx"])
    assert frontend_manifest.test_problems(tmp_path) == []


def test_a_test_script_with_no_test_files_fails(tmp_path: Path) -> None:
    """A suite that stopped being collected: vitest would fail on none found, and this says why."""
    _package(tmp_path, "opsgrids-a", tests=[])
    found = frontend_manifest.test_problems(tmp_path)
    assert len(found) == 1
    assert "no test files" in found[0]


def test_test_files_with_no_test_script_fail(tmp_path: Path) -> None:
    """Tests nobody runs are not tests."""
    _package(tmp_path, "opsgrids-a", test_script=None, tests=["a.test.ts"])
    found = frontend_manifest.test_problems(tmp_path)
    assert len(found) == 1
    assert "no `test` script" in found[0]


def test_a_package_with_no_tests_and_no_script_is_legitimately_none(tmp_path: Path) -> None:
    """A presets-only package (opsgrids-config): `pnpm -r test` skips it, and nothing is hidden."""
    _package(tmp_path, "opsgrids-config", test_script=None, tests=[])
    assert frontend_manifest.test_problems(tmp_path) == []


def test_a_test_script_that_is_not_vitest_run_fails(tmp_path: Path) -> None:
    """Bare `vitest` starts a watcher and waits for a keypress in CI."""
    _package(tmp_path, "opsgrids-a", test_script="vitest", tests=["a.test.ts"])
    found = frontend_manifest.test_problems(tmp_path)
    assert len(found) == 1
    assert "vitest run" in found[0]


def test_test_files_in_node_modules_and_dist_are_not_counted(tmp_path: Path) -> None:
    package = _package(tmp_path, "opsgrids-a", tests=[])
    for ignored in ("node_modules/x/src", "dist/src"):
        (package / ignored).mkdir(parents=True)
        (package / ignored / "x.test.ts").write_text("")
    assert len(frontend_manifest.test_problems(tmp_path)) == 1


def test_a_google_fonts_link_fails(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "product-a",
        index_html='<link href="https://fonts.googleapis.com/css2?family=Manrope">',
    )
    found = frontend_manifest.font_problems(tmp_path)
    assert len(found) == 1
    assert "fonts.googleapis.com" in found[0]


def test_a_gstatic_preconnect_or_a_css_import_fails_too(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "product-a",
        index_html='<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />',
        extra={"src/index.css": '@import url("https://fonts.googleapis.com/css2?family=Inter");'},
    )
    found = frontend_manifest.font_problems(tmp_path)
    assert len(found) == 2


def test_an_app_that_takes_the_console_stylesheet_must_take_its_typeface(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "product-a",
        dependencies={"@opsgrids/ui": "workspace:*"},
        main='import "./index.css";\n',
    )
    found = frontend_manifest.font_problems(tmp_path)
    assert len(found) == 1
    assert "@opsgrids/ui/fonts" in found[0]


def test_an_app_that_imports_the_typeface_passes(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "product-a",
        dependencies={"@opsgrids/ui": "workspace:*"},
        main='import "@opsgrids/ui/fonts";\nimport "./index.css";\n',
    )
    assert frontend_manifest.font_problems(tmp_path) == []


def test_an_app_that_takes_no_stylesheet_needs_no_typeface(tmp_path: Path) -> None:
    """The storefront's buyer entry is deliberately font-free, and is not a `main.tsx`."""
    _package(
        tmp_path, "product-a", dependencies={"@opsgrids/ui": "workspace:*"}, main="export {};\n"
    )
    assert frontend_manifest.font_problems(tmp_path) == []


def test_two_apps_naming_one_session_slot_fails(tmp_path: Path) -> None:
    for name in ("product-a", "product-b"):
        _package(
            tmp_path,
            name,
            main='configureSession("opsgrids.shared.access_token");\n',
            extra={"src/session.ts": 'const KEY = "opsgrids.shared.access_token";\n'},
        )
    found = frontend_manifest.session_problems(tmp_path)
    assert any("named by product-a, product-b" in problem for problem in found)


def test_a_tenant_app_naming_the_operator_slot_fails(tmp_path: Path) -> None:
    """An operator token and a tenant token must never share a name — ADR-0003 §2."""
    _package(
        tmp_path,
        "product-a",
        main="configureSession(KEY);\n",
        extra={"src/keys.ts": 'export const KEY = "opsgrids.operator.access_token";\n'},
    )
    found = frontend_manifest.session_problems(tmp_path)
    assert any("belongs to opsgrids-internal" in problem for problem in found)


def test_a_tenant_app_reaching_the_operator_sign_in_fails(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "product-a",
        main='configureSession("opsgrids.a.access_token");\n',
        extra={"src/api.ts": 'fetch("/api/v1/auth/operator/login");\n'},
    )
    found = frontend_manifest.session_problems(tmp_path)
    assert any("reaches /auth/operator" in problem for problem in found)


def test_the_operator_console_may_reach_its_own_routes_under_its_own_slot(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "opsgrids-internal",
        main="configureSession(OPERATOR_SESSION_KEY);\n",
        extra={
            "src/keys.ts": 'export const KEY = "opsgrids.operator.access_token";\n',
            "src/api.ts": 'const LOGIN = "/auth/operator/login";\n',
        },
    )
    _package(tmp_path, "product-a", main='configureSession("opsgrids.a.access_token");\n')
    assert frontend_manifest.session_problems(tmp_path) == []


def test_the_operator_console_naming_a_slot_without_operator_in_it_fails(tmp_path: Path) -> None:
    _package(
        tmp_path,
        "opsgrids-internal",
        main="configureSession(KEY);\n",
        extra={"src/keys.ts": 'export const KEY = "opsgrids.internal.access_token";\n'},
    )
    assert any("does not say `operator`" in p for p in frontend_manifest.session_problems(tmp_path))


def test_an_app_that_never_names_its_slot_shares_the_default_with_every_other(
    tmp_path: Path,
) -> None:
    _package(tmp_path, "product-a", main="createRoot(root).render(<App />);\n")
    found = frontend_manifest.session_problems(tmp_path)
    assert any("never calls configureSession()" in problem for problem in found)


def test_the_real_workspace_has_no_manifest_problems() -> None:
    """The rules are not only right in the abstract: every package on disk satisfies them."""
    assert frontend_manifest.problems(REPO_ROOT / "frontend") == []
