/**
 * The organization's timezone, currency and locale — `GET /organization`, read once per session
 * and shared by every screen (the pattern of HRMS `lib/zone.ts`), so those defaults stop being
 * literals scattered through the apps.
 *
 * A failed read is not an error screen: the platform defaults (`Asia/Kolkata`, `INR`, `en-IN`)
 * are returned with `status: "fallback"`, so a caller can say the values are assumed rather than
 * pass them off as the organization's own. Cleared only by a full reload.
 */

import { useEffect, useState } from "react";

import { apiRequest } from "../http";
import { setDefaultPhoneRegion } from "../phone";

export interface OrgProfile {
  timezone: string;
  currency: string;
  locale: string;
  /** The organization's country (ISO 3166-1 alpha-2), or `null` when it never set one. A bare
   *  national phone number is read in this country (`normalisePhone`). */
  country: string | null;
  /** `loading` until the one read settles; `fallback` when it failed and the defaults stand in. */
  status: "loading" | "success" | "fallback";
}

interface OrganizationWire {
  timezone: string;
  currency: string;
  locale: string;
  country?: string | null;
}

const DEFAULTS = { timezone: "Asia/Kolkata", currency: "INR", locale: "en-IN", country: "IN" } as const;

/**
 * The country a bare national phone number is read in. The organization's own country when it set
 * one. A self-serve signup never asks for it, so an organization that runs in rupees or on India
 * time with no country is read as Indian -- the platform's home defaults, which it kept -- rather
 * than refusing every ten-digit mobile its people type. Anything else with no country: none.
 */
function phoneRegionOf(profile: Pick<OrgProfile, "country" | "currency" | "timezone">): string | null {
  if (profile.country !== null && profile.country.trim() !== "") return profile.country;
  return profile.currency === "INR" || profile.timezone === "Asia/Kolkata" ? "IN" : null;
}

let settled: OrgProfile | null = null;
let pending: Promise<OrgProfile> | null = null;

/** Read the profile once per session. Importers call it before they parse, so the organization's
 *  own country decides how a bare phone number is read (not the platform default). */
export function loadOrgProfile(): Promise<OrgProfile> {
  pending ??= apiRequest<OrganizationWire>("/organization", { signalUnauthorized: false })
    .then(
      (org): OrgProfile => ({
        timezone: org.timezone,
        currency: org.currency,
        locale: org.locale,
        country: org.country ?? null,
        status: "success",
      }),
      (): OrgProfile => ({ ...DEFAULTS, status: "fallback" }),
    )
    .then((profile) => {
      settled = profile;
      setDefaultPhoneRegion(phoneRegionOf(profile));
      return profile;
    });
  return pending;
}

const load = loadOrgProfile;

export function useOrgProfile(): OrgProfile {
  const [profile, setProfile] = useState<OrgProfile>(() => settled ?? { ...DEFAULTS, status: "loading" });

  useEffect(() => {
    if (settled !== null) return;
    let active = true;
    void load().then((next) => {
      if (active) setProfile(next);
    });
    return () => {
      active = false;
    };
  }, []);

  return profile;
}

/** `YYYY-MM-DD` for an instant in a zone; with no zone, the browser's own local date. */
export function dateInZone(now: Date, zone?: string): string {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  } catch {
    return dateInZone(now);
  }
}

/**
 * "Today" as a calendar date in the organization's timezone, falling back to the browser's local
 * date until the profile has loaded (or when it could not be read). Never the UTC date: at 00:40
 * IST the UTC date is still yesterday. The first call starts the profile read.
 */
export function orgToday(now: Date = new Date()): string {
  if (settled === null) void loadOrgProfile();
  return dateInZone(now, settled?.status === "success" ? settled.timezone : undefined);
}
