/**
 * The delivery team roster fixture. Only used in fixture mode (`client.ts`'s `getPeople`) and to
 * build the fixture timesheets in `fixtures.ts` — the live roster comes from the project
 * service's `/projects/people` and is resolved by whichever screen needs a name, against the
 * people it actually fetched (see `routes/Board.tsx`, `StandupList`, `ProjectsPage`), never from this
 * static list.
 */
import type { Person } from "@/api/types";

export const PEOPLE: Person[] = [
  { id: "p01", uuid: "uuid-p01", name: "Nadia Rahman", role: "Head of Delivery", location: "Bengaluru", capacity_hours: 20 },
  { id: "p02", uuid: "uuid-p02", name: "Arman Qureshi", role: "Engineering Lead", location: "Bengaluru", capacity_hours: 30 },
  { id: "p03", uuid: "uuid-p03", name: "Priya Nair", role: "Senior Product Designer", location: "Lisbon", capacity_hours: 36 },
  { id: "p04", uuid: "uuid-p04", name: "Diego Santos", role: "Full-Stack Engineer", location: "Lisbon", capacity_hours: 40 },
  { id: "p05", uuid: "uuid-p05", name: "Mei Lin", role: "Frontend Engineer", location: "Singapore", capacity_hours: 40 },
  { id: "p06", uuid: "uuid-p06", name: "Kwame Mensah", role: "Backend Engineer", location: "Accra", capacity_hours: 40 },
  { id: "p07", uuid: "uuid-p07", name: "Sofia Rossi", role: "Product Manager", location: "Berlin", capacity_hours: 34 },
  { id: "p08", uuid: "uuid-p08", name: "Yuki Tanaka", role: "QA Engineer", location: "Singapore", capacity_hours: 38 },
  { id: "p09", uuid: "uuid-p09", name: "Omar Haddad", role: "DevOps Engineer", location: "Dubai", capacity_hours: 36 },
  { id: "p10", uuid: "uuid-p10", name: "Hannah Weber", role: "UX Researcher", location: "Berlin", capacity_hours: 32 },
  { id: "p11", uuid: "uuid-p11", name: "Rohan Kapoor", role: "Data Engineer", location: "Bengaluru", capacity_hours: 40 },
  { id: "p12", uuid: "uuid-p12", name: "Isabella Costa", role: "Delivery Coordinator", location: "Lisbon", capacity_hours: 30 },
];
