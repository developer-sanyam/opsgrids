"""The warehouse map's Google Maps key and how it reaches a bundle (spec
`docs/superpowers/specs/2026-10-05-warehouse-map-putaway-design.md` §7, decision D2).

Three promises, each one a way this could go wrong in production:

* **Only the operations bundle gets the key, read from the box's `.env`.** The worker app takes a
  put-away by scanning a bin label and never draws a map; a key a maintainer happens to have
  exported in their own shell must not ride into any other bundle, and must not re-enable a map
  the box was given no key for.
* **No key means no map, never a broken one.** The publish still succeeds, behind a loud warning,
  and the key is never printed — not even when it is malformed.
* **No Caddy change.** D2: nothing about `ops`/`worker`'s response headers moves in this release.
  CSP hardening is a separate release; this pins that this one did not smuggle it in.

The script is run for real, in a sandbox, with `ssh`, `rsync`, `pnpm` and `node` replaced by stubs
on `PATH`: the stub `ssh` runs the script's own `sed` reads against a throwaway `.env`, so the key
is looked up by the very pipeline `publish-web.sh` ships, and the stub `pnpm` records the
environment the build would have seen. Nothing here touches a network, a real frontend directory
or a real box.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest
from _helpers import read_text

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
SCRIPT = EDGE_DIR / "publish-web.sh"
CADDYFILE = EDGE_DIR / "caddy/Caddyfile"
ENV_EXAMPLE = EDGE_DIR / ".env.example"
MATRIX = REPO_ROOT / "docs/deploy/ENV_SOURCE_MATRIX.md"

#: Not a real key and not shaped like one (no `AIza` prefix): 34 characters of letters, digits and
#: hyphens, which is all `publish-web.sh` checks for.
MAPS_KEY = "test-maps-key-0123456789abcdefghij"  # pragma: allowlist secret
#: What a developer's own shell might already export. If it ever shows up in a build, the script
#: let the laptop's environment decide instead of the box's.
AMBIENT = "ambient-shell-sentinel-9f8e7d6c5b4a"  # pragma: allowlist secret

NAME = "VITE_GOOGLE_MAPS_API_KEY"

# --------------------------------------------------------------------------- stubs
STUB_SSH = """#!/usr/bin/env bash
# `ssh -o BatchMode=yes <host> <command>`. Only the script's own `sed ... .env` reads are run, so
# they read the sandbox box's `.env` through the real pipeline; every other command (the
# wrong-box interlock, mkdir, the symlink swap) is accepted and does nothing.
while [ "$1" = "-o" ]; do shift 2; done
shift
cmd="$*"
printf '%s\\n' "$cmd" >>"$STUB_SSH_LOG"
case "$cmd" in
"sed -n 's/^"*) exec bash -c "$cmd" ;;
esac
exit 0
"""

STUB_RSYNC = "#!/usr/bin/env bash\nexit 0\n"
STUB_NODE = "#!/usr/bin/env bash\necho 22\n"

STUB_PNPM = f"""#!/usr/bin/env bash
# `pnpm install` does nothing. `pnpm run build` records what the build would have seen of the
# variable (set to what, or not set at all) and leaves a bundle behind so the publish can finish.
if [ "$1" = "run" ]; then
	if [ "${{{NAME}+set}}" = set ]; then
		printf 'set:%s\\n' "${NAME}" >"$STUB_BUILD_RECORD.$(basename "$PWD")"
	else
		echo unset >"$STUB_BUILD_RECORD.$(basename "$PWD")"
	fi
	mkdir -p dist
	echo '<html></html>' >dist/index.html
fi
exit 0
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    build: dict[str, str]
    ssh_commands: list[str]

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


class Sandbox:
    """A copy of `publish-web.sh` in a scratch repo, plus a scratch box `.env` for it to read."""

    #: app -> (workspace package, host the script's CORS allow-list check wants in the box's `.env`)
    APPS: ClassVar[dict[str, tuple[str, str]]] = {
        "operations": ("product-operations", "ops.opsgrids.com"),
        "worker": ("worker-app", "worker.opsgrids.com"),
        "hrms": ("product-hrms", "people.opsgrids.com"),
    }

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "repo"
        edge = self.root / "infrastructure/edge"
        edge.mkdir(parents=True)
        shutil.copy2(SCRIPT, edge / "publish-web.sh")
        for package, _host in self.APPS.values():
            app_dir = self.root / "frontend" / package
            app_dir.mkdir(parents=True)
            (app_dir / "package.json").write_text('{"scripts": {"build": "vite build"}}\n')
            (app_dir / "index.html").write_text("<html></html>\n")

        self.box = tmp_path / "box"
        (self.box / "infrastructure/edge").mkdir(parents=True)

        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        _executable(self.bin / "ssh", STUB_SSH)
        _executable(self.bin / "rsync", STUB_RSYNC)
        _executable(self.bin / "node", STUB_NODE)
        _executable(self.bin / "pnpm", STUB_PNPM)

        self.ssh_log = tmp_path / "ssh.log"
        self.build_record = tmp_path / "build"
        self.write_box_env()

    def write_box_env(self, *extra: str) -> None:
        """The box's `.env` as the publish needs it, plus whatever `extra` lines a test adds."""
        hosts = ",".join(f"https://{host}" for _pkg, host in self.APPS.values())
        lines = [
            "EDGE_HOST=edge.opsgrids.test",
            f"EDGE_WEB_ROOT={self.box}/web",
            "APP_BASE_URL=https://app.opsgrids.com",
            f"AUTH_REFRESH_CORS_ORIGINS={hosts}",
            *extra,
        ]
        (self.box / "infrastructure/edge/.env").write_text("\n".join(lines) + "\n")

    def publish(self, app: str, *, ambient: str | None = None) -> Result:
        env = {k: v for k, v in os.environ.items() if k != NAME}
        env.update(
            PATH=f"{self.bin}{os.pathsep}{env.get('PATH', '')}",
            APP=app,
            EDGE_REPO=str(self.box),
            STUB_SSH_LOG=str(self.ssh_log),
            STUB_BUILD_RECORD=str(self.build_record),
        )
        if ambient is not None:
            env[NAME] = ambient
        proc = subprocess.run(
            ["bash", str(self.root / "infrastructure/edge/publish-web.sh"), "publish", "h", "c"],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.root,
        )
        build = {
            path.suffix.lstrip("."): path.read_text().strip()
            for path in self.build_record.parent.glob(f"{self.build_record.name}.*")
        }
        commands = self.ssh_log.read_text().splitlines() if self.ssh_log.exists() else []
        return Result(proc.returncode, proc.stdout, proc.stderr, build, commands)


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


# --------------------------------------------------------------------------- the operations bundle
def test_operations_is_built_with_the_key_from_the_box_env(sandbox: Sandbox) -> None:
    sandbox.write_box_env(f"{NAME}={MAPS_KEY}")
    result = sandbox.publish("operations")
    assert result.returncode == 0, result.output
    assert result.build == {"product-operations": f"set:{MAPS_KEY}"}
    assert "WARNING" not in result.output
    assert "published operations" in result.output


def test_the_key_is_never_printed_to_the_build_log(sandbox: Sandbox) -> None:
    sandbox.write_box_env(f"{NAME}={MAPS_KEY}")
    result = sandbox.publish("operations")
    assert result.returncode == 0, result.output
    assert MAPS_KEY not in result.output
    # It says it found one, which is the whole of what an operator needs from the log.
    assert NAME in result.output


def test_the_key_is_read_the_way_the_deploy_scripts_read_dotenv_values(sandbox: Sandbox) -> None:
    """The last assignment wins; a quoted value is what is between the quotes; an inline comment
    after it is dropped (`_dotenv_value`, the same parsing `remote.sh`'s `dotenv_get` does)."""
    sandbox.write_box_env(
        f"{NAME}=an-older-assignment-that-must-lose-0123456789",
        f'{NAME}="{MAPS_KEY}" # restricted to ops.opsgrids.com',
    )
    result = sandbox.publish("operations")
    assert result.returncode == 0, result.output
    assert result.build == {"product-operations": f"set:{MAPS_KEY}"}


@pytest.mark.parametrize(
    ("lines", "why"),
    [
        pytest.param((), "absent", id="absent"),
        pytest.param((f"{NAME}=",), "empty", id="empty"),
        pytest.param((f'{NAME}=""',), "empty", id="empty-quoted"),
        pytest.param((f"{NAME}=short",), "malformed", id="too-short"),
        pytest.param((f"{NAME}=abc$defghijklmnopqrstuvwxyz",), "malformed", id="shell-metachar"),
        pytest.param((f"{NAME}=abc/defghijklmnopqrstuvwxyz",), "malformed", id="slash"),
    ],
)
def test_a_missing_or_unusable_key_publishes_map_less_behind_a_loud_warning(
    sandbox: Sandbox, lines: tuple[str, ...], why: str
) -> None:
    sandbox.write_box_env(*lines)
    result = sandbox.publish("operations")
    # Never a refusal: the map is disabled, not the product.
    assert result.returncode == 0, result.output
    assert "published operations" in result.output
    # The build is told "no key" explicitly (an empty value, not an absent one).
    assert result.build == {"product-operations": "set:"}
    # Loud, and on stderr where a scrolled-past build log still shows it.
    assert "WARNING" in result.stderr
    assert NAME in result.stderr
    assert "WITHOUT the Google map" in result.stderr
    assert "RUNBOOK.md" in result.stderr
    # A malformed key is still a key: name the problem, never the value.
    for line in lines:
        value = line.split("=", 1)[1]
        if len(value) > 4:
            assert value not in result.output


def test_an_ambient_shell_key_never_reaches_a_map_less_operations_build(sandbox: Sandbox) -> None:
    """The box is the one source of truth. A key exported on the laptop must not turn a map on
    for a box that was never given one (the warning would then be a lie)."""
    result = sandbox.publish("operations", ambient=AMBIENT)
    assert result.returncode == 0, result.output
    assert result.build == {"product-operations": "set:"}
    assert AMBIENT not in result.output
    assert "WARNING" in result.stderr


def test_the_boxs_key_wins_over_an_ambient_shell_key(sandbox: Sandbox) -> None:
    sandbox.write_box_env(f"{NAME}={MAPS_KEY}")
    result = sandbox.publish("operations", ambient=AMBIENT)
    assert result.build == {"product-operations": f"set:{MAPS_KEY}"}


# --------------------------------------------------------------------------- every other bundle
@pytest.mark.parametrize("app", ["worker", "hrms"])
def test_no_other_app_is_ever_given_the_key(sandbox: Sandbox, app: str) -> None:
    """Not the worker app (decision D2: it has no map) and not any other product SPA. The box has
    a key, the laptop's shell has a key, and the build must see neither."""
    sandbox.write_box_env(f"{NAME}={MAPS_KEY}")
    result = sandbox.publish(app, ambient=AMBIENT)
    assert result.returncode == 0, result.output
    package, _host = Sandbox.APPS[app]
    assert result.build == {package: "unset"}
    # Not even read: the box's `.env` is not asked for it, and nothing warns about a map.
    assert not [c for c in result.ssh_commands if NAME in c]
    assert NAME not in result.output
    assert MAPS_KEY not in result.output
    assert AMBIENT not in result.output


# --------------------------------------------------------------------------- the script's own text
def _code_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_the_key_is_parsed_with_the_shared_dotenv_helper() -> None:
    code = "\n".join(_code_lines(read_text(SCRIPT)))
    assert 'maps_key=$(_dotenv_value "$maps_key")' in code, (
        "the key must be read with `_dotenv_value`, so it is judged exactly the way the box's own "
        "deploy scripts read the same line, not by a second, slightly different parser"
    )
    assert f'export {NAME}="$maps_key"' in code
    assert f"unset {NAME}" in code, "every other app must be built with the variable removed"


def test_the_worker_case_never_mentions_the_key() -> None:
    match = re.search(r"\nworker\)\n(.*?)\n\t;;\n", read_text(SCRIPT), re.DOTALL)
    assert match, "publish-web.sh has no `worker)` case in its app table"
    assert NAME not in match.group(1)


def test_the_key_is_handled_in_exactly_one_place_and_only_for_operations() -> None:
    """Every non-comment line that names the variable sits in the one `case "$app"` block whose
    first arm is `operations)` and whose default arm unsets it."""
    code_lines = _code_lines(read_text(SCRIPT))
    named = [line for line in code_lines if NAME in line]
    assert named, "publish-web.sh does not handle the Maps key at all"
    text = "\n".join(code_lines)
    block = re.search(
        r'\tcase "\$app" in\n\toperations\)\n(?P<body>.*?)\n\t\t;;\n\t\*\)\n\t\tunset '
        + NAME
        + r"\n\t\t;;\n\tesac\n",
        text,
        re.DOTALL,
    )
    assert block, "the Maps key case is not in the expected `operations) … *) unset` shape"
    in_block = [line for line in block.group(0).splitlines() if NAME in line]
    assert len(in_block) == len(named), "the key is mentioned outside its one `operations` case"


def test_the_build_log_line_never_expands_the_key_itself() -> None:
    """The one line that reports a found key may say how long it is, never what it is."""
    for line in _code_lines(read_text(SCRIPT)):
        if "echo" in line and "$maps_key" in line:
            assert "${#maps_key}" in line
            assert not re.search(r"\$maps_key\b|\$\{maps_key\}", line.replace("${#maps_key}", ""))


# --------------------------------------------------------------------------- the example and matrix
def _example_assignments() -> set[str]:
    """The keys preflight would require: the same pattern `preflight.sh` reads names with."""
    return {
        m.group(1)
        for line in ENV_EXAMPLE.read_text().splitlines()
        if (m := re.match(r"^([A-Z][A-Z0-9_]*)=", line))
    }


def test_the_key_is_documented_in_the_edge_env_example_but_not_required_by_preflight() -> None:
    """An uncommented `KEY=` line in `.env.example` becomes required on every box
    (`preflight.sh`), and the owner sets this key separately from the release that ships the map.
    So it is documented, commented out, exactly like `EDGE_UP_PARALLEL_LIMIT`."""
    text = ENV_EXAMPLE.read_text()
    assert NAME not in _example_assignments(), (
        f"{NAME} is an uncommented line in .env.example: preflight would refuse every deploy "
        "on a box that has not been given the key yet"
    )
    assert re.search(rf"^# >> {NAME}\b", text, re.MULTILINE), "no `>> KEY` annotation block"
    assert re.search(rf"^# {NAME}=$", text, re.MULTILINE), (
        "the commented example line must carry no value at all"
    )
    assert "AIza" not in text


def test_the_key_has_a_row_in_the_env_source_matrix_and_no_value() -> None:
    rows = [line for line in MATRIX.read_text().splitlines() if line.startswith(f"| `{NAME}` |")]
    assert len(rows) == 1, "exactly one matrix row, so the awk re-check in section 5 stays exact"
    assert "publish-web.sh" in rows[0]
    assert "AIza" not in rows[0]
    assert MAPS_KEY not in rows[0]


# --------------------------------------------------------------------------- Caddy and the map (D2)
#: Every Content-Security-Policy the Caddyfile sets with a bare `header` directive, each on a site
#: with its own reason. The app sites' policies come from the `(platform-headers)` snippet's
#: argument instead and are pinned per site by tests/infra/test_platform_site_headers.py — which
#: is where the CSP-hardening release allowed the Google Maps origins, on ops.opsgrids.com only.
KNOWN_CSPS = {
    "default-src 'none'; sandbox",  # the public bucket's objects (ADR-0009)
    "default-src 'self'; style-src 'self'; font-src 'self'; frame-ancestors 'none'",  # admin
}


def _site_block(host: str) -> str:
    text = CADDYFILE.read_text()
    match = re.search(rf"^https://{re.escape(host)} \{{\n(.*?)^\}}\n", text, re.DOTALL | re.M)
    assert match, f"no https://{host} site block found in the Caddyfile"
    return match.group(1)


@pytest.mark.parametrize("host", ["ops.opsgrids.com", "worker.opsgrids.com"])
def test_ops_and_worker_set_no_csp_of_their_own_or_permissions_policy(host: str) -> None:
    """Their CSP is the shared snippet's (one place to read it); the worker's location prompt and
    camera keep working because no Permissions-Policy is sent."""
    block = _site_block(host)
    assert "Content-Security-Policy" not in block
    assert "Permissions-Policy" not in block
    assert "import edge" in block, (
        "the site no longer imports the shared snippet it was audited with"
    )


def test_the_shared_edge_snippet_sets_no_csp_or_permissions_policy() -> None:
    """`ops` and `worker` import it, so a header added there would reach them without ever
    appearing in their own blocks."""
    text = CADDYFILE.read_text()
    match = re.search(r"^\(edge\) \{\n(.*?)^\}\n", text, re.DOTALL | re.M)
    assert match, "no (edge) snippet found in the Caddyfile"
    assert "Content-Security-Policy" not in match.group(1)
    assert "Permissions-Policy" not in match.group(1)


def test_no_new_csp_or_permissions_policy_anywhere_in_the_caddyfile() -> None:
    text = CADDYFILE.read_text()
    csps = set(re.findall(r'header Content-Security-Policy "([^"]*)"', text))
    assert csps == KNOWN_CSPS, (
        "the Caddyfile's set of bare Content-Security-Policy values changed; app sites take "
        "theirs from the platform-headers snippet (tests/infra/test_platform_site_headers.py)"
    )
    assert "Permissions-Policy" not in text
    # The Google Maps origins are allow-listed on the one site with a map, and nowhere else.
    for host in ("maps.googleapis.com", "maps.gstatic.com"):
        sites = [
            m.group(1)
            for m in re.finditer(r"^(https://\S*) \{\n(.*?)^\}\n", text, re.DOTALL | re.M)
            if host in m.group(2)
        ]
        assert sites == ["https://ops.opsgrids.com"], f"{host} is allowed on {sites}"
