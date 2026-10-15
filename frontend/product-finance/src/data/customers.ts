/**
 * Customer-name resolution for an invoice or a payment.
 *
 * Invoices and payments carry `customer_id` and never the party's name — ADR-0000 forbids the
 * cross-schema join, so the id is a plain UUID the UI maps to a display record. Live, that map
 * comes from the customers ledger (`@/api/customers`), whose rows embed their `Customer`. The
 * fixture list below is the same reference data for offline work, keyed by slug rather than UUID.
 *
 * Both paths go through one resolver so the label a table shows and the label the detail screen
 * shows can never diverge.
 */
import type { Customer, CustomerCreate, CustomerUpdate } from "@/api/types";

export const CUSTOMERS: Customer[] = [
  { id: "cust-01", name: "Meridian Textiles Pvt Ltd", contact_name: "Harish Patel", email: "accounts@meridiantex.in", gstin: "24AABCM1234F1Z3", city: "Surat", state: "Gujarat", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-02", name: "Kanaka Foods Ltd", contact_name: "Latha Subramaniam", email: "finance@kanakafoods.in", gstin: "33AAACK5678G1ZH", city: "Chennai", state: "Tamil Nadu", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-03", name: "Orbit Logistics", contact_name: "Devendra Joshi", email: "billing@orbitlogistics.in", gstin: "27AAECO9012H1ZG", city: "Pune", state: "Maharashtra", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-04", name: "Sundara Electricals", contact_name: "Ravi Gopal", email: "ap@sundaraelec.in", gstin: "29AADCS3456J1Z0", city: "Bengaluru", state: "Karnataka", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-05", name: "Nimbus Cloud Services", contact_name: "Ayesha Khan", email: "ap@nimbuscloud.in", gstin: "36AAFCN7890K1ZS", city: "Hyderabad", state: "Telangana", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-06", name: "Trishul Steel Works", contact_name: "Manoj Agrawal", email: "accounts@trishulsteel.in", gstin: "22AAGCT2345L1ZC", city: "Raipur", state: "Chhattisgarh", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-07", name: "Anand Pharma", contact_name: "Sunita Rao", email: "finance@anandpharma.in", gstin: "24AAHCA6789M1Z0", city: "Ahmedabad", state: "Gujarat", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-08", name: "Coastal Traders LLP", contact_name: "Thomas Kurian", email: "billing@coastaltraders.in", gstin: "32AAJFC0123N1ZR", city: "Kochi", state: "Kerala", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-09", name: "Vega Interiors", contact_name: "Priyanka Sethi", email: "accounts@vegainteriors.in", gstin: "06AAKCV4567P1ZG", city: "Gurugram", state: "Haryana", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-10", name: "Lotus Hospitality Group", contact_name: "Rajat Chauhan", email: "ap@lotushospitality.in", gstin: "08AALCL8901Q1ZP", city: "Jaipur", state: "Rajasthan", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-11", name: "Deccan AgriTech", contact_name: "Vaishnavi Kulkarni", email: "finance@deccanagritech.in", gstin: "27AAMCD2345R1Z0", city: "Nagpur", state: "Maharashtra", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  { id: "cust-12", name: "Apex Motors", contact_name: "Iqbal Ahmed", email: "accounts@apexmotors.in", gstin: "07AANCA6789S1ZE", city: "New Delhi", state: "Delhi", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
  // Unregistered: a buyer with no GSTIN. Their supplies are B2C, which is the one classification
  // that needs no IRN — the case a compliance screen has to distinguish from "not filed yet".
  { id: "cust-13", name: "Rajwada Banquets", contact_name: "Meera Deshpande", email: "accounts@rajwadabanquets.in", gstin: null, city: "Pune", state: "Maharashtra", is_tds_deductor: false, deducts_tds_194q: false, tcs_applicable: false },
];

/**
 * The fixture directory as a map — the offline stand-in for the live customers ledger. Mutable
 * underneath (fixture writes have to land somewhere); the exported type is read-only so nothing
 * outside this module's own write helpers below can put it out of sync with `CUSTOMERS`.
 */
const MUTABLE_FIXTURE_CUSTOMERS = new Map<string, Customer>(CUSTOMERS.map((c) => [c.id, c]));
export const FIXTURE_CUSTOMERS: ReadonlyMap<string, Customer> = MUTABLE_FIXTURE_CUSTOMERS;

let nextFixtureCustomerSeq = CUSTOMERS.length + 1;

/** Fixture-mode `POST /finance/customers` — appends to both the array and the lookup map. */
/** The mask the server returns for a phone: bullets and the last four numerals. Never the number. */
function maskPhone(raw: string | undefined): string {
  const digits = (raw ?? "").replace(/\D/g, "");
  return digits === "" ? "" : `${"•".repeat(Math.max(digits.length - 4, 0))} ${digits.slice(-4)}`;
}

export function addFixtureCustomer(input: CustomerCreate): Customer {
  const customer: Customer = {
    id: `cust-fixture-${nextFixtureCustomerSeq++}`,
    name: input.name,
    contact_name: input.contact_name ?? "",
    email: input.email ?? "",
    gstin: input.gstin ?? null,
    city: input.city ?? "",
    state: input.state ?? "",
    address_line1: input.address_line1 ?? "",
    address_line2: input.address_line2 ?? "",
    pincode: input.pincode ?? "",
    state_code: input.state_code ?? null,
    phone: maskPhone(input.phone),
    is_tds_deductor: input.is_tds_deductor ?? false,
    deducts_tds_194q: input.deducts_tds_194q ?? false,
    tcs_applicable: input.tcs_applicable ?? false,
  };
  CUSTOMERS.push(customer);
  MUTABLE_FIXTURE_CUSTOMERS.set(customer.id, customer);
  return customer;
}

/** Fixture-mode `PATCH /finance/customers/{id}` — only the fields present are applied. */
export function updateFixtureCustomer(id: string, patch: CustomerUpdate): Customer | null {
  const existing = MUTABLE_FIXTURE_CUSTOMERS.get(id);
  if (!existing) return null;
  const { phone, ...rest } = patch;
  const updated: Customer = { ...existing, ...rest, ...(phone !== undefined ? { phone: maskPhone(phone) } : {}) };
  MUTABLE_FIXTURE_CUSTOMERS.set(id, updated);
  const index = CUSTOMERS.findIndex((c) => c.id === id);
  if (index >= 0) CUSTOMERS[index] = updated;
  return updated;
}

/** Fixture-mode `DELETE /finance/customers/{id}` — archive is a removal from the directory. */
export function removeFixtureCustomer(id: string): void {
  MUTABLE_FIXTURE_CUSTOMERS.delete(id);
  const index = CUSTOMERS.findIndex((c) => c.id === id);
  if (index >= 0) CUSTOMERS.splice(index, 1);
}

/** Resolve one `customer_id` against a directory. Null when the id isn't in it. */
export function getCustomer(
  byId: ReadonlyMap<string, Customer> | undefined,
  id: string,
): Customer | null {
  return byId?.get(id) ?? null;
}

/**
 * A customer's display name, or a stable fallback while the directory is still loading or the id
 * doesn't resolve. Never throws and never blocks a row from rendering.
 */
export function customerName(
  byId: ReadonlyMap<string, Customer> | undefined,
  id: string,
): string {
  return byId?.get(id)?.name ?? "Unknown customer";
}
