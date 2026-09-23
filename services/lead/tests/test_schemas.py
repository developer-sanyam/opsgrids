"""m5: an explicit `null` on a PATCH field the database column never allows must be a 422 at
the boundary, not a value that reaches `update_lead` and either no-ops silently or raises an
`IntegrityError`. Pure pydantic — no database needed to prove these.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import LeadPatch


class TestLeadPatchRejectsExplicitNulls:
    @pytest.mark.parametrize("field", ["full_name", "status", "notes"])
    def test_explicit_null_on_a_non_nullable_field_is_refused(self, field: str) -> None:
        with pytest.raises(ValidationError):
            LeadPatch(**{field: None})

    def test_owner_id_may_be_explicitly_null_to_unassign(self) -> None:
        # Unassigning is a real state (`services.update_lead` requires ORG scope for it) --
        # must not be caught by the same guard.
        patch = LeadPatch(owner_id=None)
        assert "owner_id" in patch.model_fields_set

    def test_omitting_a_field_entirely_is_still_fine(self) -> None:
        patch = LeadPatch(notes="left a voicemail")
        assert "status" not in patch.model_fields_set
