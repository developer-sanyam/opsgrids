"""`app.domain.ALERT_KINDS` and `alert_contact` (fulfilment plan §4; F12) — no I/O, no session,
pure functions, mirroring `test_fulfilment_domain.py`'s own shape for `PICK_TASK_STATUSES`.
"""

from __future__ import annotations

from app import domain
from app.alerts.selectors import AlertSettings


def test_alert_kinds_match_the_models_check_constraint() -> None:
    """`domain.ALERT_KINDS` is `OperationalAlert.kind`'s vocabulary — mirrored, not imported, in
    the model's own `CheckConstraint` (`app/models.py`) and the migration that creates it
    (`kind IN ('low_stock', 'task_delayed', 'shipment_delayed')` in both), the same "the Python
    constant and the DB constraint must agree" check `test_fulfilment_domain.py`'s own
    `PICK_TASK_STATUSES` test makes for the pick-task state machine."""
    assert set(domain.ALERT_KINDS) == {"low_stock", "task_delayed", "shipment_delayed"}


def test_alert_contact_reads_email_and_phone_off_any_resolved_settings_shape() -> None:
    """Both the sweep's own lightweight `AlertSettings` and a live `FulfilmentSettings` ORM row
    satisfy `_AlertContactSource` structurally — this only exercises the former, since building
    the latter needs a session."""
    settings = AlertSettings(task_sla_minutes=60, alert_email="ops@example.com", alert_phone=None)
    assert domain.alert_contact(settings) == {"email": "ops@example.com", "phone": None}
