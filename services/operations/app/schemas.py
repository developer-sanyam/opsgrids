"""Request and response contracts for the operations service.

This module is the published API surface: a change here is a change to the OpenAPI contract, and
the Operations SPA generates its client from it. The field names, nesting and enums mirror
`frontend/product-operations/src/api/types.ts` exactly, so wiring the SPA from fixtures to live
is a flag flip rather than a rewrite.

Money is integer minor units (`*_minor`) in an ISO `currency`, never a float (ADR-0000 §8). A
purchase order's total is **derived from its lines** and does not appear on the wire — the SPA
sums `line_items`, and the overview rollup does the same server-side. Names are denormalised onto
the read models (`supplier_name`, `warehouse`) so a list renders without a cross-service join.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StrictInt,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app import domain
from app.inventory.quantity import MAX_ABS_MILLI, QTY_SCALE
from app.jobwork.domain import RECEIVED_ON_FUTURE_TOLERANCE, ist_today
from platform_core.validation import reject_explicit_null

#: Validates a non-empty string as an email address — used where the field is "email format or
#: empty" rather than always-required, so `EmailStr` itself (which rejects empty) does not fit.
_EMAIL = TypeAdapter(EmailStr)

#: The largest whole-unit quantity the pre-Phase-1 item-level API can accept without its
#: milli-unit equivalent (`* QTY_SCALE`) overflowing what `quantity.MAX_ABS_MILLI` allows through
#: the ledger — bounding it here turns an overflow into a 422 instead of a 500 from the kernel or
#: the database.
_MAX_WHOLE_QTY = MAX_ABS_MILLI // QTY_SCALE


def _validate_email_or_empty(value: str) -> str:
    if not value:
        return value
    try:
        _EMAIL.validate_python(value)
    except Exception as exc:
        raise ValueError("must be a valid email address, or empty") from exc
    return value


# ------------------------------------------------------------------ vocabularies (match the SPA)

Category = Literal["raw_materials", "packaging", "components", "mro", "logistics", "services"]
SupplierStatus = Literal["active", "on_hold", "pending"]
PoStatus = Literal["draft", "approved", "partially_received", "received", "closed", "cancelled"]
AttentionKind = Literal["low_stock", "out_of_stock", "late_delivery"]
AttentionTone = Literal["danger", "late"]
StockMovementKind = Literal["opening", "receipt", "adjustment", "count", "relocation", "issue"]
StockStatus = Literal[
    "AVAILABLE",
    "QUALITY_HOLD",
    "QUARANTINE",
    "DAMAGED",
    "REJECTED",
    "WIP",
    "IN_TRANSIT",
    "AT_JOB_WORKER",
    "BLOCKED",
]
#: An exact quantity on the wire: a decimal string with at most three decimals (ADR-0014 §2).
#: Never a JSON number — a float cannot carry 0.1 exactly, and stock must be exact.
Qty = Annotated[str, Field(pattern=r"^-?\d+(\.\d{1,3})?$", examples=["82.5"])]
#: A percentage with at most two decimals ("5", "2.5") — an item's receipt tolerance. Stored as
#: integer basis points (`InventoryItem.receipt_tolerance_bp`); never a float.
TolerancePct = Annotated[str, Field(pattern=r"^\d{1,3}(\.\d{1,2})?$", examples=["2.5"])]
#: India's Harmonized System Nomenclature code — 4, 6 or 8 digits (the granularity a GST filer
#: chooses), or empty (not every item carries one). Never partial-length digit strings.
_HSN_PATTERN = r"^(\d{4}(\d{2}(\d{2})?)?)?$"
StockMovementReason = Literal["damaged", "lost", "found", "consumed", "returned", "other"]
#: ADR-0024 §5 (amending ADR-0014 §1's zone|rack|bin): the full location-kind vocabulary once
#: fulfilment needs staging, pack stations, docks, receiving, returns and quarantine as places,
#: not stock statuses (`StockStatus`, above, is the separate axis for *what condition* stock is
#: in).
LocationKind = Literal[
    "zone",
    "aisle",
    "rack",
    "shelf",
    "bin",
    "staging",
    "pack_station",
    "dock",
    "receiving",
    "returns",
    "quarantine",
]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ suppliers


class SupplierOut(BaseModel):
    """Mirrors the SPA's `SupplierOut`. `spend_minor` is lifetime spend in minor units of
    `currency`; `on_time_pct` is the trailing on-time delivery rate, 0-100."""

    id: str
    code: str
    name: str
    category: Category
    status: SupplierStatus
    lead_time_days: int
    on_time_pct: float
    spend_minor: int
    currency: str
    open_pos: int
    contact_name: str
    contact_email: str
    location: str
    since: date


class SupplierIn(BaseModel):
    """`POST /suppliers`. `spend_minor`, `open_pos` and `on_time_pct` are system-maintained
    rollups — never accepted here, only ever moved by `_bump_supplier` and the (unbuilt)
    on-time-delivery rollup. `since` defaults to today rather than staying null, so the response
    is never the nullable-column 500 gap-12 found."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    category: Category
    status: SupplierStatus = "active"
    lead_time_days: int = Field(default=0, ge=0, le=365)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    contact_name: str = Field(default="", max_length=120)
    contact_email: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=160)
    since: date = Field(default_factory=date.today)

    @field_validator("contact_email")
    @classmethod
    def _contact_email_valid(cls, value: str) -> str:
        return _validate_email_or_empty(value)


class SupplierPatch(BaseModel):
    """`PATCH /suppliers/{id}`. `code` and `currency` are deliberately absent — join keys and
    the unit spend has already accumulated in, neither may move under a supplier. This is also
    how a supplier's status changes (`active` <-> `on_hold` <-> `pending`)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: Category | None = None
    status: SupplierStatus | None = None
    lead_time_days: int | None = Field(default=None, ge=0, le=365)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_email: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=160)
    since: date | None = None

    @field_validator("contact_email")
    @classmethod
    def _contact_email_valid(cls, value: str | None) -> str | None:
        return value if value is None else _validate_email_or_empty(value)


# ------------------------------------------------------------------ purchase orders


class PoLineItem(BaseModel):
    """One line on a purchase order. `unit_price_minor` is per-unit minor units of the PO
    currency; the line total (`quantity * unit_price_minor`) is summed into the PO total."""

    id: str
    sku: str
    description: str
    quantity: int
    uom: str
    unit_price_minor: int
    received_qty: int
    #: The exact cumulative receipt in the item's stock UOM (`received_qty` is its whole-unit
    #: view) — a decimal string, so a roll's 82.5 m is not rounded to 82.
    received_exact: Qty = "0"
    #: The buyer closed this line short: nothing more is expected against it.
    short_closed: bool = False


class PurchaseOrderOut(BaseModel):
    """Mirrors the SPA's `PurchaseOrderOut`. The total is derived from `line_items`, never
    stored, so it never appears here — the client sums the lines."""

    id: str
    po_number: str
    supplier_id: str
    supplier_name: str
    category: Category
    status: PoStatus
    currency: str
    warehouse: str
    raised_by: str
    created_date: date
    expected_date: date
    received_date: date | None
    line_items: list[PoLineItem] = Field(default_factory=list)
    notes: str | None


class PurchaseOrderReceiptOut(PurchaseOrderOut):
    """What receiving answers with: the order, plus the SKUs of the lines that matched no
    inventory item and therefore posted no stock (a freight or service line, or a SKU nobody
    has created yet)."""

    unmatched_skus: list[str] = Field(default_factory=list)


class PoLineIn(BaseModel):
    """One line on `PurchaseOrderIn`/`PurchaseOrderPatch.lines`. `sku` is free text — a line
    against a SKU with no matching inventory item is a service/logistics line, not an error; it
    simply posts no stock when the PO is received."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)
    quantity: int = Field(ge=1)
    uom: str = Field(default="", max_length=20)
    unit_price_minor: int = Field(default=0, ge=0)


class PurchaseOrderIn(BaseModel):
    """`POST /purchase-orders`. `status`, `number`, `received_date` and `received_qty` are
    server-owned and never accepted here: the number is assigned by `services.next_po_number`,
    status always starts `draft`, and receiving is its own endpoint. `raised_by_id` is always
    `ctx.subject_ref` — never the client's word for who is calling."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID
    warehouse: str = Field(min_length=1, max_length=120)
    expected_date: date
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=2000)
    #: Display name only — `Principal` carries no name; the SPA sends `useAuth().user.full_name`.
    raised_by: str = Field(default="", max_length=120)
    lines: list[PoLineIn] = Field(min_length=1, max_length=200)

    @field_validator("expected_date")
    @classmethod
    def _expected_not_in_the_past(cls, value: date) -> date:
        if value < date.today():
            raise ValueError("expected_date cannot be in the past")
        return value


class PurchaseOrderPatch(BaseModel):
    """`PATCH /purchase-orders/{id}`, draft only. When `lines` is present it is a **full
    replacement** of the PO's lines, never a merge. `status` never appears — every transition
    goes through its own endpoint (`/approve`, `/receive`, `/close`, `/cancel`)."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID | None = None
    warehouse: str | None = Field(default=None, min_length=1, max_length=120)
    expected_date: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=2000)
    lines: list[PoLineIn] | None = Field(default=None, min_length=1, max_length=200)


class ReceiveIn(BaseModel):
    """`POST /purchase-orders/{id}/receive`. The body is optional — an empty POST receives as of
    today — so this schema exists only to validate `received_date` when one is sent."""

    model_config = ConfigDict(extra="forbid")

    received_date: date | None = None

    @field_validator("received_date")
    @classmethod
    def _not_in_the_future(cls, value: date | None) -> date | None:
        if value is not None and value > ist_today() + RECEIVED_ON_FUTURE_TOLERANCE:
            raise ValueError("received_date cannot be in the future")
        return value


class CancelIn(BaseModel):
    """`POST /purchase-orders/{id}/cancel`. The reason is recorded in the audit row's `after`,
    never silently dropped — cancelling committed spend needs a "why" on the record."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


# ------------------------------------------------------------------ units of measure


class UomOut(BaseModel):
    """One entry in the organization's UOM directory (ADR-0014 Phase 1). `is_system` marks a
    unit seeded from the fixed registry; a caller cannot tell the two apart except by this flag —
    both are equally usable as an item's `uom`."""

    id: str
    code: str
    name: str
    dimension: str
    is_discrete: bool
    is_system: bool


# ------------------------------------------------------------------ item categories


class ItemCategoryOut(BaseModel):
    """A node in the item master's own category tree (ADR-0014 Phase 1) — additive to, and
    distinct from, the fixed procurement `category` enum every item already carries."""

    id: str
    code: str
    name: str
    parent_id: str | None


class ItemCategoryIn(BaseModel):
    """`POST /inventory/categories`. `parent_id`, when given, must name an existing category in
    the org."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None


class ItemCategoryPatch(BaseModel):
    """`PATCH /inventory/categories/{id}`. `code` is immutable once created, like a warehouse's."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: str | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        reject_explicit_null(self, ("name",))
        return self


# ------------------------------------------------------------------ inventory


class InventoryItemOut(BaseModel):
    """Mirrors the SPA's `InventoryItemOut`. Stock value = `on_hand * unit_value_minor`;
    low-stock is derived by the client (and the overview) as `on_hand <= reorder_point`.

    Stock is read from the ledger's balances (ADR-0014 §10). `on_hand` is the whole-unit figure
    the API has always returned — exact while stock is a whole number of units, which is all
    the item-level API can create — and `on_hand_qty` / `reserved_qty` / `available_qty` are the
    exact decimal strings every new client should read.
    """

    id: str
    sku: str
    name: str
    category: Category
    warehouse: str
    on_hand: int
    on_hand_qty: Qty
    reserved_qty: Qty
    available_qty: Qty
    reorder_point: int
    uom: str
    unit_value_minor: int
    currency: str
    updated_at: date
    #: Item master (ADR-0014 Phase 1) — every field optional at the domain level; `null`/absent
    #: means "not classified this way", not "unknown".
    item_category_id: str | None
    secondary_uom_id: str | None
    hsn_code: str
    track_lot: bool
    track_roll: bool
    track_expiry: bool
    track_quality: bool
    is_active: bool
    #: How far over the ordered quantity a purchase-order receipt of this item may go (inventory
    #: Phase 2), as a percentage string; "0" means exactly the ordered quantity.
    receipt_tolerance_pct: TolerancePct = "0"
    #: What a customer order line is priced at (ADR-0024 §3) — distinct from `unit_value_minor`
    #: above, which is cost. Both null or both set.
    selling_price_minor: int | None
    selling_price_currency: str | None
    #: The printed / manufacturer code that resolves to this item when scanned (Phase 9); `null`
    #: means the label carries the SKU.
    barcode: str | None = None


class InventoryItemIn(BaseModel):
    """`POST /inventory`. `on_hand` is never accepted — `opening_on_hand` becomes the item's
    first `opening` movement instead, so `on_hand` is always the sum of its movements from the
    moment the row exists."""

    model_config = ConfigDict(extra="forbid")

    #: Optional: leave it out (or blank) and the next code in the organization's series
    #: (`ITM-000001`, ...) is generated. A typed code must be unique within the organization.
    sku: str | None = Field(default=None, min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    category: Category
    #: Must name an existing warehouse in the org — validated against the warehouse directory,
    #: not just a non-empty string.
    warehouse: str = Field(min_length=1, max_length=120)
    uom: str = Field(min_length=1, max_length=20)
    reorder_point: int = Field(default=0, ge=0)
    unit_value_minor: int = Field(default=0, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    opening_on_hand: int = Field(default=0, ge=0, le=_MAX_WHOLE_QTY)
    #: Must name an existing category in the org, when given.
    item_category_id: str | None = None
    #: Must name an existing UOM in the org, when given — an alternate reporting/purchase unit.
    secondary_uom_id: str | None = None
    hsn_code: str = Field(default="", pattern=_HSN_PATTERN)
    track_lot: bool = False
    track_roll: bool = False
    track_expiry: bool = False
    track_quality: bool = False
    is_active: bool = True
    #: How far over the ordered quantity a receipt may go, in percent (at most two decimals).
    receipt_tolerance_pct: TolerancePct = "0"
    #: What a customer order line is priced at (ADR-0024 §3), per one whole stock UOM. Both
    #: given, or neither — an item created with neither can still go on an order, but only with
    #: an explicit per-line price.
    selling_price_minor: int | None = Field(default=None, ge=0)
    selling_price_currency: str | None = Field(default=None, min_length=3, max_length=3)
    #: Unique across the organization's live items; printable ASCII, no spaces.
    barcode: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[!-~]+$")

    @field_validator("sku", mode="before")
    @classmethod
    def _blank_sku_means_generate(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _selling_price_paired(self) -> Self:
        if (self.selling_price_minor is None) != (self.selling_price_currency is None):
            raise ValueError(
                "selling_price_minor and selling_price_currency must be given together"
            )
        return self


class InventoryItemPatch(BaseModel):
    """`PATCH /inventory/{id}`. `sku`, `currency` and — above all — `on_hand` are deliberately
    absent: `sku` is the string a PO line joins on, `currency` is the unit the stock value
    accumulated in, and `on_hand` may only ever move through a movement (`/adjustments`).
    Sending any of the three is a 422 by `extra="forbid"`, not a silently ignored field."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: Category | None = None
    warehouse: str | None = Field(default=None, min_length=1, max_length=120)
    uom: str | None = Field(default=None, min_length=1, max_length=20)
    reorder_point: int | None = Field(default=None, ge=0)
    unit_value_minor: int | None = Field(default=None, ge=0)
    item_category_id: str | None = None
    secondary_uom_id: str | None = None
    hsn_code: str | None = Field(default=None, pattern=_HSN_PATTERN)
    track_lot: bool | None = None
    track_roll: bool | None = None
    track_expiry: bool | None = None
    track_quality: bool | None = None
    is_active: bool | None = None
    receipt_tolerance_pct: TolerancePct | None = None
    #: Both given, both explicitly `null` (clears the price — a real, meaningful state, unlike
    #: the fields `_no_explicit_nulls` guards below), or both omitted.
    selling_price_minor: int | None = Field(default=None, ge=0)
    selling_price_currency: str | None = Field(default=None, min_length=3, max_length=3)
    #: `null` is ignored (cannot clear); send a new code to change it.
    barcode: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[!-~]+$")

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        # `item_category_id`, `secondary_uom_id` and the selling-price pair are deliberately
        # excluded — `null` on any of them is a real, meaningful state ("no category", "no
        # secondary unit", "no selling price"), not a malformed one.
        reject_explicit_null(
            self,
            (
                "name",
                "category",
                "warehouse",
                "uom",
                "reorder_point",
                "unit_value_minor",
                "hsn_code",
                "track_lot",
                "track_roll",
                "track_expiry",
                "track_quality",
                "is_active",
                "receipt_tolerance_pct",
            ),
        )
        return self

    @model_validator(mode="after")
    def _selling_price_paired(self) -> Self:
        has_minor = "selling_price_minor" in self.model_fields_set
        has_currency = "selling_price_currency" in self.model_fields_set
        if has_minor != has_currency:
            raise ValueError("selling_price_minor and selling_price_currency must be set together")
        if has_minor and (self.selling_price_minor is None) != (
            self.selling_price_currency is None
        ):
            raise ValueError(
                "selling_price_minor and selling_price_currency must both be null, or neither"
            )
        return self


ItemAttributeType = Literal["text", "number", "boolean", "date"]


class ItemAttributeOut(BaseModel):
    """One typed key/value fact about an item (ADR-0014 Phase 1). Exactly one of the four
    `value_*` fields is non-null, matching `value_type`."""

    id: str
    key: str
    value_type: ItemAttributeType
    value_text: str | None
    value_number: int | None
    value_boolean: bool | None
    value_date: date | None


class ItemAttributeIn(BaseModel):
    """`PUT /inventory/{id}/attributes/{key}`. **Typed values only**: `value_type` names which
    single `value_*` field is set — the others must be absent — so there is no untyped `value`
    a caller could mass-assign into. `value_number` is an integer (GSM, mm): attributes are not
    stock, but the platform's no-float rule still applies to them (ADR-0014 §2)."""

    model_config = ConfigDict(extra="forbid")

    value_type: ItemAttributeType
    value_text: str | None = Field(default=None, min_length=1, max_length=500)
    value_number: int | None = Field(default=None, ge=-(2**53), le=2**53)
    value_boolean: bool | None = None
    value_date: date | None = None

    @model_validator(mode="after")
    def _exactly_the_named_value(self) -> Self:
        by_type: dict[ItemAttributeType, object | None] = {
            "text": self.value_text,
            "number": self.value_number,
            "boolean": self.value_boolean,
            "date": self.value_date,
        }
        given = {t for t, v in by_type.items() if v is not None}
        if given != {self.value_type}:
            raise ValueError(
                f"value_type={self.value_type!r} requires exactly value_{self.value_type} "
                "and no other value field"
            )
        return self


class ItemVariantOut(BaseModel):
    """A purchasable/stockable variant of an item master row — its own SKU (ADR-0014 Phase 1)."""

    id: str
    inventory_item_id: str
    sku: str
    name: str
    is_active: bool


class ItemVariantIn(BaseModel):
    """`POST /inventory/{id}/variants`."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)


class ItemVariantPatch(BaseModel):
    """`PATCH /inventory/{id}/variants/{variant_id}`. `sku` is immutable once created."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        reject_explicit_null(self, ("name", "is_active"))
        return self


class StockAdjustmentIn(BaseModel):
    """`POST /inventory/{id}/adjustments`. Exactly one of `quantity_delta` (a relative add/remove)
    or `counted_on_hand` (an absolute physical count) — never both, never neither. `reason` is
    required with a delta adjustment (why it moved) and forbidden with a count (a count is a
    fact, not a reason)."""

    model_config = ConfigDict(extra="forbid")

    quantity_delta: int | None = Field(default=None, ge=-_MAX_WHOLE_QTY, le=_MAX_WHOLE_QTY)
    counted_on_hand: int | None = Field(default=None, ge=0, le=_MAX_WHOLE_QTY)
    reason: StockMovementReason | None = None
    note: str = Field(default="", max_length=500)
    occurred_on: date = Field(default_factory=date.today)

    @field_validator("quantity_delta")
    @classmethod
    def _delta_nonzero(cls, value: int | None) -> int | None:
        if value == 0:
            raise ValueError("quantity_delta must not be zero")
        return value

    @field_validator("occurred_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("occurred_on cannot be in the future")
        return value

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> Self:
        has_delta = self.quantity_delta is not None
        has_count = self.counted_on_hand is not None
        if has_delta == has_count:
            raise ValueError("Provide exactly one of quantity_delta or counted_on_hand")
        if has_delta and self.reason is None:
            raise ValueError("reason is required with quantity_delta")
        if has_count and self.reason is not None:
            raise ValueError("reason is not accepted with counted_on_hand")
        return self


class StockMovementOut(BaseModel):
    """One row of an item's ledger, with its move's kind/reason/reference. Append-only, newest
    first. `quantity_delta` / `on_hand_after` are the whole-unit figures the API always returned;
    `quantity` / `balance_after` are exact. `balance_after` is the balance of the row's own
    bucket (`location_id` + `status`), which for a single-location item is its on-hand."""

    id: str
    kind: StockMovementKind
    reason: StockMovementReason | None
    quantity_delta: int
    on_hand_after: int
    quantity: Qty
    balance_after: Qty
    location_id: str
    status: StockStatus
    #: Free text, like `LedgerRowOut.reference_type`: a move says what it was *for* in the
    #: vocabulary ADR-0014 §12 lists (`purchase_order`, `bin_transfer`, `status_change`,
    #: `roll_split`, `putaway`, a sales order ...). It was once the single literal `purchase_order`,
    #: which made this route answer 500 for any item that had ever been transferred.
    reference_type: str | None
    reference_id: str | None
    note: str
    occurred_on: date
    created_at: datetime
    created_by: str | None


class StockBalanceOut(BaseModel):
    """One bucket of an item's stock: where it is, in what state, and how much of it is
    promised. Exact decimal strings in the item's UOM."""

    location_id: str
    warehouse_code: str
    warehouse: str
    location_code: str
    status: StockStatus
    lot_id: str | None
    roll_id: str | None
    on_hand_qty: Qty
    reserved_qty: Qty
    available_qty: Qty


class InventoryDiscrepancyOut(BaseModel):
    """A stored stock figure the reconciler found disagreeing with its source of truth.
    `kind=balance`: the bucket's balance vs the sum of its ledger. `kind=reserved`: its reserved
    figure vs its open reservations. Nothing repairs these automatically."""

    id: str
    kind: Literal["balance", "reserved"]
    inventory_item_id: str
    location_id: str
    status: str
    lot_id: str | None
    roll_id: str | None
    stored_qty: Qty
    expected_qty: Qty
    detected_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_note: str


class InventoryReservationOut(BaseModel):
    """Stock promised to a demand without moving it (ADR-0014 §11). `quantity` is the original
    promise, unchanged by a partial release; `consumed` is what an outbound move has drawn
    against it; `released` is what has been explicitly given back; `transferred` is what a
    relocation has carried to a destination reservation (ADR-0024 §6 — a pick or pack, not
    dispatch); `remaining` is what is still open — the four sum to `quantity`."""

    id: str
    inventory_item_id: str
    quantity: Qty
    consumed: Qty
    released: Qty
    transferred: Qty
    remaining: Qty
    status: Literal["active", "released", "consumed", "transferred"]
    reference_type: str | None
    reference_id: str | None


class ReservationReleaseIn(BaseModel):
    """`POST /inventory/reservations/{id}:release`. An empty body releases the whole open
    remainder; `quantity` releases only that much, leaving the rest active."""

    model_config = ConfigDict(extra="forbid")

    quantity: Qty | None = None


class ReconcileOut(BaseModel):
    """What an on-demand reconciliation of the caller's organization found."""

    run_id: str
    buckets_checked: int
    findings: int
    new_findings: int
    discrepancies: list[InventoryDiscrepancyOut]


class DiscrepancyResolveIn(BaseModel):
    """`POST /inventory/discrepancies/{id}/resolve` — what was done about it, in words."""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=500)


# ------------------------------------------------------------------ operational alerts and
# insight dashboards (fulfilment plan §4; F12)


class OperationalAlertOut(BaseModel):
    """One open (or recently closed) operational condition the alert sweep found. `detail` is
    exactly what the alert's own domain event carried — enough to render the row without
    rejoining the subject table."""

    id: str
    kind: Literal["low_stock", "task_delayed", "shipment_delayed"]
    subject_type: Literal["inventory_item", "pick_task", "shipment"]
    subject_id: str
    warehouse_id: str | None
    detail: dict[str, Any]
    detected_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None


class TaskBacklogOut(BaseModel):
    """Open pick-task count and age per warehouse — the backlog half of the task
    backlog/throughput insight view."""

    warehouse_id: str
    open_tasks: int
    avg_age_minutes: float
    max_age_minutes: float


class ThroughputOut(BaseModel):
    """Tasks closed since the window started, and their average cycle time — the throughput half
    of the same view."""

    warehouse_id: str
    closed_tasks: int
    avg_cycle_minutes: float


class ZoneBottleneckOut(BaseModel):
    """Which zone's tasks took longest to close, most recently — slowest first."""

    zone_location_id: str
    closed_tasks: int
    avg_cycle_minutes: float


class WorkerWorkloadOut(BaseModel):
    """Open task count per worker, busiest first — the workload-distribution view."""

    worker_id: str
    worker_name: str
    open_tasks: int


class DiscrepancyTrendPointOut(BaseModel):
    """One day's count of a discrepancy kind — the exception-trend view over the reconciler's own
    findings (`InventoryDiscrepancyOut`), never a synthetic series."""

    date: str
    kind: Literal["balance", "reserved"]
    count: int


# ------------------------------------------------------------------ warehouses (reference)


class WarehouseOut(BaseModel):
    """A stocking location. Reference data behind the warehouse names the POs and inventory
    rows carry. `lat_micro`/`lng_micro` are the *site's* position on a map in integer
    micro-degrees (10^-6), both set or both null."""

    id: str
    code: str
    name: str
    location: str
    lat_micro: int | None = None
    lng_micro: int | None = None


class WarehouseIn(BaseModel):
    """`POST /warehouses`. `code` is immutable once created — it never appears on
    `WarehousePatch`. `lat_micro`/`lng_micro` place the site on a map: integers (never floats),
    given together or not at all, latitude within +-90 degrees and longitude within +-180."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    location: str = Field(default="", max_length=160)
    lat_micro: StrictInt | None = None
    lng_micro: StrictInt | None = None

    @model_validator(mode="after")
    def _coordinates(self) -> Self:
        if errors := domain.site_coordinate_errors(self.lat_micro, self.lng_micro):
            raise ValueError(errors[0][1])
        return self


class WarehousePatch(BaseModel):
    """`PATCH /warehouses/{id}`. A rename cascades to `purchase_orders.warehouse` and
    `inventory_items.warehouse` in the same transaction — those columns are denormalised copies
    of `name`, not a join.

    `lat_micro`/`lng_micro` are checked against the **merged** state (a patch that sets only the
    latitude is checked against the persisted longitude — `app.inventory.services
    .update_warehouse`); an explicit `null` for both clears the site's position."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=160)
    lat_micro: StrictInt | None = None
    lng_micro: StrictInt | None = None


class WarehouseMapSiteOut(BaseModel):
    """One placed site on the warehouses map — `GET /warehouses/map`. These are *site*
    coordinates (a business address), not a worker's position."""

    id: str
    code: str
    name: str
    location: str
    lat_micro: int
    lng_micro: int
    floor_count: int
    #: Locations in this warehouse that hold stock right now (a positive on-site balance).
    stocked_location_count: int


class WarehouseMapUnplacedOut(BaseModel):
    """A warehouse with no coordinates yet — listed beside the map so it can be placed."""

    id: str
    code: str
    name: str
    location: str


class WarehouseMapOut(BaseModel):
    sites: list[WarehouseMapSiteOut]
    without_coordinates: list[WarehouseMapUnplacedOut]


# ------------------------------------------------------------------ locations (ADR-0024 §5)


class LocationOut(BaseModel):
    """A node in a warehouse's location tree — zone, aisle, rack, shelf, bin, staging, pack
    station, dock, receiving, returns or quarantine (ADR-0024 §5)."""

    id: str
    warehouse_id: str
    parent_id: str | None
    kind: LocationKind
    code: str
    name: str
    is_default: bool
    barcode: str | None
    pick_sequence: int | None
    max_weight_grams: int | None
    max_volume_milli: int | None
    storage_type: str | None


class LocationIn(BaseModel):
    """`POST /warehouses/{warehouse_id}/locations`. `parent_id`, when given, must name an
    existing location in the same warehouse. `barcode`, when given, must be unique across every
    live location in the organization."""

    model_config = ConfigDict(extra="forbid")

    kind: LocationKind = "bin"
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None
    # Printable ASCII, no space and no `/`: a barcode containing either could never be resolved
    # back through `GET /locations/by-barcode/{barcode}`'s path segment, and whitespace/control
    # characters have no business in a value a scanner or a human types verbatim.
    barcode: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[!-.0-~]+$")
    pick_sequence: int | None = Field(default=None, ge=0)
    max_weight_grams: int | None = Field(default=None, ge=0)
    max_volume_milli: int | None = Field(default=None, ge=0)
    storage_type: str | None = Field(default=None, max_length=30)


class LocationPatch(BaseModel):
    """`PATCH /locations/{id}`. Changing `kind` or either capacity field on a location that
    already has ledger history needs `operations.inventory.layout.publish`, not just
    `location.write` (ADR-0024 §5) — enforced in the route, since which permission applies
    depends on what changed and on the location's own history, not on the shape of this
    payload alone."""

    model_config = ConfigDict(extra="forbid")

    kind: LocationKind | None = None
    code: str | None = Field(default=None, min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    barcode: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[!-.0-~]+$")
    pick_sequence: int | None = Field(default=None, ge=0)
    max_weight_grams: int | None = Field(default=None, ge=0)
    max_volume_milli: int | None = Field(default=None, ge=0)
    storage_type: str | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        reject_explicit_null(self, ("kind", "code", "name"))
        return self


# ------------------------------------------------------------------ warehouse geometry (plan §2/
# F7 — floors, versioned layouts, per-version location placements, and the walk graph)

LayoutVersionStatus = Literal["draft", "published", "archived"]


class WarehouseFloorOut(BaseModel):
    id: str
    warehouse_id: str
    floor_number: int
    name: str


class WarehouseFloorIn(BaseModel):
    """`POST /warehouses/{warehouse_id}/floors`."""

    model_config = ConfigDict(extra="forbid")

    floor_number: int = Field(ge=-10, le=200)
    name: str = Field(default="", max_length=120)


class WarehouseFloorPatch(BaseModel):
    """`PATCH /warehouse-floors/{id}`."""

    model_config = ConfigDict(extra="forbid")

    floor_number: int | None = Field(default=None, ge=-10, le=200)
    name: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        reject_explicit_null(self, ("floor_number",))
        return self


class LayoutVersionOut(BaseModel):
    id: str
    warehouse_floor_id: str
    version_number: int
    status: LayoutVersionStatus
    label: str
    published_at: datetime | None
    published_by: str | None
    created_at: datetime
    updated_at: datetime


class LayoutVersionCreateIn(BaseModel):
    """`POST /warehouse-floors/{floor_id}/layout-versions` — opens a new draft. Refused with 409
    if the floor already has one (`app.warehouse.services.open_draft_version`)."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", max_length=200)
    #: Start from a copy of the floor's current published placements/walk-graph (the ordinary
    #: "edit the live layout" case) rather than empty (a genuinely new floor plan).
    clone_from_published: bool = True


class LocationPlacementIn(BaseModel):
    """One entry of a `LayoutGeometryIn.placements` list — where `location_id` sits on this
    draft's floor. Integer millimetres throughout (ADR-0000 §8)."""

    model_config = ConfigDict(extra="forbid")

    location_id: UUID
    x_mm: int = Field(ge=0, le=1_000_000)
    y_mm: int = Field(ge=0, le=1_000_000)
    z_mm: int | None = Field(default=None, ge=0, le=1_000_000)
    width_mm: int = Field(default=1000, gt=0, le=1_000_000)
    depth_mm: int = Field(default=1000, gt=0, le=1_000_000)
    height_mm: int | None = Field(default=None, gt=0, le=1_000_000)


class LocationPlacementOut(BaseModel):
    location_id: str
    x_mm: int
    y_mm: int
    z_mm: int | None
    width_mm: int
    depth_mm: int
    height_mm: int | None
    location_code: str
    location_kind: LocationKind


class PublishedPlacementOut(BaseModel):
    """One location on a floor's *published* layout, with the identity a map needs — so a viewer
    never has to fetch the location tree beside it. Integer millimetres throughout."""

    location_id: str
    code: str
    name: str
    kind: LocationKind
    parent_id: str | None
    x_mm: int
    y_mm: int
    z_mm: int | None
    width_mm: int
    depth_mm: int
    height_mm: int | None


class PublishedLayoutOut(BaseModel):
    """`GET /floors/{floor_id}/published-layout` — what is live on a floor, never a draft.
    `published` is false (with no placements) when the floor has no published layout."""

    floor_id: str
    warehouse_id: str
    floor_name: str
    published: bool
    layout_version_id: str | None
    placements: list[PublishedPlacementOut]


class WalkNodeIn(BaseModel):
    """One entry of a `LayoutGeometryIn.walk_nodes` list. `code` is this draft's own reference for
    the node — what a `WalkEdgeIn` names in `from_code`/`to_code` — never a database id, so a
    caller building the whole graph client-side never has to round-trip one."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    location_id: UUID | None = None
    x_mm: int = Field(ge=0, le=1_000_000)
    y_mm: int = Field(ge=0, le=1_000_000)
    z_mm: int | None = Field(default=None, ge=0, le=1_000_000)


class WalkNodeOut(BaseModel):
    id: str
    code: str
    location_id: str | None
    x_mm: int
    y_mm: int
    z_mm: int | None


class WalkEdgeIn(BaseModel):
    """One entry of a `LayoutGeometryIn.walk_edges` list, naming its endpoints by the `code`s
    named in the same payload's `walk_nodes`."""

    model_config = ConfigDict(extra="forbid")

    from_code: str = Field(min_length=1, max_length=40)
    to_code: str = Field(min_length=1, max_length=40)
    distance_mm: int = Field(gt=0, le=10_000_000)
    is_traversable: bool = True
    bidirectional: bool = True

    @model_validator(mode="after")
    def _no_self_loop(self) -> Self:
        if self.from_code == self.to_code:
            raise ValueError("A walk edge cannot connect a node to itself")
        return self


class WalkEdgeOut(BaseModel):
    id: str
    from_node_id: str
    to_node_id: str
    from_code: str
    to_code: str
    distance_mm: int
    is_traversable: bool
    bidirectional: bool


class LayoutGeometryIn(BaseModel):
    """`PUT /layout-versions/{id}/geometry` — the whole "save draft" payload a 2D canvas editor
    posts. A **full replace**: every placement, walk node and walk edge this draft has is
    dropped and rebuilt from exactly what is given here, which is what lets a canvas save its
    entire current state in one call without diffing against what the server already had. Refused
    (409) on anything but a `draft` version (`app.warehouse.services.save_draft_geometry`) — a
    published version is never edited in place."""

    model_config = ConfigDict(extra="forbid")

    placements: list[LocationPlacementIn] = Field(default_factory=list)
    walk_nodes: list[WalkNodeIn] = Field(default_factory=list)
    walk_edges: list[WalkEdgeIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _codes_and_locations_are_unique_and_resolvable(self) -> Self:
        node_codes = [n.code for n in self.walk_nodes]
        if len(node_codes) != len(set(node_codes)):
            raise ValueError("walk_nodes[].code must be unique within one layout version")
        location_ids = [p.location_id for p in self.placements]
        if len(location_ids) != len(set(location_ids)):
            raise ValueError("placements[].location_id must be unique within one layout version")
        node_location_ids = [n.location_id for n in self.walk_nodes if n.location_id is not None]
        if len(node_location_ids) != len(set(node_location_ids)):
            raise ValueError(
                "walk_nodes[].location_id must be unique within one layout version — at most "
                "one walk node may stand for a given location"
            )
        known = set(node_codes)
        for edge in self.walk_edges:
            if edge.from_code not in known or edge.to_code not in known:
                raise ValueError(
                    f"walk_edges[] names a node code not present in walk_nodes: "
                    f"{edge.from_code!r} -> {edge.to_code!r}"
                )
        return self


class LayoutGeometryOut(BaseModel):
    version: LayoutVersionOut
    placements: list[LocationPlacementOut]
    walk_nodes: list[WalkNodeOut]
    walk_edges: list[WalkEdgeOut]


class LayoutValidationIssueOut(BaseModel):
    #: overlap | disconnected_node.
    kind: str
    message: str
    location_ids: list[str] = Field(default_factory=list)
    node_id: str | None = None


class LayoutValidationOut(BaseModel):
    is_valid: bool
    issues: list[LayoutValidationIssueOut]


class RouteOut(BaseModel):
    """`GET /warehouse-floors/{floor_id}/route` — a computed path over the floor's **published**
    layout, from one location to another (`app.domain.shortest_walk_path`)."""

    from_location_id: str
    to_location_id: str
    node_ids: list[str]
    distance_mm: int


# ------------------------------------------------------------------ overview rollups


class CategorySpend(BaseModel):
    """One category's share of committed spend — grouped from purchase orders. Mirrors the
    SPA's `CategorySpend`."""

    key: Category
    name: str
    spend_minor: int
    #: Share of the resolved total, 0-100.
    pct: int


class StatusSlice(BaseModel):
    """One slice of the PO-status donut — grouped by status. Mirrors the SPA's `StatusSlice`."""

    key: PoStatus
    label: str
    count: int
    pct: int


class SpendPoint(BaseModel):
    """A point on the spend-over-time chart, in minor units. Mirrors the SPA's `SpendPoint`."""

    label: str
    spend_minor: int


class SupplierSpend(BaseModel):
    """One supplier's share of lifetime spend — the supplier-breakdown row on the overview."""

    id: str
    name: str
    category: Category
    spend_minor: int
    #: Share of total supplier spend, 0-100.
    pct: int


class AttentionItem(BaseModel):
    """One row in the "needs attention" queue. Mirrors the SPA's `AttentionItem`."""

    id: str
    kind: AttentionKind
    title: str
    detail: str
    href: str
    tone: AttentionTone


class OverviewOut(BaseModel):
    """The Operations overview's server-side rollup. The SPA can derive all of this from the
    list endpoints; this exposes the same arithmetic once, so a reporting view can share it.

    `open_pos` counts POs still in flight (draft or approved); `inventory_value_minor` is the
    value of stock on hand; `low_stock_count` is items at or below their reorder point;
    `out_of_stock_count` is items with nothing on hand; `on_time_pct` is the real, spend-weighted
    on-time delivery rate computed from received purchase orders (`expected_date` vs
    `received_date`) — `None` when this organization has not received a PO yet, never a
    manufactured percentage. The series and breakdowns feed the charts."""

    open_pos: int
    inventory_value_minor: int
    low_stock_count: int
    out_of_stock_count: int
    on_time_pct: float | None
    currency: str
    spend_trend: list[SpendPoint] = Field(default_factory=list)
    spend_by_category: list[CategorySpend] = Field(default_factory=list)
    po_status_mix: list[StatusSlice] = Field(default_factory=list)
    top_suppliers: list[SupplierSpend] = Field(default_factory=list)
    attention: list[AttentionItem] = Field(default_factory=list)


# ------------------------------------------------------------------ orders (ADR-0024, ADR-0025)

OrderChannel = Literal["phone", "manual", "sales", "walk_in", "marketplace"]
OrderStatus = Literal[
    "DRAFT",
    "PLACED",
    "ACCEPTED",
    "IN_FULFILMENT",
    "ON_HOLD",
    "READY",
    "HANDED_OVER",
    "DELIVERED",
    "COMPLETED",
    "CANCELLED",
    "REJECTED",
]
PaymentTerm = Literal["prepaid", "cash", "credit"]
InvoiceTiming = Literal["on_accept", "on_dispatch", "on_delivery"]


class FulfilmentSettingsIn(BaseModel):
    """`PUT /operations/fulfilment/settings` — replaces the whole policy row for the organization
    (or, with `?warehouse_id=`, that warehouse's override). Every field is required so a saved row
    states each value on purpose; `null` clears an alert contact."""

    model_config = ConfigDict(extra="forbid")

    invoice_timing: InvoiceTiming
    payment_defaults: dict[OrderChannel, PaymentTerm]
    offer_timeout_seconds: int = Field(ge=15, le=3600)
    offer_rounds: int = Field(ge=1, le=10)
    require_pickup_code: bool
    default_tax_rate_pct: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    auto_assignment_enabled: bool
    #: Order invoicing opt-in (INVENTORY-01): READY orders raise a tax invoice only when true.
    auto_invoice_orders: bool
    task_sla_minutes: int = Field(ge=1, le=10080)
    alert_email: EmailStr | None = Field(max_length=254)
    alert_phone: str | None = Field(max_length=20, pattern=r"^\+?[0-9][0-9 ()-]{5,19}$")


class FulfilmentSettingsOut(BaseModel):
    """The effective policy. `source` says where it came from: the warehouse's own override, the
    organization's row, or the built-in default (no row yet)."""

    warehouse_id: UUID | None
    source: Literal["warehouse", "organization", "default"]
    invoice_timing: InvoiceTiming
    payment_defaults: dict[str, str]
    offer_timeout_seconds: int
    offer_rounds: int
    require_pickup_code: bool
    default_tax_rate_pct: Decimal
    auto_assignment_enabled: bool
    auto_invoice_orders: bool
    task_sla_minutes: int
    alert_email: str | None
    alert_phone: str | None
    updated_at: datetime | None
    updated_by: str | None


class OrderLineItem(BaseModel):
    """One line on an order. `selling_price_minor`/`line_total_minor` are `null` until `place`
    prices the line (ADR-0024 §3) — NOT NULL on every line of a `PLACED` order or later.

    `price_overridden`/`catalogue_price_minor` (review MAJOR B) make an explicit per-line price
    visible rather than a silent substitution: `price_overridden` is `True` when this line named
    its own price at create time instead of taking the item's; `catalogue_price_minor` is the
    item's own `selling_price_minor` as of this read, for a reviewer (e.g. whoever accepts the
    order) to compare against `selling_price_minor` above. Neither is stored — both are computed
    on every read (`app.orders.selectors.order_line_out`), so `catalogue_price_minor` always
    reflects the item's *current* price, not the one in force when the line was created."""

    id: str
    inventory_item_id: str
    sku: str
    quantity: Qty
    selling_price_minor: int | None
    line_total_minor: int | None
    price_overridden: bool
    catalogue_price_minor: int | None


class OrderLineIn(BaseModel):
    """One line on `OrderIn`. `selling_price_minor`, when given, is this line's own price and
    `place` will never overwrite it with the item's current `selling_price_minor`; when omitted,
    `place` fills it from the item, and refuses the whole order if the item has none either
    (ADR-0024 §3). Any caller holding `orders.order.create` may set this — ADR-0024 §3 means it
    for an item that has no price of its own, not as a general override, but F2 does not restrict
    who may use it; `OrderLineItem.price_overridden` and the create/place audit rows make every
    use of it visible instead (review MAJOR B)."""

    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    #: Parsed and range-checked by `app.inventory.quantity.parse_qty` at the boundary
    #: (`app/orders/services.py`) — a non-positive or non-UOM-discrete value is a 422 there, not
    #: here, since whether a fraction is legal depends on the item's own UOM.
    quantity: Qty
    #: `le=MAX_ABS_MILLI` — the same order-of-magnitude ceiling `app.inventory.quantity` already
    #: uses for a quantity, comfortably inside `BigInteger`'s ~9.22e18 ceiling — turns an absurd
    #: value (`10**19`) into a clean 422 instead of an unhandled `DataError` at flush (review
    #: MINOR D). It does not by itself bound `quantity * selling_price_minor`; `place_order`
    #: refuses a line total that would still overflow.
    selling_price_minor: int | None = Field(default=None, ge=0, le=MAX_ABS_MILLI)


class OrderOut(BaseModel):
    """Mirrors the SPA's `OrderOut`. `total_minor` is `subtotal_minor` (F2 carries no
    discount/tax/shipping line — those are a later phase's addition); both are `0` until `place`
    prices the lines.

    `invoice_id`/`paid_at`/`pickup_code_verified_at` are the billing-gate facts F4 adds
    (ADR-0025 §1) — `pickup_code` itself is deliberately never on this model (`Order`'s own
    docstring explains why)."""

    id: str
    number: str
    channel: OrderChannel
    status: OrderStatus
    version: int
    warehouse_id: str
    customer_id: str | None
    customer_name: str
    customer_phone: str
    subtotal_minor: int
    total_minor: int
    currency: str
    invoice_id: str | None = None
    paid_at: datetime | None = None
    #: The online payment (a `services/payment` collection id) whose verified capture set
    #: `paid_at`; None when the order was not paid online (ADR-0024 A4).
    paid_via_charge_id: str | None = None
    pickup_code_verified_at: datetime | None = None
    lines: list[OrderLineItem] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class OrderIn(BaseModel):
    """`POST /orders`. Creates a `DRAFT`. `customer_id` names a `services/customer` contact by
    plain UUID (ADR-0000 §10 — no cross-schema FK); a walk-in or no-CRM-record order instead (or
    additionally) carries `customer_name`/`customer_phone` by hand. At least one of a customer id
    or a manual name/phone pair is required — an order for nobody is not a real order."""

    model_config = ConfigDict(extra="forbid")

    channel: OrderChannel
    warehouse_id: UUID
    customer_id: UUID | None = None
    customer_name: str = Field(default="", max_length=160)
    customer_phone: str = Field(default="", max_length=32)
    #: ISO-4217, uppercase letters only — the pattern `services/finance/app/schemas.py` and
    #: `services/notification/app/m1_schemas.py` already use for a currency field (mirrored here
    #: rather than inventing a new one); previously any 3-character string passed (review MAJOR A).
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "INR"
    lines: list[OrderLineIn] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _has_a_customer(self) -> Self:
        if self.customer_id is None and not self.customer_name and not self.customer_phone:
            raise ValueError("a customer_id, or a customer_name/customer_phone pair, is required")
        return self


class OrderVersionIn(BaseModel):
    """The optimistic-lock envelope every order transition takes (ADR-0025 §1): the caller's
    last-known `version`. A stale value is a 409, naming the order's current version."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)


class OrderHoldIn(OrderVersionIn):
    """`POST /orders/{id}/hold`. A reason is required — an operator hold, e.g. the customer
    asked to delay collection (ADR-0025 §1)."""

    reason: str = Field(min_length=1, max_length=500)


class OrderCancelIn(OrderVersionIn):
    """`POST /orders/{id}/cancel`. A reason is required, recorded on the `order_transitions` row
    and the cancellation event, the same as a purchase order's cancel."""

    reason: str = Field(min_length=1, max_length=500)


class OrderHandoverIn(OrderVersionIn):
    """`POST /orders/{id}/hand-over` — the counter/walk-in path through ADR-0025 §1's billing
    gate (`app.orders.services.check_billing_gate`; Fulfilment plan F4). For walk-in and counter
    handover the handover *is* delivery (ADR-0025 §1), so a successful call moves the order
    `READY -> HANDED_OVER -> DELIVERED` in one transaction.

    `pickup_code` is what the customer presents at the counter, checked against the code issued
    at READY when this org requires one (`fulfilment_settings.require_pickup_code`); omitted or
    wrong when one is required is exactly the "pickup code unverified" gate failure.
    `payment_confirmed` is the operator's own attestation that a cash/prepaid order's payment was
    collected at the counter — a documented stand-in for `services/payment`'s tenant collections
    module (ADR-0024 A4), not yet built; see this slice's own report. Both are inert for a
    credit-terms order or an org that requires no pickup code — the gate simply never asks for
    them."""

    #: MINOR 3 (security review): must match the exact shape `_generate_pickup_code` issues — 6
    #: ASCII digits, `secrets.choice("0123456789")` x 6 — not just a length bound. Without the
    #: `pattern`, a non-ASCII code reaches `secrets.compare_digest(pickup_code, order.pickup_code)`
    #: unfiltered, and that call raises `TypeError` (it refuses to compare `str` operands unless
    #: both are ASCII-only), turning a bad request into an unhandled 500.
    pickup_code: str | None = Field(default=None, max_length=20, pattern=r"^[0-9]{6}$")
    payment_confirmed: bool = False


class QuoteLineIn(BaseModel):
    """One line of `POST /orders/quote`. Same shape as `OrderLineIn` — a quote is what an order's
    lines would price to before any order exists."""

    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    quantity: Qty
    #: Same bound and reasoning as `OrderLineIn.selling_price_minor` (review MINOR D).
    selling_price_minor: int | None = Field(default=None, ge=0, le=MAX_ABS_MILLI)


class QuoteIn(BaseModel):
    """`POST /orders/quote`. Read-only — posts nothing, creates no order."""

    model_config = ConfigDict(extra="forbid")

    warehouse_id: UUID
    currency: str = Field(default="INR", min_length=3, max_length=3)
    lines: list[QuoteLineIn] = Field(min_length=1, max_length=200)


class QuoteLineOut(BaseModel):
    """One priced (or unpriceable) line of a quote preview, with its availability against
    `InventoryItem.available_milli` at the requested warehouse."""

    inventory_item_id: str
    sku: str
    quantity: Qty
    #: `False` when the item has no selling price and the line named none either — the same
    #: refusal `place` would give, surfaced here instead so a preview never 422s over it.
    priced: bool
    selling_price_minor: int | None
    line_total_minor: int | None
    available_qty: Qty
    sufficient: bool


class QuoteOut(BaseModel):
    """What `POST /orders/quote` answers with: a priced total (over the lines that could be
    priced) plus each line's availability. `fully_priced` is `False` when any line named no price
    and matched no item price either — `place` would refuse such an order outright."""

    warehouse_id: str
    currency: str
    subtotal_minor: int
    total_minor: int
    fully_priced: bool
    fully_available: bool
    lines: list[QuoteLineOut]


# ------------------------------------------------------------------ fulfilment (ADR-0024 §7,
# ADR-0025 §2/§3; docs/fulfilment/plan.md F3)

PickTaskStatus = Literal[
    "CREATED",
    "OFFERED",
    "ESCALATED",
    "ACCEPTED",
    "IN_PROGRESS",
    "EXCEPTION",
    "PICKED",
    "CLOSED",
    "CANCELLED",
]
OfferState = Literal["OFFERED", "ACCEPTED", "DECLINED", "EXPIRED", "WITHDRAWN", "RELEASED"]
PickExceptionReason = Literal["short_pick", "damaged", "not_found", "other"]
PresenceStatus = Literal["online", "offline"]
PackageStatus = Literal["OPEN", "PACKED"]


class WorkerOut(BaseModel):
    """Mirrors the SPA's `WorkerOut`. `zone_location_ids` is `null` unless the caller also holds
    `operations.fulfilment.worker.location_read` — a separate permission from the rest of the
    roster (`app.catalogue`), so it is progressive disclosure, not always-present data."""

    id: str
    employee_id: str
    warehouse_id: str
    display_name: str
    is_active: bool
    skills: list[str]
    presence_status: PresenceStatus
    presence_last_seen_at: datetime | None
    zone_location_ids: list[str] | None
    created_at: datetime
    updated_at: datetime


class WorkerIn(BaseModel):
    """`POST /fulfilment/workers`."""

    model_config = ConfigDict(extra="forbid")

    employee_id: UUID
    warehouse_id: UUID
    display_name: str = Field(default="", max_length=120)
    is_active: bool = True
    skills: list[str] = Field(default_factory=list, max_length=50)
    zone_location_ids: list[UUID] = Field(default_factory=list, max_length=200)


class WorkerPatch(BaseModel):
    """`PATCH /fulfilment/workers/{id}`. `skills`/`zone_location_ids`, when given, **replace** the
    whole set — the same full-replace shape simpler than a per-item add/remove pair, matching how
    few rows either set ever holds."""

    model_config = ConfigDict(extra="forbid")

    warehouse_id: UUID | None = None
    display_name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None
    skills: list[str] | None = Field(default=None, max_length=50)
    zone_location_ids: list[UUID] | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        reject_explicit_null(self, ("warehouse_id", "display_name", "is_active"))
        return self


class WorkerPresenceIn(BaseModel):
    """`POST /fulfilment/workers/{id}/presence`."""

    model_config = ConfigDict(extra="forbid")

    status: PresenceStatus


class AssignmentOfferOut(BaseModel):
    """Mirrors `assignment_offers` (ADR-0025 §3's exact row shape)."""

    id: str
    task_id: str
    worker_id: str
    round: int
    state: OfferState
    score_breakdown: dict[str, object]
    offered_at: datetime
    expires_at: datetime
    responded_at: datetime | None


class OfferIn(BaseModel):
    """`POST /fulfilment/tasks/{id}/offers` — a dispatcher offering a task to one worker. Also the
    manual-override path when auto-assignment already made a live, not-yet-accepted offer: a
    caller holding `operations.fulfilment.task.override` withdraws it and offers to whoever this
    call names instead; a caller without it gets the same 409 F3 has always given a second offer
    attempt."""

    model_config = ConfigDict(extra="forbid")

    worker_id: UUID


PickTaskLineStatus = Literal["pending", "partial", "picked", "short"]


class PickTaskLineOut(BaseModel):
    id: str
    order_line_id: str
    inventory_item_id: str
    sku: str
    source_location_id: str
    destination_location_id: str
    quantity: Qty
    picked: Qty
    short: Qty
    sequence: int | None
    status: PickTaskLineStatus
    #: Display fields (ADR-0024 §7 addendum). Resolved server-side inside operations
    #: and `null` unless the caller may see them: they hold the matching org-wide read
    #: (`inventory.item.read` / `inventory.location.read`) or this task is assigned to them
    #: (a live `OFFERED`/`ACCEPTED` offer to their own worker row). A client falls back to the
    #: ids when one is `null`.
    item_name: str | None = None
    item_unit: str | None = None
    source_location_code: str | None = None
    source_location_name: str | None = None
    destination_location_code: str | None = None


class PickExceptionOut(BaseModel):
    id: str
    task_id: str
    task_line_id: str
    reason: PickExceptionReason
    quantity: Qty
    note: str
    status: Literal["open", "resolved"]
    raised_at: datetime
    resolved_at: datetime | None


class PickTaskOut(BaseModel):
    """Mirrors the SPA's `PickTaskOut` — the dispatcher board's one row per task, with its lines,
    its whole offer history (ADR-0025 §3's "why did this take forty minutes") and any open
    exceptions."""

    id: str
    order_id: str
    #: The human order number (`ORD-…`), under the same visibility rule as the line display
    #: fields: `orders.order.read` at ORG, or this task is assigned to the caller. `null`
    #: otherwise.
    order_number: str | None = None
    warehouse_id: str
    zone_location_id: str | None
    status: PickTaskStatus
    round: int
    max_rounds: int
    lines: list[PickTaskLineOut]
    offers: list[AssignmentOfferOut]
    exceptions: list[PickExceptionOut]
    #: Set when auto-assignment ran and found no eligible worker (e.g. "No eligible worker online
    #: for zone DOCK-A") — `null` otherwise, including whenever a live offer exists (auto or
    #: manual). The dispatcher board's own visible reason to offer this task by hand.
    auto_assign_failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class CreatePickTasksIn(BaseModel):
    """`POST /fulfilment/tasks` — derives every pick task an accepted order needs, from its lines
    (ADR-0025 §1: `ACCEPTED -> IN_FULFILMENT` fires when the first one is created)."""

    model_config = ConfigDict(extra="forbid")

    order_id: UUID
    #: The order's own optimistic-lock version (ADR-0025 §1) — this call moves the order
    #: `ACCEPTED -> IN_FULFILMENT` in the same transaction as the tasks it creates.
    order_version: int = Field(ge=0)


class TaskReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScanIn(BaseModel):
    """`POST /fulfilment/tasks/{id}/scan` — one row in the append-only `scan_events` (plan F3).
    The task's own first scan additionally moves it `ACCEPTED -> IN_PROGRESS` (ADR-0025 §2)."""

    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=1, max_length=64)
    location_id: UUID | None = None
    inventory_item_id: UUID | None = None


class PickConfirmIn(BaseModel):
    """`POST /fulfilment/tasks/{id}/lines/{line_id}/pick`. `quantity` defaults to the line's whole
    remaining request; giving less records the rest as a `short_pick` exception (ADR-0025 §2's
    `IN_PROGRESS -> EXCEPTION`) rather than silently dropping it."""

    model_config = ConfigDict(extra="forbid")

    quantity: Qty | None = None
    #: Named only when `quantity` is less than the line's remaining request — the reason a
    #: `pick_exceptions` row is raised for the shortfall. Required together with a short quantity.
    reason: PickExceptionReason | None = None
    note: str = Field(default="", max_length=500)


class ExceptionResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=500)


class TaskCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class PackagingMaterialOut(BaseModel):
    id: str
    code: str
    name: str
    length_mm: int | None
    width_mm: int | None
    height_mm: int | None
    max_weight_grams: int | None
    tare_weight_grams: int | None
    is_active: bool


class PackagingMaterialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    length_mm: int | None = Field(default=None, ge=0)
    width_mm: int | None = Field(default=None, ge=0)
    height_mm: int | None = Field(default=None, ge=0)
    max_weight_grams: int | None = Field(default=None, ge=0)
    tare_weight_grams: int | None = Field(default=None, ge=0)
    is_active: bool = True


class PackageLineOut(BaseModel):
    id: str
    pick_task_line_id: str
    inventory_item_id: str
    sku: str
    quantity: Qty
    #: `null` unless the caller holds `inventory.item.read` at ORG or is assigned a pick task on
    #: this package's order (see `PickTaskLineOut`).
    item_name: str | None = None
    item_unit: str | None = None


class PackageLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pick_task_line_id: UUID
    quantity: Qty


class PackageOut(BaseModel):
    id: str
    order_id: str
    packaging_material_id: str | None
    destination_location_id: str
    status: PackageStatus
    weight_grams: int | None
    lines: list[PackageLineOut]
    created_at: datetime


class PackageIn(BaseModel):
    """`POST /fulfilment/packages` — one call opens the package, adds its lines and posts the
    staging -> pack/dock relocation, transferring each line's own destination reservation onward
    (ADR-0024 §6). There is no separate confirm step in F3 (`Package`'s own docstring)."""

    model_config = ConfigDict(extra="forbid")

    order_id: UUID
    packaging_material_id: UUID | None = None
    destination_location_id: UUID
    weight_grams: int | None = Field(default=None, ge=0)
    lines: list[PackageLineIn] = Field(min_length=1, max_length=200)


# ------------------------------------------------------------------ dispatch (ADR-0024 §6;
# `docs/fulfilment/plan.md` F5)

ShipmentStatus = Literal[
    "BOOKING",
    "BOOKING_FAILED",
    "PICKUP_HANDOVER",
    "IN_TRANSIT",
    "DELIVERED",
    "FAILED",
    "RETURNED",
]


class ShipmentOut(BaseModel):
    id: str
    order_id: str
    warehouse_id: str
    status: ShipmentStatus
    carrier_name: str
    tracking_reference: str | None
    #: The carrier connection it was booked through, if any, and the carrier's own shipment id.
    channel_id: str | None = None
    external_shipment_id: str | None = None
    failure_reason: str | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _reject_blank_or_control(value: str, *, field_name: str) -> str:
    """The codebase's existing blank-rejection convention (`services/subscription/app/schemas.py
    ::_reason_is_not_blank`), extended to also reject control characters — a `min_length` bound
    alone lets a whitespace-only or control-character string through, and this is free-text a
    warehouse floor or carrier desk types by hand (security review cheap-fix list)."""
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be blank")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in stripped):
        raise ValueError(f"{field_name} must not contain control characters")
    return stripped


class CarrierBookingIn(BaseModel):
    """Where and what to book when `POST /dispatch/shipments` ships through a carrier connection
    in `live` mode. The order carries no address, so the dispatcher supplies it here."""

    model_config = ConfigDict(extra="forbid")

    consignee_name: str = Field(min_length=1, max_length=120)
    consignee_phone: str = Field(pattern=r"^\+?\d{10,15}$")
    consignee_email: str | None = Field(default=None, max_length=120, pattern=r"^[^@\s]+@[^@\s]+$")
    consignee_address: str = Field(min_length=1, max_length=300)
    consignee_city: str = Field(min_length=1, max_length=80)
    consignee_state: str = Field(min_length=1, max_length=80)
    destination_pincode: str = Field(pattern=r"^\d{6}$")
    weight_grams: int = Field(ge=1, le=500_000)
    length_cm: int = Field(default=10, ge=1, le=200)
    breadth_cm: int = Field(default=10, ge=1, le=200)
    height_cm: int = Field(default=10, ge=1, le=200)
    #: Cash to collect on delivery, integer minor units; 0 is prepaid.
    cod_amount_minor: int = Field(default=0, ge=0)
    #: The carrier's own service id (from its serviceability list); omitted, the carrier picks.
    service_code: str | None = Field(default=None, pattern=r"^\d{1,10}$")

    @field_validator("consignee_name", "consignee_address", "consignee_city", "consignee_state")
    @classmethod
    def _text_is_clean(cls, value: str) -> str:
        return _reject_blank_or_control(value, field_name="value")


class ShipmentIn(BaseModel):
    """`POST /dispatch/shipments` — the carrier handover (Fulfilment plan F5): creates the
    shipment, already at `PICKUP_HANDOVER`, posts the dispatch `issue` move against whatever pick
    and pack transferred (ADR-0024 §6), and moves the order `READY -> HANDED_OVER -> DELIVERED`
    in the same call once `app.orders.services.check_billing_gate` passes — the delivery/courier
    counterpart to `POST /orders/{id}/hand-over`'s counter/walk-in path.

    `carrier_name`/`tracking_reference` are manual text only — no carrier API integration exists
    in this build (Fulfilment plan F5's own "not in phase" list); `tracking_reference` may be
    unknown at handover and set (or corrected) on a later transition instead.

    `payment_confirmed` mirrors `OrderHandoverIn`'s own field exactly — the carrier-desk
    operator's attestation that a cash/prepaid order's payment was collected, the same documented
    stand-in for `services/payment`'s tenant collections module (ADR-0024 A4) the counter path
    already uses (security review M3). Inert for a credit-terms order.
    """

    model_config = ConfigDict(extra="forbid")

    order_id: UUID
    #: The order's own optimistic-lock version (ADR-0025 §1) — this call moves the order, the
    #: same `order_version` field `CreatePickTasksIn` names for the identical reason.
    order_version: int = Field(ge=0)
    #: Free text for a manually-entered carrier; optional when `channel_id` names a connected
    #: carrier, whose name is then used.
    carrier_name: str | None = Field(default=None, min_length=1, max_length=120)
    #: A connected `carrier` channel to ship through (`GET /operations/channels?kind=carrier`).
    channel_id: UUID | None = None
    tracking_reference: str | None = Field(default=None, max_length=120)
    payment_confirmed: bool = False
    #: Required to book with a carrier in `live` mode; ignored in `log_only`.
    booking: CarrierBookingIn | None = None

    @field_validator("carrier_name")
    @classmethod
    def _carrier_name_is_not_blank(cls, value: str | None) -> str | None:
        return None if value is None else _reject_blank_or_control(value, field_name="carrier_name")

    @model_validator(mode="after")
    def _a_carrier_is_named(self) -> Self:
        if self.carrier_name is None and self.channel_id is None:
            raise ValueError("name a carrier: carrier_name or a connected carrier channel_id")
        return self


class ShipmentAdvanceIn(BaseModel):
    """`POST /dispatch/shipments/{id}/in-transit` and `.../deliver` — no reason needed for a
    forward-moving update. `tracking_reference` may be set or corrected here: a courier's own
    tracking number is often not known until pickup is scanned into their own system, after this
    shipment's own creation."""

    model_config = ConfigDict(extra="forbid")

    tracking_reference: str | None = Field(default=None, max_length=120)


class ShipmentRefusalIn(BaseModel):
    """`POST /dispatch/shipments/{id}/fail` and `.../return` — a reason is required, the same
    convention `OrderCancelIn`/`OrderHoldIn` set for any operator action that needs one."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_is_not_blank(cls, value: str) -> str:
        return _reject_blank_or_control(value, field_name="reason")


# ------------------------------------------------------------------ counting (ADR-0026;
# `docs/fulfilment/plan.md` F8)

CountScopeKind = Literal["WAREHOUSE", "ZONE", "LOCATIONS"]
CountSessionStatus = Literal["ACTIVE", "REVIEW", "CLOSED", "CANCELLED"]
CountLineReviewStatus = Literal["NONE", "PENDING", "APPROVED", "REJECTED"]
CountMethod = Literal["scan", "manual"]


class CountSessionOut(BaseModel):
    id: str
    warehouse_id: str
    scope_kind: CountScopeKind
    zone_location_id: str | None
    location_ids: list[str] | None
    status: CountSessionStatus
    label: str
    line_count: int
    counted_count: int
    pending_review_count: int
    created_at: datetime
    updated_at: datetime


class CountSessionIn(BaseModel):
    """`POST /counting/sessions` — opens a session and snapshots its lines from the current
    `inventory_balances` in scope (ADR-0026 §6). Refused (409) while the warehouse already has a
    session `ACTIVE` or under `REVIEW` (ADR-0026 §5)."""

    model_config = ConfigDict(extra="forbid")

    warehouse_id: UUID
    scope_kind: CountScopeKind
    #: Required (and only meaningful) when `scope_kind == "ZONE"` — every location at or below
    #: this one is in scope.
    zone_location_id: UUID | None = None
    #: Required (and only meaningful) when `scope_kind == "LOCATIONS"`.
    location_ids: list[UUID] | None = Field(default=None, max_length=500)
    label: str = Field(default="", max_length=200)


class CountSessionCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class CountLineOut(BaseModel):
    id: str
    session_id: str
    inventory_item_id: str
    sku: str
    location_id: str
    status: str
    lot_id: str | None
    roll_id: str | None
    is_unlisted: bool
    expected: Qty
    counted: Qty | None
    count_method: CountMethod | None
    scanned_barcode: str | None
    counted_by: str | None
    counted_at: datetime | None
    review_status: CountLineReviewStatus
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str
    posted_move_id: str | None
    created_at: datetime


class CountLineSubmitIn(BaseModel):
    """`POST /counting/sessions/{id}/lines/{line_id}/count` — a worker's physical reading for a
    line already on the session's open-time snapshot. A line may be counted once; a mistaken entry
    is corrected by a manager rejecting it (never a silent overwrite) — recounting a rejected line
    is a new session (ADR-0026 §4, "not in scope: a dedicated recount state")."""

    model_config = ConfigDict(extra="forbid")

    counted: Qty
    count_method: CountMethod
    scanned_barcode: str | None = Field(default=None, max_length=64)


class CountLineAddIn(BaseModel):
    """`POST /counting/sessions/{id}/lines` — a worker reporting stock at a bucket the session's
    snapshot did not expect anything at (`is_unlisted=True` on the created line). Refused if the
    bucket is already tracked by this session (use the count endpoint on that line instead) or is
    outside the session's own scope."""

    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    location_id: UUID
    status: str = "AVAILABLE"
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    counted: Qty
    count_method: CountMethod
    scanned_barcode: str | None = Field(default=None, max_length=64)


class CountLineReviewIn(BaseModel):
    """`POST /counting/sessions/{id}/lines/{line_id}/review` — a manager's decision on one
    `PENDING` discrepancy. Approving posts through `app.inventory.ledger.post_move` in the same
    call (ADR-0026 §3); rejecting never touches the ledger. A rejection needs a note — the same
    "a refusal names why" convention `PickConfirmIn`'s short-pick `reason` sets."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=500)


# ------------------------------------------------------------------ online payment (ADR-0024 A4;
# Fulfilment F10). Mirrors `services/payment`'s collection shapes — operations relays them, it
# holds no payment state of its own beyond the order's `paid_at` fact. No card, bank or UPI
# detail exists anywhere in these models: the customer pays in the provider's own hosted checkout.

CollectionState = Literal["created", "authorized", "captured", "failed", "cancelled", "expired"]


class CollectionRefundOut(BaseModel):
    id: str
    provider_refund_id: str | None
    amount_minor: int
    currency: str
    #: `created` (pending at the provider) | `processed` | `failed`.
    state: str
    reason: str | None
    created_at: datetime


class CollectionHistoryOut(BaseModel):
    from_state: str | None
    to_state: str
    #: `api` | `webhook` | `sync` | `sweep` — what moved it.
    source: str
    reason: str | None
    at: datetime


class OrderCollectionOut(BaseModel):
    """One online payment attempt for the order, as `services/payment` records it."""

    id: str
    state: CollectionState
    amount_minor: int
    currency: str
    provider: str
    provider_order_id: str | None
    provider_payment_id: str | None
    failure_reason: str | None
    refunded_minor: int
    refunds: list[CollectionRefundOut] = Field(default_factory=list)
    history: list[CollectionHistoryOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class OrderPaymentsOut(BaseModel):
    """`GET /orders/{id}/payments` — the order's paid fact, whether it can be paid online now (and
    if not, why), and every online payment attempt for it, newest first."""

    order_id: str
    total_minor: int
    currency: str
    paid_at: datetime | None
    paid_via_charge_id: str | None
    payment_term: str
    can_collect: bool
    collect_blocked_reason: str | None
    online_payments_configured: bool
    mode: Literal["test", "live"] | None
    #: `stub` when no Razorpay call leaves the platform — kept as it always was.
    wire: Literal["stub", "razorpay"]
    #: The connected account's provider (`razorpay` / `cashfree`), and whether a checkout with it
    #: can be paid for real (`live`) or only on the platform's stub (`stub`). None when no account
    #: is connected.
    provider: str | None = None
    provider_wire: Literal["stub", "live"] | None = None
    collections: list[OrderCollectionOut]


class CheckoutPrefillOut(BaseModel):
    name: str
    contact: str


class RazorpayOrderCheckoutSessionOut(BaseModel):
    """Exactly what the browser needs to open Razorpay's hosted Checkout — the **public** key id
    and the provider Order id — plus the prefill it may show. Never a secret."""

    provider: Literal["razorpay"]
    key_id: str
    provider_order_id: str
    amount_minor: int
    currency: str
    mode: Literal["test", "live"]
    openable: bool
    order_number: str
    prefill: CheckoutPrefillOut


class CashfreeOrderCheckoutSessionOut(BaseModel):
    """What the browser's `Cashfree({mode}).checkout({paymentSessionId})` needs (ADR-0033). The
    session id is fetched from Cashfree for this response by payment and never stored anywhere;
    reopening the checkout fetches it again. No credential, and no customer detail: the phone
    Cashfree requires went to it server to server."""

    provider: Literal["cashfree"]
    payment_session_id: str
    #: The SDK's own word for the environment.
    mode: Literal["sandbox", "production"]
    provider_order_id: str
    amount_minor: int
    currency: str
    openable: bool
    order_number: str


class RedirectOrderCheckoutSessionOut(BaseModel):
    """Where the browser goes to pay with a redirect-checkout provider (PhonePe, PayU, Paytm): a
    `GET` of
    `url` or a form `POST` of `fields` to it, opened in a new tab. Fetched from the provider by
    payment for this response and never stored; reopening asks again. No credential."""

    provider: Literal["phonepe", "payu", "paytm"]
    method: Literal["GET", "POST"]
    url: str
    fields: dict[str, str] = Field(default_factory=dict)
    provider_order_id: str
    amount_minor: int
    currency: str
    openable: bool
    order_number: str


#: The browser's handle, by provider.
OrderCheckoutSessionOut = Annotated[
    RazorpayOrderCheckoutSessionOut
    | CashfreeOrderCheckoutSessionOut
    | RedirectOrderCheckoutSessionOut,
    Field(discriminator="provider"),
]


class OrderCheckoutOut(BaseModel):
    collection: OrderCollectionOut
    #: None while payment is still creating the provider Order — retry in a moment.
    checkout: OrderCheckoutSessionOut | None


class OrderRefundIn(BaseModel):
    """`POST /orders/{id}/payments/{charge_id}/refunds`. The `Idempotency-Key` header is required:
    the same key is the same refund, here and at the provider."""

    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0, le=MAX_ABS_MILLI)
    reason: str = Field(min_length=1, max_length=200)


# ------------------------------------------------------------------ channels (Fulfilment plan F11)

ChannelProvider = Literal["mock_marketplace", "shopify", "shiprocket", "delhivery"]
ChannelStatus = Literal["connected", "disconnected", "error"]
ChannelSyncType = Literal["order_import", "inventory_push", "fulfilment_push", "health_check"]
ChannelSyncStatus = Literal["success", "failed"]


ChannelKind = Literal["sales", "erp", "pos", "carrier"]


class IntegrationWebhookAck(BaseModel):
    """The answer to a recorded (or already-recorded) webhook delivery."""

    event_id: str
    outcome: Literal["received", "ignored"]
    duplicate: bool


class ChannelOut(BaseModel):
    id: str
    #: Free text on read: a carrier, ERP or POS provider is not in `ChannelProvider` (what can be
    #: connected through `POST /operations/channels`) until its connector lands.
    provider: str
    kind: ChannelKind
    name: str
    status: ChannelStatus
    external_shop_id: str
    #: Never the credential itself — see `app.models.Channel`'s own docstring.
    api_key_masked: str
    default_warehouse_id: str | None
    default_currency: str
    auto_accept_orders: bool
    last_orders_synced_at: datetime | None
    last_inventory_synced_at: datetime | None
    last_health_check_at: datetime | None
    consecutive_failures: int
    last_error_code: str | None
    last_error_message: str | None
    connected_at: datetime | None
    disconnected_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChannelConnectIn(BaseModel):
    """`POST /operations/channels` — connects a new channel. Only `mock_marketplace` exists
    today (`app.domain.CHANNEL_PROVIDERS`) — this build's self-hosted sandbox, in place of the
    real Shopify/Amazon credentials this environment does not have."""

    model_config = ConfigDict(extra="forbid")

    provider: ChannelProvider
    #: What the connection is for; must be its provider's kind (`domain.CHANNEL_PROVIDER_KINDS`).
    kind: ChannelKind = "sales"
    name: str = Field(min_length=1, max_length=120)
    external_shop_id: str = Field(min_length=1, max_length=120)
    api_key: str = Field(min_length=1, max_length=500)
    #: The second half of a login (Shiprocket's password); carriers that need one only.
    api_secret: str | None = Field(default=None, min_length=1, max_length=200)
    #: Shopify only: the custom app's API secret key, which signs `X-Shopify-Hmac-Sha256`.
    #: Sealed with the access token; without it the channel polls but accepts no webhook.
    webhook_secret: str | None = Field(default=None, min_length=1, max_length=200)
    default_warehouse_id: UUID
    default_currency: str = Field(default="INR", min_length=3, max_length=3)
    auto_accept_orders: bool = True

    @field_validator("name", "external_shop_id", "api_key", "webhook_secret")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _reject_blank_or_control(value, field_name="value")


class ChannelConfigPatch(BaseModel):
    """`PATCH /operations/channels/{id}` — configuration only; the credential is rotated
    separately (`ChannelReconnectIn`) so a plain rename can never accidentally touch it."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_warehouse_id: UUID | None = None
    auto_accept_orders: bool | None = None


class ChannelReconnectIn(BaseModel):
    """`POST /operations/channels/{id}/reconnect` — rotates the credential and clears an `error`
    status. Also how a deliberately `disconnected` connection is brought back."""

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=500)
    api_secret: str | None = Field(default=None, min_length=1, max_length=200)
    #: Shopify only; omitting it on rotation drops the stored webhook secret.
    webhook_secret: str | None = Field(default=None, min_length=1, max_length=200)


class WebhookSetupOut(BaseModel):
    """What the customer registers at their carrier for tracking: the public path (the dashboard
    prefixes its own origin) and the token the carrier must present. The token is a secret."""

    path: str
    token: str


class ChannelSkuMappingOut(BaseModel):
    id: str
    channel_id: str
    external_sku: str
    inventory_item_id: str
    last_pushed_available_milli: int | None
    last_pushed_at: datetime | None
    last_push_status: Literal["ok", "failed"] | None
    last_push_error: str | None


class ChannelSkuMappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_sku: str = Field(min_length=1, max_length=120)
    inventory_item_id: UUID

    @field_validator("external_sku")
    @classmethod
    def _sku_not_blank(cls, value: str) -> str:
        return _reject_blank_or_control(value, field_name="external_sku")


class ChannelSyncLogOut(BaseModel):
    id: str
    channel_id: str
    sync_type: ChannelSyncType
    status: ChannelSyncStatus
    external_ref: str
    detail: dict[str, object] | None
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
    attempted_at: datetime


class ChannelSyncResultOut(BaseModel):
    """What a manual "sync now" or "retry" action returns — real counts from the sync that just
    ran, never a fixed success message (F11's own "no fake dashboard numbers" rule)."""

    attempted: int
    succeeded: int
    rejected: int


class ChannelRetryResultOut(BaseModel):
    succeeded: bool


class ChannelHealthCheckOut(BaseModel):
    healthy: bool
