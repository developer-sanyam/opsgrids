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

export interface OrgProfile {
  timezone: string;
  currency: string;
  locale: string;
  /** `loading` until the one read settles; `fallback` when it failed and the defaults stand in. */
  status: "loading" | "success" | "fallback";
}

interface OrganizationWire {
  timezone: string;
  currency: string;
  locale: string;
}

const DEFAULTS = { timezone: "Asia/Kolkata", currency: "INR", locale: "en-IN" } as const;

let settled: OrgProfile | null = null;
let pending: Promise<OrgProfile> | null = null;

function load(): Promise<OrgProfile> {
  pending ??= apiRequest<OrganizationWire>("/organization", { signalUnauthorized: false })
    .then(
      (org): OrgProfile => ({ timezone: org.timezone, currency: org.currency, locale: org.locale, status: "success" }),
      (): OrgProfile => ({ ...DEFAULTS, status: "fallback" }),
    )
    .then((profile) => {
      settled = profile;
      return profile;
    });
  return pending;
}

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
