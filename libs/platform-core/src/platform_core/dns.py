"""DNS answer helpers shared by the services that verify a published record.

Deliberately free of any resolver import: it reads the shape dnspython gives back, so a service
that never resolves anything (or a test with a fake rdata) can use it without the dependency.
"""

from __future__ import annotations

from typing import Any


def answer_text(rdata: Any) -> str:
    """One published value as the organization pasted it.

    A TXT rdata carries `strings` because DNS caps a single character-string at 255 bytes; a
    longer value (a 2048-bit DKIM key is roughly twice that) is published in chunks and the value
    is their concatenation. `to_text()` instead would hand back the quoted, space-separated
    chunks, which matches nothing anyone was shown to publish.
    """
    strings: tuple[bytes, ...] | None = getattr(rdata, "strings", None)
    if strings is not None:
        return b"".join(strings).decode("utf-8", "replace")
    return str(rdata.to_text())
