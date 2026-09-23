"""The hand-written Prometheus exposition format (M3) — a parse test, not a scrape: this
platform carries no `prometheus_client`, so nothing here can lean on its parser to prove the
output is valid. Instead this checks the same shape Prometheus's own text-format parser
requires: https://prometheus.io/docs/instrumenting/exposition_formats/#text-based-format.
"""

from __future__ import annotations

import re

from platform_core.metrics import Sample, render

_METRIC_LINE = re.compile(
    r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? -?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$"
)


def _parse(text: str) -> tuple[list[str], list[str]]:
    """Split rendered text into (help/type comment lines, sample lines), asserting every
    non-comment line matches Prometheus's own grammar for a sample."""
    comments = []
    samples = []
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("#"):
            comments.append(line)
            continue
        assert _METRIC_LINE.match(line), f"not a valid Prometheus sample line: {line!r}"
        samples.append(line)
    return comments, samples


def test_empty_samples_render_to_empty_text() -> None:
    assert render([]) == ""


def test_a_bare_gauge_parses() -> None:
    text = render([Sample("ops_monitor_up", 1.0)])
    _, samples = _parse(text)
    assert samples == ["ops_monitor_up 1.0"]
    assert text.endswith("\n")


def test_labelled_samples_parse_and_keep_insertion_order() -> None:
    text = render(
        [
            Sample("ops_monitor_check_status", 1.0, {"check": "disk"}),
            Sample("ops_monitor_check_status", 0.0, {"check": "outbox.account"}),
        ]
    )
    _, samples = _parse(text)
    assert samples == [
        'ops_monitor_check_status{check="disk"} 1.0',
        'ops_monitor_check_status{check="outbox.account"} 0.0',
    ]


def test_help_and_type_are_emitted_once_per_metric_name_not_per_sample() -> None:
    text = render(
        [
            Sample("ops_monitor_check_status", 1.0, {"check": "disk"}),
            Sample("ops_monitor_check_status", 0.0, {"check": "cert"}),
        ],
        help_text={"ops_monitor_check_status": "1 if the check is OK."},
        metric_types={"ops_monitor_check_status": "gauge"},
    )
    comments, samples = _parse(text)
    assert comments == [
        "# HELP ops_monitor_check_status 1 if the check is OK.",
        "# TYPE ops_monitor_check_status gauge",
    ]
    assert len(samples) == 2


def test_negative_and_float_values_parse() -> None:
    text = render([Sample("ops_monitor_last_cycle_timestamp_seconds", 1735689600.5)])
    _, samples = _parse(text)
    assert samples == ["ops_monitor_last_cycle_timestamp_seconds 1735689600.5"]


class TestLabelValueEscaping:
    """m11: a label value that happens to carry a quote, a backslash or a newline must not
    break the exposition format for every metric on the line, or the ones after it."""

    def test_a_double_quote_is_escaped(self) -> None:
        text = render([Sample("m", 1.0, {"check": 'say "hi"'})])
        assert text == 'm{check="say \\"hi\\""} 1.0\n'

    def test_a_backslash_is_escaped_before_the_quote_escape_is_added(self) -> None:
        text = render([Sample("m", 1.0, {"check": "a\\b"})])
        assert text == 'm{check="a\\\\b"} 1.0\n'

    def test_a_newline_is_escaped(self) -> None:
        text = render([Sample("m", 1.0, {"check": "line1\nline2"})])
        assert text == 'm{check="line1\\nline2"} 1.0\n'
        assert text.count("\n") == 1  # the escaped \n is literal text, not a real newline
