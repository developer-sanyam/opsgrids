/**
 * The delivery team roster. The PulseBoard service stores a `person_id` (a UUID) on every task,
 * project and timesheet; names and roles are resolved here until a people endpoint exists. Kept
 * as reference data so the whole app can render one consistent team.
 */
import type { Person } from "@/api/types";

export const PEOPLE: Person[] = [
  { id: "p01", name: "Nadia Rahman", role: "Head of Delivery", location: "Bengaluru", capacity_hours: 20 },
  { id: "p02", name: "Arman Qureshi", role: "Engineering Lead", location: "Bengaluru", capacity_hours: 30 },
  { id: "p03", name: "Priya Nair", role: "Senior Product Designer", location: "Lisbon", capacity_hours: 36 },
  { id: "p04", name: "Diego Santos", role: "Full-Stack Engineer", location: "Lisbon", capacity_hours: 40 },
  { id: "p05", name: "Mei Lin", role: "Frontend Engineer", location: "Singapore", capacity_hours: 40 },
  { id: "p06", name: "Kwame Mensah", role: "Backend Engineer", location: "Accra", capacity_hours: 40 },
  { id: "p07", name: "Sofia Rossi", role: "Product Manager", location: "Berlin", capacity_hours: 34 },
  { id: "p08", name: "Yuki Tanaka", role: "QA Engineer", location: "Singapore", capacity_hours: 38 },
  { id: "p09", name: "Omar Haddad", role: "DevOps Engineer", location: "Dubai", capacity_hours: 36 },
  { id: "p10", name: "Hannah Weber", role: "UX Researcher", location: "Berlin", capacity_hours: 32 },
  { id: "p11", name: "Rohan Kapoor", role: "Data Engineer", location: "Bengaluru", capacity_hours: 40 },
  { id: "p12", name: "Isabella Costa", role: "Delivery Coordinator", location: "Lisbon", capacity_hours: 30 },
];

const BY_ID = new Map(PEOPLE.map((p) => [p.id, p]));

export function personName(id: string | null): string {
  if (id === null) return "Unassigned";
  return BY_ID.get(id)?.name ?? "Unknown";
}

export function person(id: string): Person | undefined {
  return BY_ID.get(id);
}
