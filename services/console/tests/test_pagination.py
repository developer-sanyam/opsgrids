"""`app/pagination.py` — the opaque cursor `/admin/audit` and `/admin/usage` page with."""

from __future__ import annotations

import pytest

from app.pagination import decode_cursor, encode_cursor
from platform_core.errors import ValidationProblem


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
