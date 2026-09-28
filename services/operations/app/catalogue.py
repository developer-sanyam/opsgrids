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
        #
        # Orders (ADR-0024 §1, ADR-0025; Fulfilment plan F2). Unlike a purchase order — a buyer's
        # own document, with a meaningful OWN reading (`raised_by_id`) — a customer order is a
        # shared operational queue: whoever is at the counter or on the phone has to see and work
        # every order to fulfil it, not only the one they personally took. So, like suppliers,
        # inventory and warehouses, every order permission is ORG-only (`default_scope=max_scope
        # ="ORG"`; no OWN reading is offered at all, matching `procurement.supplier.read/write`'s
        # own reasoning). `read` also covers the quote preview (`POST /orders/quote`): it posts
        # nothing and needs no stronger permission than seeing an order would.
        PermissionSpec(
            "orders", "order", "read", "See orders, their lines, and price a quote", "ORG", "ORG"
        ),
        PermissionSpec("orders", "order", "create", "Create a draft order", "ORG", "ORG"),
        # Places a draft (prices its lines) and moves a hold/release — the ordinary counter-staff
        # work of running an order up to the point stock is actually promised.
        PermissionSpec(
            "orders",
            "order",
            "write",
            "Place a draft order, and hold/release an accepted one",
            "ORG",
            "ORG",
        ),
        # Accepting is where the business commits stock (ADR-0025 §1's guard: a credit check, an
        # all-or-nothing multi-line reservation) — a bigger commitment than raising or placing an
        # order, so it is its own permission, the same separation `purchase_order.approve` draws
        # from `purchase_order.write`. `OPERATIONS_USER` (the counter/phone role) does not hold
        # it by default; `OPERATIONS_MANAGER` and `OPERATIONS_ADMIN` do.
        PermissionSpec(
            "orders", "order", "accept", "Accept a placed order, reserving its stock", "ORG", "ORG"
        ),
        # Cancelling releases whatever stock `accept` already promised and starts finance's and
        # payment's own compensation (ADR-0025 §1's cancellation fan-out) — kept apart from
        # `order.write` for the same reason PO's `approve` is separate from its `write`: undoing
        # a commitment is not the same act as making the ordinary ones.
        PermissionSpec(
            "orders",
            "order",
            "cancel",
            "Cancel an order and release its reservations",
            "ORG",
            "ORG",
        ),
        # Fulfilment (ADR-0024 §7, ADR-0025 §2/§3; Fulfilment plan F3). Tasks and workers are a
        # shared operational queue like orders — a dispatcher has to see and offer every task, so
        # `task.read`/`task.assign` default and top out at ORG, the same reasoning
        # `orders.order.read` already documents. `task.execute` is the one exception: it is a
        # worker acting on *their own* offered/accepted task, never any worker's, so its ceiling
        # is `OWN` — even `OPERATIONS_ADMIN`'s `all("ORG")` grant is clamped down to `OWN` by
        # `min_scope`, which is deliberate (CLAUDE.md: holding a permission is not holding scope;
        # an admin executing someone else's pick is `task.override`'s job, not a wider grant
        # here).
        PermissionSpec(
            "fulfilment",
            "task",
            "read",
            "See pick tasks, their lines and offer history",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "fulfilment",
            "task",
            "execute",
            "Accept/decline your own offer, scan, confirm a pick line, raise or resolve an "
            "exception — always on a task currently offered or accepted to you",
            "OWN",
            "OWN",
        ),
        PermissionSpec(
            "fulfilment",
            "task",
            "assign",
            "Create an accepted order's pick tasks, offer or reassign one to a worker, cancel a "
            "task nobody has started yet",
            "ORG",
            "ORG",
        ),
        # A stronger permission on edges that ARE in `app.domain.PICK_TASK_TRANSITIONS`, never a
        # way around the map (ADR-0025 §2): cancelling a task a worker already has in hand
        # (`ACCEPTED`/`IN_PROGRESS`/`EXCEPTION`), and manually closing a `PICKED` task — the
        # automatic close-on-handover path is a later phase's addition, so F3 has no other door
        # into `CLOSED`.
        PermissionSpec(
            "fulfilment",
            "task",
            "override",
            "Cancel a task a worker already has in hand, or force-close a picked one",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        # Worker directory. The plan (`docs/fulfilment/plan.md` §4) names `read`/`location_read`
        # only; `write` is this build's own addition — the dispatcher's roster screen has to
        # create and edit worker rows somehow, and there is no permission-free write path in this
        # codebase. Scoped `OWN`-default so a worker can hold it just to set their *own*
        # presence (`app.fulfilment.services.set_presence`, gated by `ScopeContext
        # .assert_can_touch` against `employee_id`), while a dispatcher role is granted it at ORG
        # for the rest of the roster (create, warehouse reassignment, skills, zone access).
        PermissionSpec(
            "fulfilment",
            "worker",
            "read",
            "See the worker roster and presence",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "fulfilment",
            "worker",
            "write",
            "Create and edit worker records, and set your own presence",
            "OWN",
            "ORG",
        ),
        # Separate from `worker.read`: which zones a worker may be offered tasks in is read here,
        # progressive disclosure on top of the ordinary roster fields (`WorkerOut
        # .zone_location_ids` is `null` without it).
        PermissionSpec(
            "fulfilment",
            "worker",
            "location_read",
            "See which zones a worker may be offered tasks in",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "fulfilment",
            "pack",
            "read",
            "See packages, their lines and packaging materials",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "fulfilment",
            "pack",
            "execute",
            "Confirm a package (posts the staging -> pack/dock relocation) and maintain "
            "packaging materials",
            "ORG",
            "ORG",
        ),
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
            "inventory (including stock adjustments); reads warehouses. Accepts and cancels "
            "customer orders — the stock-commitment and stock-release steps — on top of the "
            "ordinary counter work every Operations user does. Does not create or rename "
            "warehouses — that stays with the admin."
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
            ("operations.orders.order.read", "ORG"),
            ("operations.orders.order.create", "ORG"),
            ("operations.orders.order.write", "ORG"),
            ("operations.orders.order.accept", "ORG"),
            ("operations.orders.order.cancel", "ORG"),
            ("operations.fulfilment.task.read", "ORG"),
            ("operations.fulfilment.task.assign", "ORG"),
            ("operations.fulfilment.task.override", "ORG"),
            ("operations.fulfilment.worker.read", "ORG"),
            ("operations.fulfilment.worker.write", "ORG"),
            ("operations.fulfilment.worker.location_read", "ORG"),
            ("operations.fulfilment.pack.read", "ORG"),
            ("operations.fulfilment.pack.execute", "ORG"),
        ),
    ),
    RoleTemplate(
        code="OPERATIONS_WORKER",
        name="Warehouse worker",
        product=PRODUCT,
        description=(
            "The picker/packer role the worker PWA authenticates as (plan F3). Sees the task "
            "board and the worker roster read-only, executes only the tasks offered or accepted "
            "to them (`task.execute` tops out at OWN — even this role cannot execute another "
            "worker's task), sets their own presence, and confirms packages. Does not offer, "
            "reassign or override a task — that stays with the dispatcher (`OPERATIONS_MANAGER`)."
        ),
        grants=OPERATIONS.pick(
            ("operations.fulfilment.task.read", "ORG"),
            ("operations.fulfilment.task.execute", "OWN"),
            ("operations.fulfilment.worker.read", "ORG"),
            ("operations.fulfilment.worker.write", "OWN"),
            ("operations.fulfilment.pack.read", "ORG"),
            ("operations.fulfilment.pack.execute", "ORG"),
        ),
    ),
    RoleTemplate(
        code="OPERATIONS_USER",
        name="Operations user",
        product=PRODUCT,
        description=(
            "Every person with an Operations seat. Raises and sees their own purchase orders, "
            "reads the shared suppliers, inventory, warehouses and locations, and runs the "
            "counter: creates and places customer orders, holds and releases them. Does not "
            "accept (commit stock to) or cancel an order — those stay with the manager, the same "
            "split `purchase_order.approve` draws from `purchase_order.write`."
        ),
        is_default=True,
        grants=OPERATIONS.pick(
            ("operations.procurement.purchase_order.read", "OWN"),
            ("operations.procurement.purchase_order.write", "OWN"),
            ("operations.procurement.supplier.read", "ORG"),
            ("operations.inventory.item.read", "ORG"),
            ("operations.inventory.warehouse.read", "ORG"),
            ("operations.inventory.location.read", "ORG"),
            ("operations.orders.order.read", "ORG"),
            ("operations.orders.order.create", "ORG"),
            ("operations.orders.order.write", "ORG"),
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
