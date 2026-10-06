"""HTTP surface for payment."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import settings
from app.deps import get_provider
from app.models import SCHEMA
from app.providers.base import PaymentProvider, credentials_mode_of
from app.schemas import ServiceInfo

router = APIRouter(tags=["payment"])


@router.get("/payment/info", response_model=ServiceInfo, summary="What this service is")
async def info(provider: Annotated[PaymentProvider, Depends(get_provider)]) -> ServiceInfo:
    """The service, the provider new checkouts go to and what it runs on: `mode` is `test` when that
    provider is on test / sandbox credentials (a staging box under `PAYMENT_ALLOW_TEST_KEYS`).
    Names and modes only — never a key or any part of one."""
    cfg = settings()
    return ServiceInfo(
        service=cfg.service_name,
        schema_name=SCHEMA,
        provider=provider.name,
        mode=credentials_mode_of(provider),
        test_keys_allowed=bool(getattr(cfg, "payment_allow_test_keys", False)),
    )
