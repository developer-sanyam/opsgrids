"""HTTP surface for operations."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.models import SCHEMA
from app.schemas import ServiceInfo

router = APIRouter(tags=["operations"])


@router.get("/operations/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)
