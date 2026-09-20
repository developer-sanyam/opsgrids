"""Entry point for the document service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Document service",
    description=(
        "The platform's **public asset store** (ADR-0009), not a document vault. A merchant "
        "uploads an image; it is decoded, downscaled and re-encoded, and written to a bucket "
        "that is world-readable in its entirety under a key of two UUIDs. The reference it "
        "returns never expires, because nothing about it is signed — which is the single "
        "property the design exists to get: a presigned URL stored inside a merchant's store "
        "document would serve AccessDenied a week later, with nothing failing on our side. "
        "This service never serves image bytes and offers no way to store a private object; "
        "the private half is named and unbuilt, so the failure mode for a confidential file "
        "is a missing feature rather than a misplaced payslip."
    ),
    routers=[router],
    # Redis is a declared dependency because the upload route is rate limited, and the limiter
    # fails closed by design — an unverifiable limit must not become an amplifier.
    redis=True,
    outbox_model=OutboxEvent,
)
