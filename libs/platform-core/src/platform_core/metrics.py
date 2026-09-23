"""Hand-written Prometheus text exposition. No `prometheus_client` dependency — this
platform's `/metrics` surfaces (`app.py`'s health router, on every service, and
`ops_monitor.server`'s own) are a handful of gauges each, and the exposition format is a few
lines of plain text, not a reason to add a dependency every service would then carry.

Lives at the top of `platform_core` rather than under `ops_monitor` because `app.py` needs it
too — a service's own `/metrics` predates and is independent of the edge box's monitor.

https://prometheus.io/docs/instrumenting/exposition_formats/#text-based-format — the subset
this needs: `# HELP`, `# TYPE`, and `name{label="value", ...} number`, one metric per line,
trailing newline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Sample:
    name: str
    value: float
    labels: Mapping[str, str] | None = None


def _format_labels(labels: Mapping[str, str] | None) -> str:
    if not labels:
        return ""
    parts = ", ".join(f'{key}="{value}"' for key, value in labels.items())
    return "{" + parts + "}"


def render(
    samples: list[Sample],
    *,
    help_text: Mapping[str, str] | None = None,
    metric_types: Mapping[str, str] | None = None,
) -> str:
    """Render `samples` as Prometheus text exposition format.

    `help_text`/`metric_types` are keyed by metric name and emitted once per name, before its
    first sample — Prometheus's own scrape parser accepts (and every example in its own docs
    shows) `# HELP`/`# TYPE` once per metric name, not once per labelled sample.
    """
    help_text = help_text or {}
    metric_types = metric_types or {}
    lines: list[str] = []
    seen: set[str] = set()
    for sample in samples:
        if sample.name not in seen:
            seen.add(sample.name)
            if sample.name in help_text:
                lines.append(f"# HELP {sample.name} {help_text[sample.name]}")
            if sample.name in metric_types:
                lines.append(f"# TYPE {sample.name} {metric_types[sample.name]}")
        lines.append(f"{sample.name}{_format_labels(sample.labels)} {sample.value}")
    return "\n".join(lines) + "\n" if lines else ""
