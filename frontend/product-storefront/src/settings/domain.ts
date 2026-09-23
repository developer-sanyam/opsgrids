/**
 * The store's custom domain — `GET/PUT/DELETE /storefront/store/domain` and
 * `POST /storefront/store/domain/check` (ADR-0012 §11), off `services/storefront/app/schemas.py`
 * `DomainOut`.
 *
 * This is the only place in the console that changes a domain. The settings form no longer sends
 * one (`StorePut.custom_domain`), because the claim carries verification evidence a plain string
 * field cannot.
 *
 * **Fixtures by default**, like the rest of this app. The fixture never verifies anything: it
 * claims, shows the records and reports ownership as pending, which is what a merchant sees in
 * the first minutes after claiming.
 */

import { ApiError, apiRequest, settle } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";

export type DomainStatus = "unverified" | "verified" | "live" | "failing" | "suspended";

export interface DnsRecord {
  record_type: "TXT" | "ALIAS" | "CNAME" | "A";
  name: string;
  value: string;
  note: string | null;
}

export interface CheckState {
  state: "pending" | "ok" | "failing";
  last_checked_at: string | null;
  next_check_at: string | null;
  code: string | null;
  detail: string | null;
  /** The certificate check only: when the certificate Caddy holds expires. */
  not_after: string | null;
}

export interface DomainOut {
  custom_domain: string | null;
  status: DomainStatus | null;
  platform_host: string;
  canonical_host: string;
  verification: {
    record_type: "TXT";
    name: string;
    value: string;
    issued_at: string;
    claim_expires_at: string;
  } | null;
  dns: {
    apex: boolean;
    preferred: DnsRecord;
    fallback: DnsRecord[];
    proxy_warning: string;
    also_served: string[];
  } | null;
  checks: { ownership: CheckState; routing: CheckState; certificate: CheckState } | null;
}

const PENDING: CheckState = {
  state: "pending",
  last_checked_at: null,
  next_check_at: null,
  code: null,
  detail: null,
  not_after: null,
};

let fixture: DomainOut = {
  custom_domain: null,
  status: null,
  platform_host: "greenleaf.stores.opsgrids.com",
  canonical_host: "greenleaf.stores.opsgrids.com",
  verification: null,
  dns: null,
  checks: null,
};

function fixtureClaim(host: string): DomainOut {
  const now = new Date();
  const apex = host.split(".").length === 2;
  return {
    ...fixture,
    custom_domain: host,
    status: "unverified",
    verification: {
      record_type: "TXT",
      name: `_opsgrids-challenge.${host}`,
      // Obviously-fake and low-entropy on purpose (repeated hex nibbles): a real token is
      // `secrets.token_hex(16)` server-side and never appears in this repository — a
      // realistic-looking one here is exactly what `detect-secrets` exists to flag, and per
      // ADR-0012 §14 that finding must be fixed, never baselined.
      value: "opsgrids-domain-verification=" + "ab".repeat(16),
      issued_at: now.toISOString(),
      claim_expires_at: new Date(now.getTime() + 14 * 86_400_000).toISOString(),
    },
    dns: {
      apex,
      preferred: apex
        ? { record_type: "ALIAS", name: host, value: "edge.opsgrids.com", note: "Called ANAME or CNAME flattening by some providers." }
        : { record_type: "CNAME", name: host, value: "edge.opsgrids.com", note: null },
      fallback: [{ record_type: "A", name: host, value: "20.219.18.84", note: null }],
      proxy_warning: "If your DNS provider offers a proxy (an orange cloud), turn it off for these records.",
      also_served: apex ? [`www.${host}`] : [],
    },
    checks: { ownership: PENDING, routing: PENDING, certificate: PENDING },
  };
}

/** `GET /storefront/store/domain`. `null` when the organization has no store yet (404). */
export async function getDomain(): Promise<DomainOut | null> {
  if (USE_FIXTURES) return settle({ ...fixture }, 160);
  try {
    return await apiRequest<DomainOut>("/storefront/store/domain");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

/** `PUT /storefront/store/domain`. Always lands on `unverified` with a fresh token — including a
 *  re-claim of the same name. 409 `custom_domain_taken`, 422 for a malformed or reserved name. */
export async function claimDomain(customDomain: string): Promise<DomainOut> {
  if (USE_FIXTURES) {
    fixture = fixtureClaim(customDomain.trim().toLowerCase());
    return settle({ ...fixture }, 320);
  }
  return apiRequest<DomainOut>("/storefront/store/domain", {
    method: "PUT",
    body: { custom_domain: customDomain },
  });
}

/** `DELETE /storefront/store/domain`. Idempotent; the store keeps answering on its platform host. */
export async function releaseDomain(): Promise<void> {
  if (USE_FIXTURES) {
    fixture = { ...fixture, custom_domain: null, status: null, verification: null, dns: null, checks: null };
    await settle(null, 240);
    return;
  }
  await apiRequest<void>("/storefront/store/domain", { method: "DELETE" });
}

/** `POST /storefront/store/domain/check` — runs the checks now. 429 inside the 30-second window. */
export async function checkDomain(): Promise<DomainOut> {
  if (USE_FIXTURES) {
    const at = new Date().toISOString();
    if (fixture.checks) {
      fixture = {
        ...fixture,
        checks: {
          ...fixture.checks,
          ownership: {
            ...PENDING,
            state: "failing",
            last_checked_at: at,
            code: "txt_not_found",
            detail: "No TXT record found yet. DNS changes can take a few minutes to appear.",
          },
        },
      };
    }
    return settle({ ...fixture }, 600);
  }
  return apiRequest<DomainOut>("/storefront/store/domain/check", { method: "POST" });
}
