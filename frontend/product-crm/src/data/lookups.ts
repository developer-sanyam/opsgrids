/**
 * Client-side resolution of the ids the CRM records carry. A deal stores `company_id`,
 * `owner_id` and `primary_contact_id`; the service returns those as opaque ids (ADR: no
 * cross-schema joins), so the product resolves display names here. Backed by the fixture arrays
 * for now — when the accounts/owners endpoints exist these become fetches with the same shape.
 */
import { COMPANIES, CONTACTS, OWNERS } from "@/api/fixtures";
import type { Company, Contact, Owner } from "@/api/types";

const OWNER_BY_ID = new Map(OWNERS.map((o) => [o.id, o]));
const COMPANY_BY_ID = new Map(COMPANIES.map((c) => [c.id, c]));
const CONTACT_BY_ID = new Map(CONTACTS.map((c) => [c.id, c]));

export function ownerById(id: string | null): Owner | null {
  return id === null ? null : OWNER_BY_ID.get(id) ?? null;
}

export function ownerName(id: string | null): string {
  return ownerById(id)?.full_name ?? "Unassigned";
}

export function companyById(id: string | null): Company | null {
  return id === null ? null : COMPANY_BY_ID.get(id) ?? null;
}

export function companyName(id: string | null): string {
  return companyById(id)?.name ?? "—";
}

export function contactById(id: string | null): Contact | null {
  return id === null ? null : CONTACT_BY_ID.get(id) ?? null;
}

export function contactName(id: string | null): string {
  return contactById(id)?.full_name ?? "—";
}

/** Every contact that belongs to a company, in creation order. */
export function contactsForCompany(companyId: string): Contact[] {
  return CONTACTS.filter((c) => c.company_id === companyId);
}
