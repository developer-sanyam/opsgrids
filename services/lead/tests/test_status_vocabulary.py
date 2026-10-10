"""A lead's status vocabulary has one home for the rules (`domain.STATUS_ORDER`) and one for the
wire (`schemas.LeadStatus`). A third copy, a tuple in `models.py` that nothing read, was deleted:
what stays true is that the two real ones agree, and that the model does not grow another.
"""

from __future__ import annotations

from typing import get_args

from app import domain, models, schemas


def test_the_rules_and_the_wire_agree_on_the_status_vocabulary() -> None:
    assert tuple(get_args(schemas.LeadStatus)) == domain.STATUS_ORDER


def test_the_model_carries_no_third_copy_of_the_vocabulary() -> None:
    assert not hasattr(models, "STATUSES")
