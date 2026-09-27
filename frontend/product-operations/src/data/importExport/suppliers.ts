/**
 * Suppliers, for the Import / Export button on the Suppliers screen (ADR-0022).
 *
 * One row is one `POST /operations/suppliers`. Spend, open purchase orders and on-time delivery are
 * rollups the service maintains and never accepts, so they are read-only columns: exported, ignored
 * on import.
 *
 * An update is one `PATCH /operations/suppliers/{id}` carrying only the fields that differ. The
 * endpoint has no null guard, so the only empty value it is sent is `""` on the three text fields
 * that take it; `since` cannot be emptied at all (a null there is a 500). `code` and `currency`
 * are refused by the endpoint (a 422), so they are immutable fields that only ever compare.
 */
import { collectPages, defineImportEntity, minorToMajor } from "@opsgrids/client";

import { createSupplier, listSuppliers, updateSupplier } from "@/api/client";
import type { ListSuppliersParams } from "@/api/client";
import type { SupplierIn, SupplierOut, SupplierPatch, SupplierStatus } from "@/api/types";
import {
  CATEGORY_OPTIONS,
  CURRENCY_HINT,
  CURRENCY_PATTERN,
  optionLabel,
  optionalNumber,
  optionalOption,
  optionalText,
  requiredOption,
  requiredText,
  sameCode,
  sameEmail,
  textOrNull,
} from "./shared";

/** What the Suppliers screen filters by: the export is the whole filtered list, not the visible page. */
export type SupplierFilters = Omit<ListSuppliersParams, "page" | "size" | "signal">;

const STATUS_OPTIONS: readonly { value: SupplierStatus; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "on_hold", label: "On hold" },
  { value: "pending", label: "Pending" },
];

export const supplierImport = defineImportEntity<SupplierIn, SupplierOut, SupplierFilters>({
  id: "operations.suppliers",
  noun: "suppliers",
  nounSingular: "supplier",
  fileStem: "suppliers",
  recordIdKey: "id",

  columns: [
    {
      key: "code",
      header: "Supplier code",
      kind: "text",
      required: true,
      help: "The supplier's unique code. It cannot be changed once the supplier exists.",
      maxLength: 40,
      pattern: /^[A-Za-z0-9_-]+$/,
      patternHint: "Letters, digits, _ -",
    },
    {
      key: "name",
      header: "Supplier name",
      kind: "text",
      required: true,
      help: "The supplier's trading name. Renaming an existing supplier renames it on every purchase order raised to it.",
      maxLength: 200,
    },
    {
      key: "category",
      header: "Category",
      kind: "enum",
      required: true,
      help: "The procurement category the supplier's spend rolls up to.",
      options: CATEGORY_OPTIONS,
    },
    {
      key: "status",
      header: "Status",
      kind: "enum",
      help: "Whether new purchase orders can go to this supplier. Blank means Active.",
      options: STATUS_OPTIONS,
    },
    {
      key: "lead_time_days",
      header: "Lead time (days)",
      kind: "integer",
      help: "Usual days from order to delivery, from 0 to 365. Blank means 0.",
      min: 0,
      max: 365,
    },
    {
      key: "currency",
      header: "Currency",
      kind: "text",
      help: "The currency purchase orders to this supplier are raised in. Blank means INR on a new supplier. An existing supplier keeps its own currency, which cannot be changed.",
      maxLength: 3,
      pattern: CURRENCY_PATTERN,
      patternHint: CURRENCY_HINT,
    },
    {
      key: "contact_name",
      header: "Contact name",
      kind: "text",
      help: "Who to speak to at the supplier.",
      maxLength: 120,
    },
    {
      key: "contact_email",
      header: "Contact email",
      kind: "email",
      help: "The contact's email address.",
      maxLength: 200,
    },
    {
      key: "location",
      header: "Location",
      kind: "text",
      help: "Where the supplier is based, for example a city.",
      maxLength: 160,
    },
    {
      key: "since",
      header: "Supplier since",
      kind: "date",
      help: "When you started buying from them, as a date. Blank means today.",
    },
    {
      key: "spend",
      header: "Lifetime spend",
      kind: "money",
      help: "Everything spent with the supplier, worked out by the app from purchase orders.",
      currencyKey: "currency",
      readOnly: true,
    },
    {
      key: "open_pos",
      header: "Open purchase orders",
      kind: "integer",
      help: "Purchase orders still in flight, worked out by the app.",
      readOnly: true,
    },
    {
      key: "on_time_pct",
      header: "On-time delivery (%)",
      kind: "decimal",
      help: "Share of deliveries that arrived on time, worked out by the app.",
      readOnly: true,
    },
    {
      key: "id",
      header: "Record ID",
      kind: "text",
      help: "The supplier's identifier in OpsGrids.",
      readOnly: true,
    },
  ],

  sampleRows: [
    {
      code: "SUP-MER",
      name: "Meridian Metals",
      category: "Raw materials",
      status: "Active",
      lead_time_days: 12,
      currency: "INR",
      contact_name: "Rajesh Pillai",
      contact_email: "rajesh@meridianmetals.example",
      location: "Jamshedpur",
      since: "2019-04-02",
    },
    {
      code: "SUP-APX",
      name: "Apex Packaging",
      category: "Packaging",
      status: "Active",
      lead_time_days: 6,
      currency: "INR",
      contact_name: "Farhan Qureshi",
      contact_email: "farhan@apexpack.example",
      location: "Vapi",
      since: "2021-01-19",
    },
    {
      code: "SUP-CRL",
      name: "Crestline Logistics",
      category: "Logistics",
      status: "Pending",
      lead_time_days: 3,
      currency: "INR",
      contact_name: "Vikram Sethi",
      contact_email: "vikram@crestlinelog.example",
      location: "Mumbai",
      since: "2024-11-08",
    },
  ],

  notes: [
    "A supplier code that already exists is skipped, not changed, unless you choose Add new and update existing.",
    "Only the code is checked for duplicates. Two rows with the same name and different codes create two suppliers.",
    "On a new supplier, Status defaults to Active, lead time to 0 days and Supplier since to today when left blank.",
    "The Lifetime spend, Open purchase orders and On-time delivery columns are worked out by the app and ignored on import.",
    "Updating: Supplier code and Currency cannot be changed, and a cell that disagrees with the supplier is refused. A code typed in other capitals still finds its supplier.",
    "Updating: Contact name, Contact email and Location can be emptied with [clear]. Supplier since and every other column cannot.",
    "Updating: renaming a supplier also renames it, without notice, on every purchase order raised to it.",
  ],

  toPayload(row) {
    const status = optionalOption(row, "status", "Status", STATUS_OPTIONS);
    const leadTimeDays = optionalNumber(row, "lead_time_days");
    const currency = optionalText(row, "currency");
    const contactName = optionalText(row, "contact_name");
    const contactEmail = optionalText(row, "contact_email");
    const location = optionalText(row, "location");
    const since = optionalText(row, "since");

    return {
      code: requiredText(row, "code", "Supplier code"),
      name: requiredText(row, "name", "Supplier name"),
      category: requiredOption(row, "category", "Category", CATEGORY_OPTIONS),
      ...(status === null ? {} : { status }),
      ...(leadTimeDays === null ? {} : { lead_time_days: leadTimeDays }),
      ...(currency === null ? {} : { currency: currency.toUpperCase() }),
      ...(contactName === null ? {} : { contact_name: contactName }),
      ...(contactEmail === null ? {} : { contact_email: contactEmail }),
      ...(location === null ? {} : { location }),
      ...(since === null ? {} : { since }),
    };
  },

  async create(payload) {
    const created = await createSupplier(payload);
    return { id: created.id };
  },

  export: {
    fetchPage: ({ page, size, filters, signal }) => listSuppliers({ ...filters, page, size, signal }),

    toRow: (supplier) => ({
      code: supplier.code,
      name: supplier.name,
      category: optionLabel(CATEGORY_OPTIONS, supplier.category),
      status: optionLabel(STATUS_OPTIONS, supplier.status),
      lead_time_days: supplier.lead_time_days,
      currency: supplier.currency,
      contact_name: supplier.contact_name === "" ? null : supplier.contact_name,
      contact_email: supplier.contact_email === "" ? null : supplier.contact_email,
      location: supplier.location === "" ? null : supplier.location,
      since: supplier.since,
      spend: minorToMajor(supplier.spend_minor, supplier.currency),
      open_pos: supplier.open_pos,
      on_time_pct: supplier.on_time_pct,
      id: supplier.id,
    }),
  },

  update: {
    loadExisting: (ctx) =>
      collectPages((page, size) => listSuppliers({ page, size, signal: ctx.signal }), {
        signal: ctx.signal,
        key: (supplier) => supplier.id,
      }),
    idOf: (supplier) => supplier.id,
    keyColumn: "code",
    keyOf: (supplier) => supplier.code,
    currencyOf: (supplier) => supplier.currency,

    fields: [
      { column: "code", current: (supplier) => supplier.code, same: sameCode, immutable: true },
      { column: "name", current: (supplier) => supplier.name },
      { column: "category", current: (supplier) => supplier.category },
      { column: "status", current: (supplier) => supplier.status },
      { column: "lead_time_days", current: (supplier) => supplier.lead_time_days },
      { column: "currency", current: (supplier) => supplier.currency, same: sameCode, immutable: true },
      { column: "contact_name", current: (supplier) => textOrNull(supplier.contact_name), clear: "" },
      {
        column: "contact_email",
        current: (supplier) => textOrNull(supplier.contact_email),
        same: sameEmail,
        clear: "",
      },
      { column: "location", current: (supplier) => textOrNull(supplier.location), clear: "" },
      { column: "since", current: (supplier) => supplier.since },
    ],

    async send(supplier, body) {
      await updateSupplier(supplier.id, body as SupplierPatch);
    },
  },
});
