"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Category,
    DeliveryZone,
    Enquiry,
    Product,
    Storefront,
    ThemeDocument,
    ZonePostalCode,
)
from platform_core.auth import Principal, Scope, ScopeContext

STORE_READ = "storefront.store.settings.read"
STORE_WRITE = "storefront.store.settings.write"
ZONE_READ = "storefront.delivery.zone.read"
ZONE_WRITE = "storefront.delivery.zone.write"
CATALOGUE_READ = "storefront.catalogue.product.read"
CATALOGUE_WRITE = "storefront.catalogue.product.write"
ENQUIRY_READ = "storefront.sales.enquiry.read"
ENQUIRY_WRITE = "storefront.sales.enquiry.write"
ENQUIRY_UNMASK = "storefront.sales.enquiry.unmask"

ALL_PERMISSIONS = (
    STORE_READ,
    STORE_WRITE,
    ZONE_READ,
    ZONE_WRITE,
    CATALOGUE_READ,
    CATALOGUE_WRITE,
    ENQUIRY_READ,
    ENQUIRY_WRITE,
    ENQUIRY_UNMASK,
)


def principal_for(
    org_id: uuid.UUID,
    perms: list[str],
    *,
    user_id: uuid.UUID | None = None,
    amr: list[str] | None = None,
) -> Principal:
    """The principal the gateway would have forwarded.

    There is no `subject_ref` and that is not an omission: every permission in this catalogue
    is ORG-only, so nothing in this service ever narrows by the caller's own identity — a
    delivery zone, a category and a product all have no owner.
    """
    return Principal(
        sub=user_id or uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        perms=perms,
        entitlements=["storefront"],
        amr=amr or [],
    )


def all_perms(org_id: uuid.UUID) -> Principal:
    """Every permission, ORG scope, **and the `otp` step-up** — so this principal can also
    reach `storefront.sales.enquiry.unmask`, which `require()` guards with `amr={"otp"}`."""
    return principal_for(org_id, [f"{code}:ORG" for code in ALL_PERMISSIONS], amr=["otp"])


def ctx_for(org_id: uuid.UUID, permission: str, scope: Scope = Scope.ORG) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(org_id, [f"{permission}:{scope.value}"])
    return ScopeContext(principal=principal, scope=scope, permission=permission)


# --------------------------------------------------------------------- seeding


async def seed_store(
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    slug: str,
    *,
    name: str = "Verma Traders",
    tagline: str | None = None,
    logo_text: str | None = None,
    status: str = "published",
    currency: str = "INR",
    timezone: str = "Asia/Kolkata",
    custom_domain: str | None = None,
    custom_domain_status: str | None = None,
) -> Storefront:
    """Write a storefront on the BYPASSRLS role, committed, as the merchant already would have.

    `custom_domain` is seeded here rather than through the API in the tests that need another
    organization to already hold a domain — which is the only way to reach the cross-tenant
    collision, because the colliding row is invisible to the tenant that collides with it.

    `custom_domain_status` defaults to `unverified`, which is the only value a merchant route can
    reach today. **A test that wants a host to actually resolve has to name `verified`**, and
    that asymmetry is the point: ADR-0012's host lookup serves a proved claim and refuses a
    stated one, so a claim seeded the ordinary way must be invisible to a stranger.
    """
    store = Storefront(
        organization_id=org_id,
        slug=slug,
        name=name,
        tagline=tagline,
        logo_text=logo_text,
        status=status,
        currency=currency,
        timezone=timezone,
        custom_domain=custom_domain,
        custom_domain_status=(custom_domain_status or "unverified") if custom_domain else None,
    )
    admin_session.add(store)
    await admin_session.commit()
    return store


def store_payload(slug: str, **overrides: object) -> dict[str, object]:
    """The smallest valid `PUT /storefront/store` body, plus whatever the test is testing.

    `name` is required and `status` is **not a field** — publishing is its own transition
    (ADR-0011 §4) — so a payload built by hand in twenty places would encode both rules twenty
    times and drift from the schema the first time one of them changed.
    """
    return {"slug": slug, "name": "Verma Traders", **overrides}


async def seed_zone(
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    *,
    code: str = "CENTRAL",
    name: str = "Central",
    mode: str = "PROMISED",
    promise_kind: str | None = "DURATION",
    promise_minutes: int | None = 30,
    promise_offset_days: int | None = None,
    promise_cutoff_time: dt.time | None = None,
    is_active: bool = True,
    delivery_fee_minor: int = 2900,
    free_delivery_above_minor: int | None = 49900,
    min_order_minor: int = 19900,
    buyer_note: str | None = None,
    postal_codes: tuple[str, ...] = (),
) -> DeliveryZone:
    zone = DeliveryZone(
        organization_id=org_id,
        code=code,
        name=name,
        mode=mode,
        promise_kind=promise_kind,
        promise_minutes=promise_minutes,
        promise_offset_days=promise_offset_days,
        promise_cutoff_time=promise_cutoff_time,
        is_active=is_active,
        delivery_fee_minor=delivery_fee_minor,
        free_delivery_above_minor=free_delivery_above_minor,
        min_order_minor=min_order_minor,
        buyer_note=buyer_note,
    )
    admin_session.add(zone)
    await admin_session.flush()
    for postal_code in postal_codes:
        admin_session.add(
            ZonePostalCode(
                organization_id=org_id,
                zone_id=zone.id,
                postal_code=postal_code,
            )
        )
    await admin_session.commit()
    return zone


def csv_body(*rows: tuple[str, str], header: str = "postal_code,zone_code") -> str:
    return "\n".join([header, *(f"{a},{b}" for a, b in rows), ""])


# ----------------------------------------------------------- catalogue + theme + enquiries


async def seed_theme_document(
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    *,
    theme_key: str = "market-fresh",
    mode: str = "retail",
    tokens: dict | None = None,
    pages: list | None = None,
    version: int = 1,
) -> ThemeDocument:
    default_page = {"id": "home", "route": "/", "name": "Home", "sections": []}
    doc = ThemeDocument(
        organization_id=org_id,
        theme_key=theme_key,
        mode=mode,
        tokens=tokens if tokens is not None else {"colorBg": "#fff"},
        pages=pages if pages is not None else [default_page],
        version=version,
    )
    admin_session.add(doc)
    await admin_session.commit()
    return doc


async def seed_category(
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    *,
    key: str = "seasonal",
    label: str = "Seasonal",
    is_active: bool = True,
) -> Category:
    category = Category(organization_id=org_id, key=key, label=label, is_active=is_active)
    admin_session.add(category)
    await admin_session.commit()
    return category


async def seed_product(
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    category_id: uuid.UUID,
    *,
    code: str = "SKU-1",
    name: str = "Basmati Rice 25kg",
    price_min_minor: int = 190000,
    price_max_minor: int = 190000,
    mrp_minor: int | None = None,
    status: str = "active",
    is_active: bool = True,
) -> Product:
    product = Product(
        organization_id=org_id,
        code=code,
        name=name,
        category_id=category_id,
        price_min_minor=price_min_minor,
        price_max_minor=price_max_minor,
        mrp_minor=mrp_minor,
        status=status,
        is_active=is_active,
    )
    admin_session.add(product)
    await admin_session.commit()
    return product


async def seed_enquiry(
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    *,
    name: str = "Priya Shah",
    company: str = "Shah Traders",
    email_masked: str = "p•••@s•••",
    phone_masked: str = "•••••• 3210",
    status: str = "new",
) -> Enquiry:
    enquiry = Enquiry(
        organization_id=org_id,
        name=name,
        company=company,
        email_masked=email_masked,
        phone_masked=phone_masked,
        requirement="Need a quote for 500 bags.",
        quantity="500 bags",
        lines=[],
        status=status,
    )
    admin_session.add(enquiry)
    await admin_session.commit()
    return enquiry
