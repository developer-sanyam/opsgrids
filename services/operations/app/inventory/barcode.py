"""The scan payload (inventory Phase 9, ADR-0027): what a label's QR code and Code 128 bar code
carry, and how a scanner's output is read back.

**Pure functions, no I/O.** `og:<type>:<code>` where `<type>` is `item`, `lot`, `roll` or
`location` and `<code>` is the entity's printed code (its `barcode` when it has one, otherwise
its natural key: SKU, lot number, roll number; a location's barcode). The prefix keeps a scan of
a lot number from being mistaken for a SKU that happens to read the same, and tells the resolver
which table to look in. A scan with no `og:` prefix is a *bare code* — a legacy location label or
a manufacturer's EAN — and is looked up across every type.

The frontend has its own copy of the same two functions (`lib/scanPayload.ts`); both are tested
against `libs/contracts/scan-payload-vectors.json`, so they cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ScanType = Literal["item", "lot", "roll", "location"]
SCAN_TYPES: tuple[ScanType, ...] = ("item", "lot", "roll", "location")

PREFIX = "og"
#: Longest code any entity can carry (`barcode` is `String(64)`).
MAX_CODE_LENGTH = 64


class ScanPayloadError(ValueError):
    """The scanned text is not something this system could have printed."""


@dataclass(frozen=True)
class ScanPayload:
    #: `None` for a bare code (no `og:` prefix) — the resolver then tries every type.
    type: ScanType | None
    code: str


def code128_encodable(code: str) -> bool:
    """Code 128 subset B carries printable ASCII (32-126) — the only characters a label may
    use, so a code that passes here can always be printed as a bar code."""
    return bool(code) and all(32 <= ord(ch) <= 126 for ch in code)


def format_payload(type_: ScanType, code: str) -> str:
    """The text a label encodes. Refuses a code that could not be printed or read back."""
    if type_ not in SCAN_TYPES:
        raise ScanPayloadError(f"Unknown label type {type_!r}")
    _check_code(code)
    return f"{PREFIX}:{type_}:{code}"


def parse_payload(text: str) -> ScanPayload:
    """Read what a scanner (or a person typing) produced. Surrounding whitespace — scanners
    append a newline or a tab — is dropped; nothing else is rewritten."""
    value = text.strip()
    if not value:
        raise ScanPayloadError("Nothing was scanned")
    if value.startswith(f"{PREFIX}:"):
        parts = value.split(":", 2)
        if len(parts) != 3:
            raise ScanPayloadError(f"{value!r} is not a valid OpsGrids label")
        _, type_, code = parts
        if type_ not in SCAN_TYPES:
            raise ScanPayloadError(f"{value!r} names an unknown label type {type_!r}")
        _check_code(code)
        return ScanPayload(type=type_, code=code)
    _check_code(value)
    return ScanPayload(type=None, code=value)


def _check_code(code: str) -> None:
    if not code:
        raise ScanPayloadError("The label has no code")
    if len(code) > MAX_CODE_LENGTH:
        raise ScanPayloadError(f"A code is at most {MAX_CODE_LENGTH} characters")
    if not code128_encodable(code):
        raise ScanPayloadError("A code may only contain printable ASCII characters")
