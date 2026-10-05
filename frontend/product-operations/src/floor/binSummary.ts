import type { LocationKind, LocationOut } from "@/api/types";

/**
 * The location a floor view's drawer opens on. A published placement carries only these four
 * fields; the auto-arranged view reads the whole `LocationOut` from the location tree, which the
 * drawer shows in full when it has it.
 */
export interface BinSummary {
  id: string;
  code: string;
  name: string;
  kind: LocationKind;
  details: LocationOut | null;
}
