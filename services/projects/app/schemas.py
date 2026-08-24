"""Request and response contracts for projects.

This module is the OpenAPI surface: a change here is a change to the published contract,
and the frontend generates its client from it.
"""

from __future__ import annotations

from pydantic import BaseModel


class ServiceInfo(BaseModel):
    service: str
    schema_name: str
