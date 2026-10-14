"""`platform_core.dns.answer_text` — a TXT answer is its chunks joined, never `to_text()`."""

from __future__ import annotations

from platform_core.dns import answer_text


class _Txt:
    def __init__(self, *strings: bytes) -> None:
        self.strings = strings

    def to_text(self) -> str:  # what the helper must NOT return for a TXT record
        return " ".join(f'"{s.decode()}"' for s in self.strings)


class _NoStrings:
    def to_text(self) -> str:
        return "10 mail.example.com."


def test_chunks_of_one_value_are_concatenated() -> None:
    assert answer_text(_Txt(b"v=DKIM1; k=rsa; p=AAAA", b"BBBB")) == "v=DKIM1; k=rsa; p=AAAABBBB"


def test_a_single_chunk_is_returned_as_is() -> None:
    assert answer_text(_Txt(b"v=spf1 -all")) == "v=spf1 -all"


def test_non_utf8_bytes_are_replaced_not_raised() -> None:
    assert answer_text(_Txt(b"ok\xff")) == "ok�"


def test_a_record_without_strings_falls_back_to_to_text() -> None:
    assert answer_text(_NoStrings()) == "10 mail.example.com."
