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
import { resolveZone, zoneAbbreviation } from "@opsgrids/ui";
import type { ZoneLookup } from "@opsgrids/ui";

import { USE_FIXTURES } from "@/api/client";
import { FIXTURE_LOCATIONS, FIXTURE_ORGANIZATION } from "@/api/fixtures";
import type { LocationOut, OrganizationOut } from "@/api/types";

/**
 * `ZoneLookup` plus the location's own name per code — a superset every existing caller of
 * `ZoneLookup` still accepts (`resolveZone`, `formatInstant`). §4.E's "Times are in Bengaluru
 * time (IST)." line needs the location's *name*, which `ZoneLookup` itself deliberately does not
 * carry (§2.1: approval and delegation responses return a code, never a resolved zone or a
 * name) — this is the one place that name is looked up, the same way the code is.
 */
export interface ZoneDirectory extends ZoneLookup {
  /** Location `name` per `code`, from `LocationOut.name`. Fetched once per session, with `locations`. */
  names: Readonly<Record<string, string>>;
}

/** One process-lifetime fetch — every screen that needs a zone shares it (§2.1: "fetched once per
 *  session and cached; approvals never fetch it per row"). Cleared only by a full reload. */
let cached: Promise<ZoneDirectory> | null = null;

async function loadZoneLookup(): Promise<ZoneDirectory> {
  if (USE_FIXTURES) {
    return {
      locations: Object.fromEntries(FIXTURE_LOCATIONS.map((l) => [l.code, l.timezone])),
      names: Object.fromEntries(FIXTURE_LOCATIONS.map((l) => [l.code, l.name])),
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
    names: Object.fromEntries(locations.map((l) => [l.code, l.name])),
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
export function useZoneLookup(): ZoneDirectory | null {
  const [lookup, setLookup] = useState<ZoneDirectory | null>(null);

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

/**
 * §4.E's shared timezone line, honest either way: `"Times are in Bengaluru time (IST)."` when the
 * code resolves to a real location, or `"Times are in organization time (Asia/Kolkata)."` on the
 * same visible fallback §2.1 uses everywhere else — never a silent one.
 */
export function delegationZoneNote(
  locationCode: string | null | undefined,
  lookup: ZoneDirectory,
  now: Date = new Date(),
): string {
  const { zone, fallback } = resolveZone(locationCode, lookup);
  if (fallback) return `Times are in organization time (${zone}).`;
  const name = locationCode ? lookup.names[locationCode] : undefined;
  if (!name) return `Times are in organization time (${zone}).`;
  return `Times are in ${name} time (${zoneAbbreviation(now, zone)}).`;
}
