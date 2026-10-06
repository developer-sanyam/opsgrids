"""No real-shaped payment credential is ever committed (ADR-0032).

`detect-secrets` (the repo's `make secrets` gate) has no Razorpay or Cashfree detector, and the
owner of this work was handed sandbox credentials in chat. So this is the guard that is specific to
these providers: every tracked (or about-to-be-tracked) text file is scanned for the shape of a
**real** key —

* Cashfree secret keys: `cfsk_ma_test_` / `cfsk_ma_prod_` and 16 or more letters and digits;
* a Cashfree sandbox App ID: `TEST` followed by six or more digits, or by sixteen or more letters
  and digits (Cashfree's published samples carry a hex-like tail: digits alone are not enough);
* Razorpay key ids: `rzp_test_` / `rzp_live_` followed by **exactly 14** letters and digits (their
  shape; the fixtures in this repo are deliberately 12, 13 or 18 characters, never 14 — bar
  Razorpay's own documentation sample, allowed by value).

Fixtures use `dev-only-` placeholders and shapes that are not these (`cfsk_ma_test_dev-only-0001`
has a hyphen inside the tail; `TESTdev-only-app` has no digits). A hit prints the file and line,
**never the match**: a leaked key must not be re-leaked into a CI log by the test that found it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PATTERNS: dict[str, re.Pattern[str]] = {
    "a Cashfree secret key (cfsk_ma_...)": re.compile(r"cfsk_ma_(?:test|prod)_[A-Za-z0-9]{16,}"),
    "a Cashfree sandbox App ID (TEST<digits>)": re.compile(
        r"\bTEST(?:[0-9]{6,}|[0-9A-Za-z]{16,})\b"
    ),
    "a Razorpay key id (rzp_...)": re.compile(r"rzp_(?:test|live)_[A-Za-z0-9]{14}(?![A-Za-z0-9])"),
}

#: Files a scan has no business reading: dependency locks, generated bundles, binaries.
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".gz", ".mp4", ".mov", ".pem", ".lock", ".sqlite", ".db",
}  # fmt: skip
SKIP_NAMES = {"pnpm-lock.yaml", "package-lock.json", "uv.lock", ".secrets.baseline"}
SKIP_PARTS = {"node_modules", ".venv", "dist", ".git", ".mypy_cache", ".ruff_cache"}


def _files() -> list[Path]:
    out = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        capture_output=True,
        check=True,
    ).stdout
    found = []
    for raw in out.split(b"\0"):
        if not raw:
            continue
        path = REPO_ROOT / raw.decode()
        parts = set(path.relative_to(REPO_ROOT).parts)
        if parts & SKIP_PARTS or path.name in SKIP_NAMES or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.is_file():
            found.append(path)
    return found


#: Razorpay's own documentation example key id — public, printed on every one of their pages, and
#: used by the repo's pre-existing credential-shape tests. Not a credential; allowed by value.
DOCUMENTED_SAMPLES = ("rzp_test_1DP5mmOlF5G5ag", "rzp_live_1DP5mmOlF5G5ag")


def _hits(text: str) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for sample in DOCUMENTED_SAMPLES:
            line = line.replace(sample, "")
        found.extend((number, label) for label, pattern in PATTERNS.items() if pattern.search(line))
    return found


def test_no_tracked_file_holds_a_real_shaped_payment_credential() -> None:
    offenders: list[str] = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, label in _hits(text):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{number} looks like {label}")
    assert offenders == [], "\n".join(offenders)  # file and line only: never the match itself


class TestTheScannerItself:
    """A scan that cannot find anything proves nothing. The samples are assembled at run time so
    this file never contains a string the scan would flag."""

    def test_it_finds_each_shape(self) -> None:
        test_secret = "cfsk_ma_" + "test_" + "a1b2c3d4e5f6a7b8c9d0"  # pragma: allowlist secret
        prod_secret = "cfsk_ma_" + "prod_" + "A1B2C3D4E5F6A7B8C9D0e1"  # pragma: allowlist secret
        samples = {
            "cashfree secret": test_secret,
            "cashfree prod secret": prod_secret,
            "sandbox app id": "TEST" + "10" + "9876543",
            "sandbox app id, hex tail": "TEST" + "00ff" * 8,
            "sandbox app id, digits then letters": "TEST" + "10" + "9876543" + "abcdef" * 2,
            "razorpay test": "rzp_" + "test_" + "AbCdEfGhIjKlMn",
            "razorpay live": "rzp_" + "live_" + "AbCdEfGhIjKlMn",
        }
        for name, sample in samples.items():
            assert _hits(f'KEY = "{sample}"'), f"the scan misses {name}"

    @pytest.mark.parametrize(
        "fixture",
        [
            "cfsk_ma_test_dev-only-0001",  # a hyphen inside the tail
            "TESTdev-only-app",  # no digits after TEST
            "rzp_test_Devonly000001",  # 13 characters
            "rzp_test_Merchant0001",  # 12 characters
            "rzp_live_LiveKeyNotAllowed1",  # 18 characters
            "rzp_test_1DP5mmOlF5G5ag",  # Razorpay's own documentation example
            "dev-only-razorpay-webhook-secret",
        ],
    )
    def test_the_repos_own_fixtures_are_not_flagged(self, fixture: str) -> None:
        assert _hits(f'KEY = "{fixture}"') == []

    def test_a_hit_reports_where_and_never_what(self) -> None:
        secret = "cfsk_ma_" + "test_" + "a1b2c3d4e5f6a7b8c9d0"  # pragma: allowlist secret
        hits = _hits(f"line one\nCASHFREE_SECRET_KEY={secret}\n")
        assert hits == [(2, "a Cashfree secret key (cfsk_ma_...)")]
        assert secret not in repr(hits)
