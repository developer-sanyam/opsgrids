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
import type { Customer } from "@/api/types";

export const CUSTOMERS: Customer[] = [
  { id: "cust-01", name: "Meridian Textiles Pvt Ltd", contact_name: "Harish Patel", email: "accounts@meridiantex.in", gstin: "24AABCM1234F1Z3", city: "Surat", state: "Gujarat" },
  { id: "cust-02", name: "Kanaka Foods Ltd", contact_name: "Latha Subramaniam", email: "finance@kanakafoods.in", gstin: "33AAACK5678G1ZH", city: "Chennai", state: "Tamil Nadu" },
  { id: "cust-03", name: "Orbit Logistics", contact_name: "Devendra Joshi", email: "billing@orbitlogistics.in", gstin: "27AAECO9012H1ZG", city: "Pune", state: "Maharashtra" },
  { id: "cust-04", name: "Sundara Electricals", contact_name: "Ravi Gopal", email: "ap@sundaraelec.in", gstin: "29AADCS3456J1Z0", city: "Bengaluru", state: "Karnataka" },
  { id: "cust-05", name: "Nimbus Cloud Services", contact_name: "Ayesha Khan", email: "ap@nimbuscloud.in", gstin: "36AAFCN7890K1ZS", city: "Hyderabad", state: "Telangana" },
  { id: "cust-06", name: "Trishul Steel Works", contact_name: "Manoj Agrawal", email: "accounts@trishulsteel.in", gstin: "22AAGCT2345L1ZC", city: "Raipur", state: "Chhattisgarh" },
  { id: "cust-07", name: "Anand Pharma", contact_name: "Sunita Rao", email: "finance@anandpharma.in", gstin: "24AAHCA6789M1Z0", city: "Ahmedabad", state: "Gujarat" },
  { id: "cust-08", name: "Coastal Traders LLP", contact_name: "Thomas Kurian", email: "billing@coastaltraders.in", gstin: "32AAJFC0123N1ZR", city: "Kochi", state: "Kerala" },
  { id: "cust-09", name: "Vega Interiors", contact_name: "Priyanka Sethi", email: "accounts@vegainteriors.in", gstin: "06AAKCV4567P1ZG", city: "Gurugram", state: "Haryana" },
  { id: "cust-10", name: "Lotus Hospitality Group", contact_name: "Rajat Chauhan", email: "ap@lotushospitality.in", gstin: "08AALCL8901Q1ZP", city: "Jaipur", state: "Rajasthan" },
  { id: "cust-11", name: "Deccan AgriTech", contact_name: "Vaishnavi Kulkarni", email: "finance@deccanagritech.in", gstin: "27AAMCD2345R1Z0", city: "Nagpur", state: "Maharashtra" },
  { id: "cust-12", name: "Apex Motors", contact_name: "Iqbal Ahmed", email: "accounts@apexmotors.in", gstin: "07AANCA6789S1ZE", city: "New Delhi", state: "Delhi" },
  // Unregistered: a buyer with no GSTIN. Their supplies are B2C, which is the one classification
  // that needs no IRN — the case a compliance screen has to distinguish from "not filed yet".
  { id: "cust-13", name: "Rajwada Banquets", contact_name: "Meera Deshpande", email: "accounts@rajwadabanquets.in", gstin: null, city: "Pune", state: "Maharashtra" },
];

/** The fixture directory as a map — the offline stand-in for the live customers ledger. */
export const FIXTURE_CUSTOMERS: ReadonlyMap<string, Customer> = new Map(
  CUSTOMERS.map((c) => [c.id, c]),
);

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
