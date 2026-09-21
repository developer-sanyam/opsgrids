import { describe, expect, it } from "vitest";

import { CONNECTION, DOMAINS, SENDERS } from "./fixtures";

/**
 * The fixtures are design-time data, which makes them the realistic path for a credential to
 * reach a screen: a fabricated `secret_hint` here becomes a field somebody renders, and the
 * rendering survives the flag flip even though the service never sends one.
 *
 * `services/notification` asserts `ConnectionOut`'s shape key-for-key for the same reason. This is
 * the browser's half of that check.
 */

describe("the fixture connection", () => {
  it("carries exactly the six keys the service publishes", () => {
    expect(Object.keys(CONNECTION).sort()).toEqual([
      "daily_cap",
      "host",
      "kind",
      "port",
      "status",
      "username",
    ]);
  });
});

describe("the fixture domains", () => {
  it("covers a verified domain and one still waiting on DNS", () => {
    expect(DOMAINS.map((d) => d.status).sort()).toEqual(["pending", "verified"]);
  });

  it("generates the same five records, in the same order, as the service does", () => {
    for (const domain of DOMAINS) {
      expect(domain.records.map((r) => r.kind)).toEqual([
        "spf",
        "dkim",
        "dmarc",
        "return_path",
        "tracking",
      ]);
    }
  });

  it("marks DMARC as the only record a domain can send without", () => {
    for (const domain of DOMAINS) {
      const optional = domain.records.filter((r) => !r.required).map((r) => r.kind);
      expect(optional).toEqual(["dmarc"]);
    }
  });

  it("folds each status from its required records, as `fold_domain_status` does", () => {
    for (const domain of DOMAINS) {
      const required = domain.records.filter((r) => r.required);
      const allPublished = required.length > 0 && required.every((r) => r.verified);
      expect(domain.status).toBe(allPublished ? "verified" : "pending");
    }
  });
});

describe("the fixture senders", () => {
  it("offers one sender a campaign may use and one it may not", () => {
    expect(SENDERS.map((s) => s.usable).sort()).toEqual([false, true]);
  });

  it("keeps `usable` in step with `verified_at`, which is what derives it", () => {
    for (const sender of SENDERS) {
      expect(sender.usable).toBe(sender.verified_at !== null);
    }
  });

  it("puts every sender on a domain that exists", () => {
    const ids = new Set(DOMAINS.map((d) => d.id));
    for (const sender of SENDERS) {
      expect(ids.has(sender.domain_id)).toBe(true);
    }
  });
});
