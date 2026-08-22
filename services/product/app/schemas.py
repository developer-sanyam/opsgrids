"""Request and response contracts for product.

This module is the OpenAPI surface: a change here is a change to the published contract,
and the frontend generates its client from it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


class ProductOut(BaseModel):
    """One entry in the marketplace."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    tagline: str | None = None
    description: str | None = None
    category: str | None = None
    status: str
    connects_to: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    is_platform: bool = False


class ActivationOut(BaseModel):
    """What an organization has, and whether it currently counts.

    `entitles` is computed rather than stored, so a lapsed trial reads as false the moment it
    lapses. A client showing `status` alone would tell somebody they still have a product the
    gateway is already refusing.
    """

    model_config = ConfigDict(from_attributes=True)

    product_key: str
    status: str
    entitles: bool
    activated_at: datetime
    trial_ends_at: datetime | None = None
    deactivated_at: datetime | None = None


class ActivateIn(BaseModel):
    product_key: str
    #: Omitted means a straight activation. Set for a time-boxed trial.
    trial_days: int | None = Field(default=None, ge=1, le=365)
