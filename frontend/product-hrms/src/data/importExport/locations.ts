import { ApiError, apiRequest } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";
import { FIXTURE_LOCATIONS } from "@/api/fixtures";
import type { LocationOut } from "@/api/types";

/**
 * Every location as `(label, code)` pairs for a lookup: each by its code and each by its name,
 * both valued as the code. A location code is what the employee record stores and what a
 * location-scoped role is matched against, so an employee whose code is not a real one is
 * invisible to every such role: the sheet may say "Bengaluru HQ" or "blr-hq", and the record
 * gets `BLR-HQ`. Codes come first, so an export writes the code.
 */
export function locationEntries(
  locations: readonly Pick<LocationOut, "code" | "name">[],
): { label: string; value: string }[] {
  return locations.flatMap(({ code, name }) => [
    { label: code, value: code },
    { label: name, value: code },
  ]);
}

/**
 * `GET /locations`, every site of the organisation. A caller without `core.org.location.read`
 * (403) gets none, so the import still runs and only a row that names a location is refused; any
 * other failure rejects, because a transient error must not read as "no such location" on every
 * row. A 401 is left to the client, which refreshes the session once and repeats the request.
 */
export async function loadLocations(): Promise<LocationOut[]> {
  if (USE_FIXTURES) return FIXTURE_LOCATIONS;
  try {
    return await apiRequest<LocationOut[]>("/locations");
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) return [];
    throw error;
  }
}
