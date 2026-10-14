"""The finance permission catalogue and role templates, for the `finance` product.

Mirrors account- and employee-service's catalogue, and for the same reason: a product's admin
role is built from *that product's own catalogue*, never "every permission that exists". The
structure makes the wrong thing impossible rather than merely discouraged —

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import if a template holds a foreign product's permission without
  declaring `cross_product`.

The construct is copied here rather than imported from another service on purpose: a product
declares its own vocabulary, and importing another service's `catalogue` would couple this
service across the boundary the platform exists to keep clean.

Every finance read is an **organization-level** function: an AR clerk sees the whole org's
receivable book, not "their own" invoices. So every permission here defaults to ORG and tops out
at ORG, and the routes floor at ORG — there is no OWN reading of a company's books.
"""

from __future__ import annotations

from dataclasses import dataclass

from platform_core.catalogue import (
    PermissionCatalogue,
    ProductRegistration,
    RoleTemplate,
    Scope,
    validate,
)
from platform_core.catalogue import PermissionSpec as _BasePermissionSpec

PRODUCT = "finance"


@dataclass(frozen=True, slots=True)
class PermissionSpec(_BasePermissionSpec):
    """Finance permissions are organisation-wide by nature, so the default scope is ORG."""

    default_scope: Scope = "ORG"


# --------------------------------------------------------------------- finance catalogue

FINANCE = PermissionCatalogue(
    PRODUCT,
    [
        PermissionSpec("billing", "invoice", "read", "See invoices and credit notes"),
        # Issuing a document is dangerous in the platform's sense: a B2B/export/SEZ document
        # stages an IRP filing on the outbox in the same transaction as the write, so granting
        # this hands somebody the ability to make a real filing request, not just add a row.
        PermissionSpec(
            "billing",
            "invoice",
            "issue",
            "Issue an invoice or a credit note against one",
            is_dangerous=True,
        ),
        PermissionSpec("billing", "payment", "read", "See payments received"),
        PermissionSpec(
            "billing", "payment", "record", "Record a payment received and allocate it to invoices"
        ),
        PermissionSpec("billing", "customer", "read", "See customers and their receivables"),
        PermissionSpec("billing", "customer", "write", "Add, edit or archive a customer"),
        # Purchases are the inbound half of the book: what a supplier billed this organization.
        # Read/write rather than read/record because there is no lifecycle here to name — see
        # `finance.md`: recording is the only write, and there is no update or delete path.
        PermissionSpec("billing", "purchase", "read", "See recorded purchase documents"),
        PermissionSpec("billing", "purchase", "write", "Record what a supplier billed"),
        PermissionSpec("reports", "summary", "read", "See the finance overview / KPIs"),
        PermissionSpec("reports", "ledger", "read", "See the ledger / trial balance"),
        # Compliance. Reading a filing history or a supplier's claim is an ordinary finance read.
        # The two writes are not, and both are marked dangerous for the same reason: each one
        # decides money. Changing the GST registration decides which identity every future invoice
        # is filed under; deciding an IMS document decides whether the input tax credit on
        # somebody else's invoice is claimed.
        PermissionSpec("compliance", "einvoice", "read", "See e-invoice filing status and history"),
        PermissionSpec(
            "compliance", "registration", "read", "See the organization's GST registration"
        ),
        PermissionSpec(
            "compliance",
            "registration",
            "write",
            "Set the organization's GST registration",
            is_dangerous=True,
        ),
        PermissionSpec(
            "compliance", "ims", "read", "See the IMS reconciliation queue and its suggestions"
        ),
        PermissionSpec(
            "compliance",
            "ims",
            "decide",
            "Accept, reject or park an inbound supplier document",
            is_dangerous=True,
        ),
        # Returns. Three permissions rather than two, because assembling a period and *filing* it
        # are different acts. Generating states what this platform believes about its own book
        # and can be repeated; filing makes a claim to a government and is terminal — which is
        # why only the second is dangerous, and why a finance analyst can reasonably hold the
        # first without the second.
        PermissionSpec("compliance", "returns", "read", "See generated and filed GST returns"),
        PermissionSpec(
            "compliance",
            "returns",
            "generate",
            "Assemble a GST return from the invoices recorded for a period",
        ),
        PermissionSpec(
            "compliance",
            "returns",
            "file",
            "Mark a generated GST return as filed with the portal",
            is_dangerous=True,
        ),
        # e-Way bills are read here and raised over the internal API by a dispatch system, so
        # there is one permission rather than a read/write pair: what a person does with this is
        # answer "may that lorry leave".
        PermissionSpec("compliance", "eway", "read", "See e-Way bills and their validity"),
        PermissionSpec(
            "compliance", "withholding", "read", "See TDS deducted and TCS collected per document"
        ),
        # The ITC ledger has no write permission of its own: every posting is a consequence of an
        # IMS decision or a GSTR-3B filing, each already behind its own dangerous permission, so a
        # third grant here would only ever be held alongside one of those two.
        PermissionSpec(
            "compliance", "itc", "read", "See the input-tax-credit ledger and its balance"
        ),
    ],
)


FINANCE_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="FINANCE_ADMIN",
        name="Finance administrator",
        product=PRODUCT,
        description=(
            "Runs the receivable book for the organization: every invoice, payment, customer "
            "position and report, at ORG. Deliberately built from the finance catalogue alone."
        ),
        grants=FINANCE.all("ORG"),
    ),
    RoleTemplate(
        code="FINANCE_VIEWER",
        name="Finance viewer",
        product=PRODUCT,
        description="Read-only access to the finance book. The default finance seat.",
        is_default=True,
        grants=FINANCE.pick(
            ("finance.billing.invoice.read", "ORG"),
            ("finance.billing.payment.read", "ORG"),
            ("finance.billing.customer.read", "ORG"),
            ("finance.reports.summary.read", "ORG"),
            ("finance.reports.ledger.read", "ORG"),
            # A viewer sees whether a document is filed; they cannot change who it is filed as.
            ("finance.compliance.einvoice.read", "ORG"),
            ("finance.compliance.registration.read", "ORG"),
            # And they see the supplier queue — a reconciliation is worked by looking before it is
            # worked by deciding, and the two are separate seats on purpose.
            ("finance.compliance.ims.read", "ORG"),
            # The compliance reads a viewer needs to answer the everyday questions: what was
            # reported for August, is that lorry's bill still valid, and how much has already
            # been deducted from what we are owed. None of the three changes anything.
            ("finance.compliance.returns.read", "ORG"),
            ("finance.compliance.eway.read", "ORG"),
            ("finance.compliance.withholding.read", "ORG"),
            ("finance.compliance.itc.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(FINANCE_ROLE_TEMPLATES)


FINANCE_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=FINANCE, role_templates=FINANCE_ROLE_TEMPLATES
)
