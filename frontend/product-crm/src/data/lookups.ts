/**
 * Client-side resolution of the ids the CRM records carry. A deal stores `company_id`,
 * `owner_id` and `primary_contact_id`; the service returns those as opaque ids (ADR: no
 * cross-schema joins), so the product resolves display names here.
 *
 * Pure functions over an explicit directory, never a module-level fixture import — the caller
 * (see `DirectoryProvider`) is what decides whether that directory came from the gateway or from
 * fixtures, and a function that quietly reached for fixture data on its own is exactly the bug
 * that shipped fabricated names to production.
 */
import type { Company, Contact, Owner } from "@/api/types";

export function ownerById(owners: readonly Owner[], id: string | null): Owner | null {
  return id === null ? null : owners.find((o) => o.id === id) ?? null;
}

export function ownerName(owners: readonly Owner[], id: string | null): string {
  return ownerById(owners, id)?.full_name ?? "Unassigned";
}

export function companyById(companies: readonly Company[], id: string | null): Company | null {
  return id === null ? null : companies.find((c) => c.id === id) ?? null;
}

export function companyName(companies: readonly Company[], id: string | null): string {
  return companyById(companies, id)?.name ?? "—";
}

export function contactById(contacts: readonly Contact[], id: string | null): Contact | null {
  return id === null ? null : contacts.find((c) => c.id === id) ?? null;
}

export function contactName(contacts: readonly Contact[], id: string | null): string {
  return contactById(contacts, id)?.full_name ?? "—";
}

/** Every contact that belongs to a company. The contacts list has no `company_id` filter on the
 *  gateway, so this filters the already-loaded directory client-side — the same shape as
 *  `CompanyDetail`'s own deals-for-this-company filter over `getAllDeals()`. */
export function contactsForCompany(contacts: readonly Contact[], companyId: string): Contact[] {
  return contacts.filter((c) => c.company_id === companyId);
}
