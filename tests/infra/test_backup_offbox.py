"""PLATFORM-09: `backup.sh` encrypts before upload when asked, and `restore-drill.sh` proves a
backup restores — both run end to end here with `docker` and `age` replaced by stubs on PATH that
record every argv they are given.

Promises pinned:

* With `BACKUP_AGE_RECIPIENT` set, only `.age` files are ever handed to `aws s3 cp`; with no
  usable `age`, nothing is uploaded at all. Unset, behaviour is unchanged and a loud warning
  says the upload is plaintext.
* No secret (database password, bucket secret, the age private key) is on any command line.
* The drill accepts one target name, `opsgrids_restore`, refuses `opsgrids` and anything else
  before touching docker, drops the scratch database and the downloaded copy whatever happens,
  and prints one dated `OK`/`FAIL` line.
"""

from __future__ import annotations

import gzip
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE = REPO_ROOT / "infrastructure/edge"

DB_PASSWORD = "pgSECRETpassword0001"  # pragma: allowlist secret
S3_SECRET = "s3SECRETaccesskey0001"  # pragma: allowlist secret
AGE_IDENTITY = "AGE-SECRET-KEY-1DEVONLYSTUBIDENTITY0001"  # pragma: allowlist secret
RECIPIENT = "age1devonlystubrecipient0001"

DOCKER_STUB = r"""#!/usr/bin/env bash
printf 'docker %s\n' "$*" >>"$STUB_LOG"
args="$*"
case "$args" in
*" exec "*pg_dump*) head -c 20000 /dev/urandom ;;
*"tar czf /backup/"*)
	name=$(printf '%s' "${@: -1}" | awk '{print $3}')
	echo certs >"$STUB_DIR/${name#/backup/}"
	;;
*" ps "*) ;;
*"s3 ls"*) cat "$STUB_LISTING" ;;
*"s3 cp s3://"*)
	for a in "$@"; do case "$a" in
		/backup/*) cp "$STUB_OBJECT" "$STUB_DIR/${a#/backup/}" ;;
	esac; done
	;;
*"s3 cp /backup/"*)
	for a in "$@"; do case "$a" in /backup/*)
		[ -f "$STUB_DIR/${a#/backup/}" ] || exit 1
		printf 'uploaded %s\n' "${a#/backup/}" >>"$STUB_LOG"
		;; esac; done
	;;
*psql*)
	case "$args" in
	*"format('%I.%I'"*) printf 'storefront.storefronts\ncatalog.products\n' ;;
	*"--dbname=opsgrids_restore"*"count(*)"*) echo "${STUB_RESTORED_COUNT:-5}" ;;
	*"count(*)"*) echo 5 ;;
	*" -c "*) ;;
	*) cat >/dev/null ;;
	esac
	;;
esac
"""

AGE_STUB = r"""#!/usr/bin/env bash
printf 'age %s\n' "$*" >>"$STUB_LOG"
if [ "$1" = -r ]; then
	{ echo AGEHDR; cat "$5"; } >"$4"
elif [ "$1" = -d ]; then
	grep -q '^AGE-SECRET-KEY-' "$3" || { echo "no identity" >&2; exit 1; }
	tail -n +2 "$4"
fi
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def box(tmp_path: Path) -> dict[str, Path]:
    edge = tmp_path / "edge"
    (edge / "lib").mkdir(parents=True)
    for name in ("backup.sh", "restore-drill.sh", "lib/backup-marker.sh"):
        shutil.copy2(EDGE / name, edge / name)
    backups = tmp_path / "backups"
    backups.mkdir()
    (edge / ".env").write_text(
        f"POSTGRES_PASSWORD={DB_PASSWORD}\n"
        "BACKUP_S3_BUCKET=bucket\nBACKUP_S3_PREFIX=edge/postgres\n"
        f"BACKUP_S3_ACCESS_KEY_ID=AKIDEXAMPLE\nBACKUP_S3_SECRET_ACCESS_KEY={S3_SECRET}\n"
        f"BACKUP_LOCAL_DIR={backups}\nEDGE_STATE_DIR={tmp_path / 'state'}\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "docker", DOCKER_STUB)
    _exe(bin_dir / "age", AGE_STUB)
    if shutil.which("ghead"):  # macOS: backup.sh's prune uses GNU `head -n -N`
        _exe(bin_dir / "head", '#!/bin/sh\nexec ghead "$@"\n')
    listing = tmp_path / "listing"
    listing.write_text(
        "2026-10-10 03:00:01  999 opsgrids-20261010T030000Z.sql.gz.age\n"
        "2026-10-11 03:00:01  999 opsgrids-20261011T030000Z.sql.gz.age\n"
        "2026-10-11 03:00:02  999 caddy-data-20261011T030000Z.tar.gz.age\n"
    )
    obj = tmp_path / "object"
    obj.write_bytes(b"AGEHDR\n" + gzip.compress(b"-- a dump\n"))
    return {
        "edge": edge,
        "backups": backups,
        "bin": bin_dir,
        "log": tmp_path / "log",
        "listing": listing,
        "object": obj,
    }


def _run(
    box: dict[str, Path], script: str, *args: str, stdin: str = "", **env: str
) -> subprocess.CompletedProcess[str]:
    full = {
        "PATH": f"{box['bin']}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(box["log"]),
        "STUB_DIR": str(box["backups"]),
        "STUB_LISTING": str(box["listing"]),
        "STUB_OBJECT": str(box["object"]),
        **env,
    }
    return subprocess.run(
        ["bash", str(box["edge"] / script), *args],
        capture_output=True,
        text=True,
        input=stdin,
        env=full,
    )


def _log(box: dict[str, Path]) -> str:
    return box["log"].read_text() if box["log"].exists() else ""


def _uploaded(box: dict[str, Path]) -> list[str]:
    return [ln.split(" ", 1)[1] for ln in _log(box).splitlines() if ln.startswith("uploaded ")]


def _with_recipient(box: dict[str, Path]) -> None:
    env = box["edge"] / ".env"
    env.write_text(env.read_text() + f"BACKUP_AGE_RECIPIENT={RECIPIENT}\n")


class TestBackupEncryption:
    def test_unset_uploads_plaintext_as_before_and_warns_loudly(self, box: dict[str, Path]) -> None:
        done = _run(box, "backup.sh")
        assert done.returncode == 0, done.stderr
        assert "PLAINTEXT" in done.stderr and "BACKUP_AGE_RECIPIENT" in done.stderr
        uploaded = _uploaded(box)
        assert len(uploaded) == 2
        assert any(u.endswith(".sql.gz") for u in uploaded)
        assert any(u.endswith(".tar.gz") for u in uploaded)
        assert "age " not in _log(box)

    def test_set_uploads_only_age_files_and_removes_them_after(self, box: dict[str, Path]) -> None:
        _with_recipient(box)
        done = _run(box, "backup.sh")
        assert done.returncode == 0, done.stderr
        assert "PLAINTEXT" not in done.stderr
        uploaded = _uploaded(box)
        assert len(uploaded) == 2 and all(u.endswith(".age") for u in uploaded), uploaded
        assert f"age -r {RECIPIENT} -o" in _log(box)
        assert not list(box["backups"].glob("*.age")), "the encrypted copy is not kept locally"
        assert list(box["backups"].glob("opsgrids-*.sql.gz")), "the local copy is kept as before"

    def test_set_without_a_usable_age_uploads_nothing(self, box: dict[str, Path]) -> None:
        _with_recipient(box)
        done = _run(box, "backup.sh", BACKUP_AGE_BIN=str(box["bin"] / "no-such-age"))
        assert done.returncode != 0
        assert "refusing to upload plaintext" in done.stderr
        assert _uploaded(box) == [] and "s3 cp" not in _log(box)

    def test_a_failing_encryption_never_falls_back_to_plaintext(self, box: dict[str, Path]) -> None:
        _with_recipient(box)
        _exe(box["bin"] / "age", "#!/bin/sh\nexit 1\n")
        done = _run(box, "backup.sh")
        assert done.returncode != 0
        assert _uploaded(box) == []


class TestRestoreDrill:
    @pytest.mark.parametrize("name", ["opsgrids", "opsgrids_prod", "OPSGRIDS_RESTORE", "x"])
    def test_any_target_but_the_scratch_name_is_refused_before_docker(
        self, box: dict[str, Path], name: str
    ) -> None:
        done = _run(box, "restore-drill.sh", name, stdin=AGE_IDENTITY)
        assert done.returncode == 64
        assert "only opsgrids_restore" in done.stderr
        assert _log(box) == ""

    def test_the_newest_encrypted_dump_restores_and_is_cleaned_up(
        self, box: dict[str, Path]
    ) -> None:
        done = _run(box, "restore-drill.sh", stdin=AGE_IDENTITY)
        assert done.returncode == 0, done.stdout + done.stderr
        last = done.stdout.strip().splitlines()[-1]
        assert last.startswith("restore-drill 20") and " OK " in last
        assert "object=opsgrids-20261011T030000Z.sql.gz.age tables=2" in last
        log = _log(box)
        assert "age -d -i /dev/fd/" in log
        assert "CREATE DATABASE opsgrids_restore" in log
        assert log.rstrip().splitlines()[-1].endswith("DROP DATABASE IF EXISTS opsgrids_restore")
        # Live is only ever read: every DROP/CREATE goes through the maintenance database.
        for line in log.splitlines():
            if "DROP" in line or "CREATE" in line:
                assert "--dbname=postgres -c" in line and "opsgrids_restore" in line, line
        assert not list(box["backups"].glob("restore-drill-*")), "the downloaded copy is removed"

    def test_an_encrypted_dump_without_a_key_fails_and_still_cleans_up(
        self, box: dict[str, Path]
    ) -> None:
        done = _run(box, "restore-drill.sh")
        assert done.returncode == 1
        assert " FAIL " in done.stdout and "no key was given" in done.stdout

    def test_a_plaintext_dump_needs_no_key(self, box: dict[str, Path]) -> None:
        box["listing"].write_text("2026-10-11 03:00:01  999 opsgrids-20261011T030000Z.sql.gz\n")
        box["object"].write_bytes(gzip.compress(b"-- a dump\n"))
        done = _run(box, "restore-drill.sh")
        assert done.returncode == 0, done.stdout + done.stderr
        assert " OK object=opsgrids-20261011T030000Z.sql.gz " in done.stdout
        assert "age " not in _log(box)

    def test_an_empty_restored_table_where_live_has_rows_fails(self, box: dict[str, Path]) -> None:
        done = _run(box, "restore-drill.sh", stdin=AGE_IDENTITY, STUB_RESTORED_COUNT="0")
        assert done.returncode == 1
        assert done.stdout.strip().splitlines()[-1].endswith("tables=2 problems=2")
        assert "DROP DATABASE IF EXISTS opsgrids_restore" in _log(box).splitlines()[-1]


def test_no_secret_is_ever_on_a_command_line(box: dict[str, Path]) -> None:
    _with_recipient(box)
    assert _run(box, "backup.sh").returncode == 0
    assert _run(box, "restore-drill.sh", stdin=AGE_IDENTITY).returncode == 0
    log = _log(box)
    assert "s3 cp" in log and "pg_dump" in log, "both scripts really ran"
    for secret in (DB_PASSWORD, S3_SECRET, AGE_IDENTITY):
        assert secret not in log


def test_the_restore_drill_is_wired_into_remote_and_make() -> None:
    assert "restore-drill) ./restore-drill.sh ;;" in (EDGE / "remote.sh").read_text()
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "edge-restore-drill: edge-guard" in makefile
    assert "restore-drill < $(or $(AGE_KEY),/dev/null)" in makefile
