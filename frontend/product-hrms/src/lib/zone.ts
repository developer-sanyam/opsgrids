/**
 * The organization's zone facts — `GET /locations` and `GET /organization`, cached once per
 * session (ADR-0000, screens spec §2.1). Approval, attendance and every other surface return a
 * **location code**, never a resolved zone; this is the one place that code is turned into an
 * IANA zone, by looking it up against the account service's small, org-wide readable location
 * list, falling back to the organization's own zone — **visibly**, never silently — when the code
 * is `null` or matches nothing.
 *
 * The browser's timezone is never read here, or anywhere downstream of it.
 */

import { useEffect, useState } from "react";

import { apiRequest } from "@opsgrids/client";
import type { ZoneLookup } from "@opsgrids/ui";

import { USE_FIXTURES } from "@/api/client";
import { FIXTURE_LOCATIONS, FIXTURE_ORGANIZATION } from "@/api/fixtures";
import type { LocationOut, OrganizationOut } from "@/api/types";

/** One process-lifetime fetch — every screen that needs a zone shares it (§2.1: "fetched once per
 *  session and cached; approvals never fetch it per row"). Cleared only by a full reload. */
let cached: Promise<ZoneLookup> | null = null;

async function loadZoneLookup(): Promise<ZoneLookup> {
  if (USE_FIXTURES) {
    return {
      locations: Object.fromEntries(FIXTURE_LOCATIONS.map((l) => [l.code, l.timezone])),
      organizationZone: FIXTURE_ORGANIZATION.timezone,
    };
  }

  const [locations, organization] = await Promise.all([
    apiRequest<LocationOut[]>("/locations", { signalUnauthorized: false }).catch(
      () => [] as LocationOut[],
    ),
    apiRequest<OrganizationOut>("/organization", { signalUnauthorized: false }).catch(
      () => null,
    ),
  ]);

  return {
    locations: Object.fromEntries(locations.map((l) => [l.code, l.timezone])),
    // A degraded read (both calls failing) has no zone fact left to give — UTC is the platform's
    // own storage zone, so it is at least never a fabricated location. This is not the labelled
    // "organization time" fallback §2.1 asks for; it is what happens one level below it, when
    // there is no organization zone to fall back *to*.
    organizationZone: organization?.timezone ?? "UTC",
  };
}

/**
 * The cached zone lookup, or `null` until the first screen to ask for it resolves the fetch.
 * Every caller shares the one in-flight request — no screen fetches `/locations` per row.
 */
export function useZoneLookup(): ZoneLookup | null {
  const [lookup, setLookup] = useState<ZoneLookup | null>(null);

  useEffect(() => {
    let active = true;
    cached ??= loadZoneLookup();
    cached.then((resolved) => {
      if (active) setLookup(resolved);
    });
    return () => {
      active = false;
    };
  }, []);

  return lookup;
}
