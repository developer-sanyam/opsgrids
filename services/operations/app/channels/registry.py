"""Which connector answers for which provider: webhook verifiers, public webhook slugs, the tracking
mappers and the `CarrierClient` factory. One place a new courier is added, so `webhooks.py` (the
generic inbox) never imports a provider.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from app.channels import delhivery, shiprocket
from app.channels.carrier import CarrierClient, CarrierCredential
from app.channels.webhooks import VERIFIERS as _BASE_VERIFIERS
from app.channels.webhooks import WebhookVerifier
from app.config import settings
from platform_core.errors import ValidationProblem

VERIFIERS: dict[str, WebhookVerifier] = {
    **_BASE_VERIFIERS,
    "shiprocket": shiprocket.ShiprocketVerifier(),
    "delhivery": delhivery.DelhiveryVerifier(),
}

#: The last path segment of a public webhook URL -> the provider it belongs to. Absent means the
#: segment is the provider name. Shiprocket forbids "shiprocket", "sr" and "kr" in a webhook URL.
WEBHOOK_SLUGS: dict[str, str] = {"parcel-track": "shiprocket"}

#: provider -> `(minimised payload) -> (awb, platform status, occurred_at) | None`.
TRACKING_MAPPERS: dict[str, Callable[[Mapping[str, Any]], tuple[str, str, datetime] | None]] = {
    "shiprocket": shiprocket.tracking_update,
    "delhivery": delhivery.tracking_update,
}


def build_carrier_client(provider: str, credential: CarrierCredential) -> CarrierClient:
    """The adapter for `provider`, built from the opened credential. No I/O here."""
    if provider == "shiprocket":
        return shiprocket.ShiprocketClient(
            base_url=settings().shiprocket_api_base,
            email=credential.key,
            password=credential.secret,
        )
    if provider == "delhivery":
        return delhivery.DelhiveryClient(
            base_url=settings().delhivery_api_base, token=credential.key
        )
    raise ValidationProblem(f"{provider!r} is not a carrier this build can book with")
