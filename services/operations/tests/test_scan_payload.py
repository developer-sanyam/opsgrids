"""The scan payload (`og:<type>:<code>`) — format, parse and round trip, against the vectors the
frontend's `scanPayload.ts` is checked against too (`libs/contracts/scan-payload-vectors.json`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.inventory.barcode import (
    SCAN_TYPES,
    ScanPayloadError,
    code128_encodable,
    format_payload,
    parse_payload,
)

VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[3] / "libs" / "contracts" / "scan-payload-vectors.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", VECTORS["format"], ids=lambda c: c["payload"])
def test_format_matches_the_shared_vectors(case: dict[str, str]) -> None:
    assert format_payload(case["type"], case["code"]) == case["payload"]  # type: ignore[arg-type]


@pytest.mark.parametrize("case", VECTORS["format"], ids=lambda c: c["payload"])
def test_format_then_parse_round_trips(case: dict[str, str]) -> None:
    parsed = parse_payload(format_payload(case["type"], case["code"]))  # type: ignore[arg-type]
    assert (parsed.type, parsed.code) == (case["type"], case["code"])


@pytest.mark.parametrize("case", VECTORS["parse"], ids=lambda c: repr(c["input"]))
def test_parse_matches_the_shared_vectors(case: dict[str, str | None]) -> None:
    parsed = parse_payload(str(case["input"]))
    assert (parsed.type, parsed.code) == (case["type"], case["code"])


@pytest.mark.parametrize("bad", VECTORS["reject"], ids=lambda v: repr(v)[:30])
def test_reject_vectors_are_refused(bad: str) -> None:
    with pytest.raises(ScanPayloadError):
        parse_payload(bad)


def test_every_type_formats() -> None:
    for type_ in SCAN_TYPES:
        assert format_payload(type_, "X1") == f"og:{type_}:X1"


def test_an_unknown_type_cannot_be_formatted() -> None:
    with pytest.raises(ScanPayloadError):
        format_payload("box", "X1")  # type: ignore[arg-type]


def test_code128_subset_b_is_printable_ascii_only() -> None:
    assert code128_encodable("AZaz09 ~!")
    assert not code128_encodable("")
    assert not code128_encodable("café")
    assert not code128_encodable("a\tb")
