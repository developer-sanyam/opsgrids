"""Configuration for the billing service."""

from __future__ import annotations

import re

from pydantic import field_validator, model_validator

from platform_core import gst
from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "billing"
    db_schema: str = "billing"
    #: Its own NOBYPASSRLS role, granted DML on the `billing` schema and nothing else. Billing
    #: is a tenant service — `invoices`, `invoice_lines`, `credit_ledger`, `audit_logs` and
    #: `outbox` all carry a tenant policy — so the API must connect as a role row-level security
    #: can bind (ADR-0002 finding 2). Append-only and freeze are enforced *below* this role with
    #: table privileges and a trigger, not by trusting the application to behave.
    db_app_role: str | None = "opsgrids_billing"
    db_admin_role: str | None = "opsgrids_admin"
    #: The outbox relay's role: BYPASSRLS, SELECT+UPDATE on `outbox` only. Kept from the
    #: scaffold — billing emits `commerce.invoice.*` and `commerce.credit.*`.
    db_outbox_role: str | None = "opsgrids_outbox"
    #: BYPASSRLS but SELECT-only, on `credit_ledger` alone. The staff credit-ledger route
    #: (`GET /admin/credit-ledger`) reads across every organization at once — there is no single
    #: tenant to bind — so it cannot run on the application role, which reads nothing without a
    #: tenant GUC. Its own role rather than the admin one keeps a bug in that read path from
    #: writing anything (ADR-0002 §2, mirrors console's `opsgrids_console_directory`).
    db_directory_role: str | None = "opsgrids_billing_directory"

    # --- the billing run (ADR-0005 addendum, 2026-09-23) -----------------------------------
    #: Where catalog answers the plan-version read. The billing run prices a subscription period
    #: off the **pinned** version's own prices, read here — never off a price the caller sent.
    catalog_service_url: str = "http://localhost:8164"
    #: Where metering answers the per-period usage read, for a version that prices a meter.
    metering_service_url: str = "http://localhost:8162"

    #: Which `tax_rates` row applies to a billing-run invoice. Jurisdiction resolution per
    #: customer is still open (ADR-0004 "Open: Tax"), so until it is settled this is one
    #: deployment-wide choice: a `code` (`gst`) and optional `jurisdiction` (`IN`). Unset means
    #: **no tax line** — the honest state for a deployment that has not registered for tax. Set,
    #: the rate in force on the period's start date is applied, and a period with no rate in force
    #: is refused rather than invoiced untaxed.
    billing_tax_code: str | None = None
    billing_tax_jurisdiction: str | None = None

    # --- the GST supplier (ADR-0004 Q1 decision 1) -----------------------------------------
    #: The legal entity operating the deployment, as it appears on a GST tax invoice. All empty by
    #: default and **inert**: nothing reads them yet (finalize still issues commercial invoices),
    #: so they are only checked for shape when set, never required.
    billing_supplier_gstin: str | None = None
    billing_supplier_legal_name: str | None = None
    billing_supplier_state_code: str | None = None
    #: The default SAC (services accounting code) for catalog products that carry none.
    billing_default_sac: str | None = None

    @field_validator("billing_supplier_gstin")
    @classmethod
    def _gstin_has_a_valid_check_digit(cls, v: str | None) -> str | None:
        return None if v is None else gst.parse_gstin(v).value

    @field_validator("billing_supplier_state_code")
    @classmethod
    def _state_code_is_a_gst_state(cls, v: str | None) -> str | None:
        if v is not None and v not in gst.GST_STATE_CODES:
            raise ValueError("BILLING_SUPPLIER_STATE_CODE is not a GST state code")
        return v

    @field_validator("billing_default_sac")
    @classmethod
    def _sac_is_six_digits_in_chapter_99(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"99\d{4}", v):
            raise ValueError("BILLING_DEFAULT_SAC is a six-digit SAC starting 99")
        return v

    @model_validator(mode="after")
    def _state_code_matches_the_gstin(self) -> Settings:
        gstin, state = self.billing_supplier_gstin, self.billing_supplier_state_code
        if gstin and state and gstin[:2] != state:
            raise ValueError(
                "BILLING_SUPPLIER_STATE_CODE differs from BILLING_SUPPLIER_GSTIN's state"
            )
        return self


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
