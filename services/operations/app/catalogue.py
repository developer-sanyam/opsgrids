"""The Operations permission catalogue and role templates, for the `operations` product.

Mirrors account- and employee-service's catalogue, and for the same reason it is shaped the way
it is: in the system this platform replaces, an admin role was "every permission that exists", so
the moment another product registered its permissions the admin silently held them too.

The structure makes that impossible rather than merely discouraged:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's permission
  without declaring `cross_product`.

The guard is exercised by a test, so a wrong-product grant is a red build rather than a
super-grant discovered during an audit. A product's admin role is built from that product's own
catalogue, never "all permissions" (CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "operations"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    module: str
    resource: str
    action: str
    description: str
    default_scope: Scope = "OWN"
    max_scope: Scope = "ORG"
    is_dangerous: bool = False

    def code(self, product: str) -> str:
        return f"{product}.{self.module}.{self.resource}.{self.action}"


@dataclass(frozen=True, slots=True)
class Grant:
    code: str
    scope: Scope


@dataclass(frozen=True, slots=True)
class RoleTemplate:
    code: str
    name: str
    product: str
    grants: list[Grant]
    description: str = ""
    is_default: bool = False
    cross_product: bool = False


_RANK = {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}


def min_scope(wanted: Scope, ceiling: Scope) -> Scope:
    """Never grant wider than the permission's own ceiling."""
    return wanted if _RANK[wanted] <= _RANK[ceiling] else ceiling


class PermissionCatalogue:
    """One product's permissions. A role builder can only see its own."""

    def __init__(self, product: str, specs: list[PermissionSpec]) -> None:
        self.product = product
        self.specs = specs

    def codes(self) -> list[str]:
        return [s.code(self.product) for s in self.specs]

    def all(self, scope: Scope = "ORG") -> list[Grant]:
        """Every permission **in this catalogue**, at `scope` — the safe "give the admin
        everything", bound to one product by construction."""
        return [Grant(s.code(self.product), min_scope(scope, s.max_scope)) for s in self.specs]

    def pick(self, *pairs: tuple[str, Scope]) -> list[Grant]:
        """Named grants, validated against this catalogue so a typo fails loudly."""
        known = set(self.codes())
        out: list[Grant] = []
        for code, scope in pairs:
            full = code if code.startswith(f"{self.product}.") else f"{self.product}.{code}"
            if full not in known:
                raise ValueError(f"{full} is not in the {self.product} catalogue")
            out.append(Grant(full, scope))
        return out


# --------------------------------------------------------------------- operations catalogue

OPERATIONS = PermissionCatalogue(
    PRODUCT,
    [
        # Purchase orders. Reading has a meaningful OWN reading (the POs I raised), so it
        # defaults to OWN and tops out at ORG. Writing (raise, receive) has an OWN reading too —
        # a buyer works their own POs — so the write route sets no floor and the object-level
        # check does the rest. Receiving still needs the PO to already be `approved`
        # (`domain.PO_TRANSITIONS` has no `draft -> received` edge) — `write` alone gets a buyer
        # to a draft, never past the approval gate. Cancelling a draft is `write` too (nothing
        # committed, nothing to reverse); cancelling an *approved* PO additionally needs
        # `purchase_order.approve`, because it reverses spend an approver committed.
        PermissionSpec(
            "procurement", "purchase_order", "read", "See purchase orders and their lines", "OWN"
        ),
        PermissionSpec(
            "procurement",
            "purchase_order",
            "write",
            "Raise, edit and delete draft purchase orders; receive an approved one; close it; "
            "cancel a draft (cancelling an approved one also needs approve)",
            "OWN",
        ),
        # Approving is a commitment step distinct from raising/editing/receiving, and the
        # prerequisite gate before a PO can be received at all: a buyer holds
        # `purchase_order.write` at OWN so they can work their own POs, but approving must not
        # let that same buyer sign off on their own spend, or walk around the sign-off by
        # receiving straight from draft. No OWN reading, so every grant floors at ORG. The same
        # floor gates cancelling an already-approved PO, since that reverses the commitment.
        PermissionSpec(
            "procurement", "purchase_order", "approve", "Approve a purchase order", "ORG", "ORG"
        ),
        # Suppliers are org reference data — no per-person owner — so the natural reading is ORG.
        PermissionSpec(
            "procurement", "supplier", "read", "See the supplier directory", "ORG", "ORG"
        ),
        PermissionSpec(
            "procurement",
            "supplier",
            "write",
            "Create, edit and delete suppliers",
            "ORG",
            "ORG",
        ),
        # Inventory and warehouses are org infrastructure; ORG for everyone with a seat.
        PermissionSpec("inventory", "item", "read", "See inventory on hand", "ORG", "ORG"),
        PermissionSpec(
            "inventory", "item", "write", "Create, edit and delete inventory items", "ORG", "ORG"
        ),
        # Separate from `item.write`: editing the item card is not the same act as changing its
        # quantity, and a stock count needs its own audit trail either way.
        PermissionSpec(
            "inventory",
            "stock",
            "adjust",
            "Adjust or count an item's stock on hand",
            "ORG",
            "ORG",
        ),
        # Reconciliation (ADR-0014 §3): run the ledger-vs-balance check on demand, see the
        # discrepancies it records, and close one out with a note. Dangerous because resolving
        # is a sign-off that a stock record has been investigated — it moves no stock, but it
        # silences an integrity alarm. OPERATIONS_ADMIN gets it through `all()`; nobody else by
        # default.
        PermissionSpec(
            "inventory",
            "stock",
            "reconcile",
            "Check the stock ledger against balances and resolve discrepancies",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        PermissionSpec("inventory", "warehouse", "read", "See warehouses", "ORG", "ORG"),
        PermissionSpec(
            "inventory", "warehouse", "write", "Create, edit and delete warehouses", "ORG", "ORG"
        ),
        # Locations (ADR-0024 §5, Fulfilment plan F1): the zone/aisle/rack/shelf/bin/staging/
        # pack_station/dock/receiving/returns/quarantine tree inside a warehouse. Org
        # infrastructure like the warehouse itself, so ORG for everyone with a seat.
        PermissionSpec(
            "inventory",
            "location",
            "read",
            "See a warehouse's locations and their barcodes",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "inventory",
            "location",
            "write",
            "Create locations and edit their code, name, barcode and pick sequence",
            "ORG",
            "ORG",
        ),
        # Distinct from `location.write`: changing what a location *is* (its kind) or how much
        # it can hold (capacity) is a layout decision, not a labelling one, and once a location
        # already has ledger history behind it, changing either can silently misdescribe stock
        # that is already sitting there. Dangerous — a plain `location.write` grant (held by
        # `OPERATIONS_MANAGER`, not `OPERATIONS_USER`, which only reads) is never enough on its
        # own for a location with history; a location with none can still have its kind/capacity
        # set under plain `write`.
        PermissionSpec(
            "inventory",
            "layout",
            "publish",
            "Change kind or capacity on a location already in use",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        # Promising stock to a demand without moving it, and giving that promise back — the
        # kernel has carried this since Phase 0 (`ledger.reserve`/`release`); this is the first
        # permission that lets a caller reach it over HTTP (ADR-0014 §11's release endpoint).
        # ORG only: a reservation narrows what every other caller in the org can promise, so
        # there is no meaningful OWN reading.
        PermissionSpec(
            "inventory",
            "stock",
            "reserve",
            "Reserve stock for a demand, and release a reservation's open remainder",
            "ORG",
            "ORG",
        ),
        # Item master reference data: categories, typed attributes, variants, tracking flags,
        # HSN/tax fields and secondary UOM (ADR-0014 Phase 1). Reuses `inventory.item.read/write`
        # rather than a permission per sub-resource: these are all facets of the one item master
        # `inventory_items` already carries, not a separate object with its own audience.
    ],
)


OPERATIONS_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="OPERATIONS_ADMIN",
        name="Operations administrator",
        product=PRODUCT,
        description=(
            "Administers procurement and inventory across the organization. Built from the "
            "Operations catalogue alone — running operations is not the same job as owning the "
            "account."
        ),
        grants=OPERATIONS.all("ORG"),
    ),
    RoleTemplate(
        code="OPERATIONS_MANAGER",
        name="Procurement manager",
        product=PRODUCT,
        description=(
            "Raises, approves and receives purchase orders org-wide; maintains suppliers and "
            "inventory (including stock adjustments); reads warehouses. Does not create or "
            "rename warehouses — that stays with the admin."
        ),
        grants=OPERATIONS.pick(
            ("operations.procurement.purchase_order.read", "ORG"),
            ("operations.procurement.purchase_order.write", "ORG"),
            ("operations.procurement.purchase_order.approve", "ORG"),
            ("operations.procurement.supplier.read", "ORG"),
            ("operations.procurement.supplier.write", "ORG"),
            ("operations.inventory.item.read", "ORG"),
            ("operations.inventory.item.write", "ORG"),
            ("operations.inventory.stock.adjust", "ORG"),
            ("operations.inventory.stock.reserve", "ORG"),
            ("operations.inventory.warehouse.read", "ORG"),
            ("operations.inventory.location.read", "ORG"),
            ("operations.inventory.location.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="OPERATIONS_USER",
        name="Operations user",
        product=PRODUCT,
        description=(
            "Every person with an Operations seat. Raises and sees their own purchase orders, and "
            "reads the shared suppliers, inventory, warehouses and locations."
        ),
        is_default=True,
        grants=OPERATIONS.pick(
            ("operations.procurement.purchase_order.read", "OWN"),
            ("operations.procurement.purchase_order.write", "OWN"),
            ("operations.procurement.supplier.read", "ORG"),
            ("operations.inventory.item.read", "ORG"),
            ("operations.inventory.warehouse.read", "ORG"),
            ("operations.inventory.location.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather than a
    customer's operations administrator quietly holding every CRM permission.
    """
    for template in templates:
        if template.cross_product:
            continue
        for grant in template.grants:
            product = grant.code.split(".", 1)[0]
            if product != template.product:
                raise ValueError(
                    f"Role template {template.code!r} belongs to product "
                    f"{template.product!r} but grants {grant.code!r}. "
                    "Set cross_product=True deliberately, or remove the grant."
                )


validate(OPERATIONS_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


OPERATIONS_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=OPERATIONS, role_templates=OPERATIONS_ROLE_TEMPLATES
)
