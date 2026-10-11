"""`CRM_CONVERSION_CLEANUP_MODE`: nothing destructive happens unless the owner types exactly `on`.

The mode defaults to `log_only`; `off` and `log_only` are read case-insensitively; `on` is accepted
only in exact lower case, so a case slip (`ON`, `On`) or an unknown word falls back to `log_only`
with one `crm.conversion_cleanup.mode_unknown` line instead of arming the deletion.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from app.config import Settings


def _build(monkeypatch: pytest.MonkeyPatch, value: str | None) -> tuple[Settings, list[dict]]:
    if value is None:
        monkeypatch.delenv("CRM_CONVERSION_CLEANUP_MODE", raising=False)
    else:
        monkeypatch.setenv("CRM_CONVERSION_CLEANUP_MODE", value)
    with capture_logs() as logs:
        cfg = Settings()
    unknown = [e for e in logs if e["event"] == "crm.conversion_cleanup.mode_unknown"]
    return cfg, unknown


def test_the_mode_defaults_to_log_only(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, unknown = _build(monkeypatch, None)
    assert cfg.crm_conversion_cleanup_mode == "log_only"
    assert unknown == []


def test_an_unknown_value_falls_back_to_log_only_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, unknown = _build(monkeypatch, "enabled")
    assert cfg.crm_conversion_cleanup_mode == "log_only"
    assert len(unknown) == 1


@pytest.mark.parametrize(("raw", "expected"), [(" Log_Only ", "log_only"), (" OFF", "off")])
def test_a_value_is_stripped_and_lower_cased(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    cfg, unknown = _build(monkeypatch, raw)
    assert cfg.crm_conversion_cleanup_mode == expected
    assert unknown == []


@pytest.mark.parametrize(
    ("raw", "expected", "logged"),
    [("on", "on", 0), (" on ", "on", 0), ("ON", "log_only", 1), ("On", "log_only", 1)],
)
def test_on_is_accepted_only_in_exact_case(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str, logged: int
) -> None:
    cfg, unknown = _build(monkeypatch, raw)
    assert cfg.crm_conversion_cleanup_mode == expected
    assert len(unknown) == logged


def test_deal_timeout_seconds_defaults_to_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEAL_TIMEOUT_SECONDS", raising=False)
    cfg = Settings()
    assert cfg.deal_timeout_seconds == 3.0
    assert cfg.deal_internal_url
