"""`platform_core.pagination` keyset cursors — what console's `/admin/audit` and `/admin/usage`
and catalog's operator lists page with."""

from __future__ import annotations

import base64

import pytest

from platform_core.errors import ValidationProblem
from platform_core.pagination import decode_keyset_cursor as decode_cursor
from platform_core.pagination import encode_keyset_cursor as encode_cursor


class TestCursorRoundTrip:
    def test_a_cursor_decodes_back_to_the_parts_it_was_encoded_from(self) -> None:
        token = encode_cursor("2026-01-05T00:00:00+00:00", "11111111-1111-1111-1111-111111111111")
        assert decode_cursor(token, expected_parts=2) == [
            "2026-01-05T00:00:00+00:00",
            "11111111-1111-1111-1111-111111111111",
        ]

    def test_three_part_cursors_round_trip_too(self) -> None:
        token = encode_cursor("org-a", "seats", "2026-01")
        assert decode_cursor(token, expected_parts=3) == ["org-a", "seats", "2026-01"]


class TestMalformedCursorsAreRejected:
    def test_garbage_is_a_validation_problem_not_a_crash(self) -> None:
        with pytest.raises(ValidationProblem):
            decode_cursor("not-a-real-cursor!!", expected_parts=2)

    def test_a_cursor_with_the_wrong_number_of_parts_is_rejected(self) -> None:
        token = encode_cursor("only-one-part")
        with pytest.raises(ValidationProblem):
            decode_cursor(token, expected_parts=2)


class TestTheWireFormatIsPinned:
    def test_the_token_is_urlsafe_base64_of_unit_separator_joined_parts(self) -> None:
        # Tokens already in clients' hands must keep decoding, so the format is part of the API.
        token = encode_cursor("a", "b")
        assert base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8") == "a\x1fb"

    def test_invalid_utf8_is_a_validation_problem(self) -> None:
        token = base64.urlsafe_b64encode(b"\xff\xfe").decode("ascii")
        with pytest.raises(ValidationProblem):
            decode_cursor(token, expected_parts=1)
