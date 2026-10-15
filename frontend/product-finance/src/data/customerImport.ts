/**
 * Finance customers from and to a spreadsheet (ADR-0022). Import calls `POST /finance/customers`
 * once per row, so the server's own checks apply to every row: the GSTIN's checksum, that it
 * belongs to the billing state, the PIN code, and the phone (a bare 10-numeral Indian mobile is
 * stored as +91, sealed, and only ever shown masked).
 *
 * Natural key: the GSTIN. A row whose GSTIN a live customer already holds is answered 409 "already
 * exists" and reported as skipped, so uploading the same sheet twice adds nothing twice. A row with
 * no GSTIN (an unregistered buyer) has no key the server can hold to: one whose name matches an
 * unregistered customer already on file is refused before anything is sent, with the reason, rather
 * than silently adding a second. Both are `RowSkipped`: previewed as "Already exists" and reported
 * as skipped, never as a row that needs fixing.
 *
 * Opening balances are not imported: Finance keeps an accounts-receivable book of invoices and
 * payments, not a general ledger, so there is nothing to post one to. An opening balance owed by a
 * customer is entered as what it is, an invoice or a payment.
 */
import { RowProblem, RowSkipped, collectPages, defineImportEntity, makeLookup } from "@opsgrids/client";
import type { ColumnOption, ImportColumn, Lookup, ParsedRow } from "@opsgrids/client";

import { createCustomer, listCustomers } from "@/api/client";
import type { CustomerSort } from "@/api/client";
import type { CustomerCreate, CustomerSummary } from "@/api/types";
import { GST_STATE_NAME } from "@/data/gstStates";

/** What the Customers screen filters its list by; the export writes exactly that list. */
export interface CustomerFilters {
  search: string;
  sort: CustomerSort;
}

/** State options: the name is what a person types, the GST code also matches (`27`). */
const STATE_OPTIONS: readonly ColumnOption[] = Object.entries(GST_STATE_NAME)
  .filter(([, name]) => !name.endsWith("(old)"))
  .map(([code, name]) => ({ value: code, label: name }));

const UNREGISTERED_NAMES = "finance.customers.unregisteredNames";
const GSTINS_ON_FILE = "finance.customers.gstins";

const COLUMNS: readonly ImportColumn[] = [
  {
    key: "name",
    header: "Name",
    kind: "text",
    required: true,
    maxLength: 200,
    help: "The customer's name as it should print on an invoice: the legal name for a registered business.",
  },
  {
    key: "gstin",
    header: "GSTIN",
    kind: "text",
    maxLength: 15,
    pattern: /^[0-9]{2}[A-Za-z]{5}[0-9]{4}[A-Za-z][1-9A-Za-z][Zz][0-9A-Za-z]$/,
    patternHint: "15 characters, like 27AABCA9012D1Z5. The check character is verified when the row is sent.",
    help: "Leave blank for an unregistered (B2C) buyer. A customer whose GSTIN is already on file is skipped.",
  },
  {
    key: "phone",
    header: "Phone",
    // Text, not the engine's `phone` kind: that one completes a bare number only when the
    // organization's profile says India, while Finance bills in India by definition and the server
    // reads a bare 10-numeral mobile as +91 for every organization (`domain.normalise_phone`).
    kind: "text",
    maxLength: 24,
    pattern: /^\+?[0-9][0-9 ().-]{6,22}$/,
    patternHint: "Numerals, with an optional leading + and spaces or dashes, like +91 98765 43210.",
    help: "A 10-numeral Indian mobile (read as +91), or the number with its country code, like +91 98765 43210. Optional. Exports show it masked and never in full.",
  },
  {
    key: "email",
    header: "Email",
    kind: "email",
    maxLength: 200,
    help: "Optional.",
  },
  {
    key: "contactName",
    header: "Contact person",
    kind: "text",
    maxLength: 200,
    help: "Optional: who to ask for in accounts payable.",
  },
  {
    key: "addressLine1",
    header: "Billing address",
    kind: "text",
    maxLength: 200,
    help: "Printed on the tax invoice. Required on an invoice for an unregistered buyer billed ₹50,000 or more.",
  },
  {
    key: "addressLine2",
    header: "Address line 2",
    kind: "text",
    maxLength: 200,
    help: "Optional.",
  },
  {
    key: "city",
    header: "City",
    kind: "text",
    maxLength: 120,
    help: "Optional.",
  },
  {
    key: "pincode",
    header: "PIN code",
    kind: "text",
    pattern: /^[1-9][0-9]{5}$/,
    patternHint: "Six numerals, not starting with 0.",
    help: "Optional. Six numerals.",
  },
  {
    key: "state",
    header: "State",
    kind: "enum",
    options: STATE_OPTIONS,
    help: "The state of the billing address: a name from the list, or its two-numeral GST code (27 for Maharashtra). With a GSTIN it must be the GSTIN's state; leave it blank to take the GSTIN's. Decides the place of supply for an unregistered buyer.",
  },
  {
    key: "phoneMasked",
    header: "Phone (masked)",
    kind: "text",
    readOnly: true,
    help: "The number as Finance shows it, with most numerals hidden. Filled in on export and ignored on import.",
  },
  {
    key: "id",
    header: "Customer ID",
    kind: "text",
    readOnly: true,
    help: "The record's id. Filled in on export. A row that carries one is refused on import: that customer already exists.",
  },
];

function text(row: ParsedRow, key: string): string | undefined {
  const value = row[key];
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

/** Why an unregistered row is refused, or null: a name already on file without a GSTIN. */
function unregisteredDuplicate(name: string, onFile: Lookup | undefined): string | null {
  if (onFile === undefined || onFile.resolve(name) === undefined && !onFile.isAmbiguous(name)) return null;
  return `An unregistered customer named ${name} is already on file. Without a GSTIN there is nothing to tell them apart, so this row is not added; add a GSTIN to the row, or add the second customer by hand if they really are different.`;
}

export const customerImport = defineImportEntity<CustomerCreate, CustomerSummary, CustomerFilters>({
  id: "finance.customers",
  noun: "customers",
  nounSingular: "customer",
  fileStem: "finance-customers",
  columns: COLUMNS,
  sampleRows: [
    {
      name: "Konkan Traders Private Limited",
      gstin: "27AAECO9012H1ZG",
      phone: "9876543210",
      email: "accounts@konkantraders.in",
      contactName: "Asha Rao",
      addressLine1: "12 FC Road",
      addressLine2: "Shivajinagar",
      city: "Pune",
      pincode: "411004",
      state: "Maharashtra",
    },
    {
      name: "Ravi Kumar",
      phone: "+91 91234 56789",
      addressLine1: "45 MG Road",
      city: "Bengaluru",
      pincode: "560001",
      state: "Karnataka",
    },
  ],
  notes: [
    "A customer whose GSTIN is already on file is skipped, not updated, so the same sheet can be uploaded again safely.",
    "A row without a GSTIN whose name matches an unregistered customer already on file is refused, so the same buyer is not added twice.",
    "The GSTIN's check character is verified by the server; a mistyped GSTIN is reported on its row.",
    "Phone numbers: a 10-numeral Indian mobile is stored with +91. Exports show numbers masked, as the app does.",
    "Opening balances are not imported: Finance has no general ledger to post them to. Record what a customer already owes as an invoice, and what they paid as a payment.",
    "A file exported from the Customers list cannot be imported again as it is: its rows carry a Customer ID and are refused, because those customers already exist.",
  ],
  recordIdKey: "id",
  loadLookups: async (signal) => {
    const all = await collectPages((page, size) => listCustomers({ sort: "name_asc", page, size }), {
      key: (row) => row.customer.id,
      signal,
    });
    const unregistered = all
      .filter((row) => row.customer.gstin === null)
      .map((row) => ({ label: row.customer.name, value: row.customer.id }));
    const gstins = all.flatMap((row) =>
      row.customer.gstin === null ? [] : [{ label: row.customer.gstin, value: row.customer.id }],
    );
    return { [UNREGISTERED_NAMES]: makeLookup(unregistered), [GSTINS_ON_FILE]: makeLookup(gstins) };
  },
  validateRow: (row) => {
    const gstin = text(row, "gstin")?.toUpperCase();
    const state = text(row, "state");
    if (gstin !== undefined && state !== undefined && gstin.slice(0, 2) !== state) {
      return `The GSTIN is registered in ${GST_STATE_NAME[gstin.slice(0, 2)] ?? gstin.slice(0, 2)}, but State says ${GST_STATE_NAME[state] ?? state}. Correct one of them, or leave State blank.`;
    }
    return null;
  },
  toPayload: (row, ctx) => {
    const name = text(row, "name");
    if (name === undefined) throw new RowProblem("Name is required");
    const gstin = text(row, "gstin")?.toUpperCase();
    if (gstin === undefined) {
      const problem = unregisteredDuplicate(name, ctx.lookups[UNREGISTERED_NAMES]);
      if (problem !== null) throw new RowSkipped(problem);
    } else if (ctx.lookups[GSTINS_ON_FILE]?.resolve(gstin) !== undefined) {
      throw new RowSkipped(`A customer with GSTIN ${gstin} is already on file, so this row is skipped and nothing is overwritten.`);
    }
    const stateCode = text(row, "state") ?? gstin?.slice(0, 2);
    const phone = text(row, "phone");
    return {
      name,
      gstin: gstin ?? null,
      ...(phone !== undefined ? { phone } : {}),
      email: text(row, "email") ?? "",
      contact_name: text(row, "contactName") ?? "",
      address_line1: text(row, "addressLine1") ?? "",
      address_line2: text(row, "addressLine2") ?? "",
      city: text(row, "city") ?? "",
      pincode: text(row, "pincode") ?? "",
      state: stateCode !== undefined ? (GST_STATE_NAME[stateCode] ?? "") : "",
      state_code: stateCode ?? null,
    };
  },
  create: async (payload) => {
    const created = await createCustomer(payload);
    return { id: created.id };
  },
  export: {
    fetchPage: ({ page, size, filters }) => listCustomers({ search: filters.search, sort: filters.sort, page, size }),
    toRow: (row) => ({
      name: row.customer.name,
      gstin: row.customer.gstin,
      phone: null,
      email: row.customer.email,
      contactName: row.customer.contact_name,
      addressLine1: row.customer.address_line1 ?? "",
      addressLine2: row.customer.address_line2 ?? "",
      city: row.customer.city,
      pincode: row.customer.pincode ?? "",
      state: row.customer.state_code ? (GST_STATE_NAME[row.customer.state_code] ?? row.customer.state) : row.customer.state,
      phoneMasked: row.customer.phone ?? "",
      id: row.customer.id,
    }),
  },
});
