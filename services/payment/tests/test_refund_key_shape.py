"""The platform's refund idempotency key fits what both providers accept (ADR-0029).

`services.create_refund` used to send `refund:<uuid>` — 43 characters containing a `:`. Razorpay's
`X-Refund-Idempotency` wants at least 10 characters of `[A-Za-z0-9_-]`, so the first live refund
would have been refused; Cashfree's refund ids are 3-40 alphanumerics. Refunds are not exposed in
this release (there is no route, and `create_refund` still calls the provider inline), but the key
is fixed now rather than discovered later.
"""

from __future__ import annotations

import re
import uuid

from app import services

RAZORPAY_KEY = re.compile(r"^[A-Za-z0-9_-]{10,}$")
CASHFREE_REFUND_ID = re.compile(r"^[A-Za-z0-9]{3,40}$")


def test_the_key_fits_razorpays_and_cashfrees_rules() -> None:
    key = services.refund_provider_key(uuid.uuid4())
    assert RAZORPAY_KEY.fullmatch(key) and CASHFREE_REFUND_ID.fullmatch(key)
    assert len(key) == 39 and key.startswith("rfd")


def test_the_old_shape_failed_both() -> None:
    old = f"refund:{uuid.uuid4()}"
    assert len(old) == 43 and RAZORPAY_KEY.fullmatch(old) is None
    assert CASHFREE_REFUND_ID.fullmatch(old) is None


def test_the_key_is_stable_per_refund_and_distinct_between_refunds() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    assert services.refund_provider_key(first) == services.refund_provider_key(first)
    assert services.refund_provider_key(first) != services.refund_provider_key(second)
