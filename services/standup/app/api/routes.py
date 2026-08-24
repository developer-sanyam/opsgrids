"""HTTP surface for standup."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.models import SCHEMA
from app.schemas import ServiceInfo

router = APIRouter(tags=["standup"])


@router.get("/standup/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)
