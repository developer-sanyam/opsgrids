"""`EDGE_WEB_ROOT` is read from the box's `.env` the way every other key is.

`publish-web.sh` documents one rule for reading `.env`: the LAST assignment wins and quotes are
stripped (`_dotenv_value`, the same reading `remote.sh`'s `dotenv_get` gives). The web root was read
with `sed ... | head -1` and no unquoting, so a quoted `EDGE_WEB_ROOT="/srv/x"` became the literal
path `"/srv/x"` (quote characters included) and a duplicated key published to the first value
while the deploy scripts used the last.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "infrastructure/edge/publish-web.sh"


def _text() -> str:
    return SCRIPT.read_text()


def _function() -> str:
    match = re.search(r"^_dotenv_value\(\) \{\n.*?\n\}\n", _text(), re.DOTALL | re.MULTILINE)
    assert match, "_dotenv_value must be defined at the top level of publish-web.sh"
    return match.group(0)


def _read(raw: str) -> str:
    result = subprocess.run(
        ["bash", "-c", f'{_function()}\n_dotenv_value "$1"', "bash", raw],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"/srv/opsgrids/web"', "/srv/opsgrids/web"),
        ("'/srv/opsgrids/web'", "/srv/opsgrids/web"),
        ("/srv/opsgrids/web   # the web root", "/srv/opsgrids/web"),
        ("/srv/opsgrids/web\r", "/srv/opsgrids/web"),
        ("", ""),
    ],
)
def test_the_value_reader_strips_quotes_comments_and_carriage_returns(
    raw: str, expected: str
) -> None:
    assert _read(raw) == expected


def test_the_web_root_is_read_last_assignment_wins_and_unquoted() -> None:
    text = _text()
    capture = re.search(r"^web_root=\$\(ssh_box \"sed -n 's/\^EDGE_WEB_ROOT=//p'.*$", text, re.M)
    assert capture, "the EDGE_WEB_ROOT capture was not found"
    assert "| tail -n 1" in capture.group(0), "the last assignment must win, not the first"
    assert "head -1" not in capture.group(0)
    assert 'web_root=$(_dotenv_value "$web_root")' in text


def test_the_reader_is_defined_before_the_web_root_uses_it() -> None:
    text = _text()
    assert text.index("_dotenv_value() {") < text.index('web_root=$(_dotenv_value "$web_root")')
    assert text.count("_dotenv_value() {") == 1, "one definition, not a copy per call site"
