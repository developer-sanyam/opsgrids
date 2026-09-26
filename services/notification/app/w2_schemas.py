"""Request and response contracts for WhatsApp template management (slice W2).

`WhatsAppTemplateOut` is a superset of `ProviderTemplateOut`: every field the older screen reads
is still here, and Meta's own vocabulary (`meta_template_id`, `rejection_reason`, `body_preview`)
sits beside it under the names the API contract publishes. `variables` items carry the contract's
`{index, name, kind, example}` and, when known, the send path's mapping (`key`, `component`,
`position`, `button_index`, `parameter_name`) alongside it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import ParameterFormat, TemplateCategory, TemplateStatus

TemplateQuality = Literal["high", "medium", "low", "unknown"]
VariableSpecKind = Literal["text", "url", "date", "number"]
HistorySource = Literal["meta_webhook", "sync", "submit"]


class TemplateVariableSpecOut(BaseModel):
    index: int
    name: str
    kind: VariableSpecKind
    example: str
    #: The send path's mapping for this placeholder, when there is one. Additive.
    key: str | None = None
    component: str | None = None
    position: int | None = None
    button_index: int | None = None
    parameter_name: str | None = None


class WhatsAppTemplateOut(BaseModel):
    #: The template's code — what a rule refers to, and what `GET /templates/{id}` accepts.
    id: str
    #: The row's own uuid, which `GET /templates/{id}` also accepts.
    row_id: uuid.UUID
    channel: Literal["whatsapp", "email"]
    name: str
    provider_name: str
    meta_template_id: str | None
    provider_template_id: str | None
    language: str
    category: TemplateCategory
    parameter_format: ParameterFormat
    status: TemplateStatus
    quality: TemplateQuality
    rejection_reason: str | None
    status_reason: str | None
    status_checked_at: datetime | None
    components: list[dict[str, Any]]
    body_preview: str
    preview_text: str
    variables: list[TemplateVariableSpecOut]
    sendable: bool
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TemplateHistoryOut(BaseModel):
    status: str
    at: str
    source: HistorySource


class WhatsAppTemplateDetailOut(WhatsAppTemplateOut):
    history: list[TemplateHistoryOut]


class SyncOut(BaseModel):
    synced: int
    created: int
    updated: int
    removed: int


# --------------------------------------------------------------------- create

#: Generous ceilings that only bound the request; the real limits (and their messages, per field)
#: are `w2_domain.validate_create`'s, so an operator sees every problem at once.
_Text = Annotated[str, Field(max_length=5000)]


class TemplateHeaderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "image", "video", "document"]
    text: _Text | None = None
    example: _Text | None = None


class TemplateBodyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: _Text
    examples: list[_Text] = Field(default_factory=list, max_length=100)


class TemplateFooterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: _Text


class TemplateButtonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["quick_reply", "url", "phone"]
    text: _Text
    url: _Text | None = None
    phone: Annotated[str, Field(max_length=40)] | None = None
    #: A full sample URL, required by Meta when `url` ends in a `{{1}}` variable. Additive.
    example: _Text | None = None


class TemplateComponentsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: TemplateHeaderIn | None = None
    body: TemplateBodyIn
    footer: TemplateFooterIn | None = None
    buttons: list[TemplateButtonIn] | None = Field(default=None, max_length=20)


class TemplateCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=600)]
    category: TemplateCategory
    language: Annotated[str, Field(max_length=40)]
    components: TemplateComponentsIn


class TemplatePreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[Annotated[str, Field(max_length=120)], Annotated[str, Field(max_length=1024)]] = (
        Field(default_factory=dict, max_length=100)
    )


class PreviewButtonOut(BaseModel):
    type: Literal["quick_reply", "url", "phone"]
    text: str
    url: str | None = None
    phone: str | None = None


class TemplatePreviewOut(BaseModel):
    text: str
    buttons: list[PreviewButtonOut]
    header: str | None = None
    body: str = ""
    footer: str | None = None
