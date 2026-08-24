/**
 * Customer reference data. A `finance-service` will resolve `customer_id` from an accounts
 * endpoint; until then the product resolves names here (ADR: no cross-schema joins, so the id is
 * a plain UUID the UI maps to a display record).
 */
import type { Customer } from "@/api/types";

export const CUSTOMERS: Customer[] = [
  { id: "cust-01", name: "Meridian Textiles Pvt Ltd", contact_name: "Harish Patel", email: "accounts@meridiantex.in", gstin: "24AABCM1234F1Z5", city: "Surat", state: "Gujarat" },
  { id: "cust-02", name: "Kanaka Foods Ltd", contact_name: "Latha Subramaniam", email: "finance@kanakafoods.in", gstin: "33AAACK5678G1Z2", city: "Chennai", state: "Tamil Nadu" },
  { id: "cust-03", name: "Orbit Logistics", contact_name: "Devendra Joshi", email: "billing@orbitlogistics.in", gstin: "27AAECO9012H1Z8", city: "Pune", state: "Maharashtra" },
  { id: "cust-04", name: "Sundara Electricals", contact_name: "Ravi Gopal", email: "ap@sundaraelec.in", gstin: "29AADCS3456J1Z4", city: "Bengaluru", state: "Karnataka" },
  { id: "cust-05", name: "Nimbus Cloud Services", contact_name: "Ayesha Khan", email: "ap@nimbuscloud.in", gstin: "36AAFCN7890K1Z0", city: "Hyderabad", state: "Telangana" },
  { id: "cust-06", name: "Trishul Steel Works", contact_name: "Manoj Agrawal", email: "accounts@trishulsteel.in", gstin: "22AAGCT2345L1Z6", city: "Raipur", state: "Chhattisgarh" },
  { id: "cust-07", name: "Anand Pharma", contact_name: "Sunita Rao", email: "finance@anandpharma.in", gstin: "24AAHCA6789M1Z3", city: "Ahmedabad", state: "Gujarat" },
  { id: "cust-08", name: "Coastal Traders LLP", contact_name: "Thomas Kurian", email: "billing@coastaltraders.in", gstin: "32AAJFC0123N1Z9", city: "Kochi", state: "Kerala" },
  { id: "cust-09", name: "Vega Interiors", contact_name: "Priyanka Sethi", email: "accounts@vegainteriors.in", gstin: "06AAKCV4567P1Z1", city: "Gurugram", state: "Haryana" },
  { id: "cust-10", name: "Lotus Hospitality Group", contact_name: "Rajat Chauhan", email: "ap@lotushospitality.in", gstin: "08AALCL8901Q1Z7", city: "Jaipur", state: "Rajasthan" },
  { id: "cust-11", name: "Deccan AgriTech", contact_name: "Vaishnavi Kulkarni", email: "finance@deccanagritech.in", gstin: "27AAMCD2345R1Z5", city: "Nagpur", state: "Maharashtra" },
  { id: "cust-12", name: "Apex Motors", contact_name: "Iqbal Ahmed", email: "accounts@apexmotors.in", gstin: "07AANCA6789S1Z2", city: "New Delhi", state: "Delhi" },
];

const BY_ID = new Map(CUSTOMERS.map((c) => [c.id, c]));

export function getCustomer(id: string): Customer | null {
  return BY_ID.get(id) ?? null;
}

/** A customer's display name, or a stable fallback when the id can't be resolved. */
export function customerName(id: string): string {
  return BY_ID.get(id)?.name ?? "Unknown customer";
}
