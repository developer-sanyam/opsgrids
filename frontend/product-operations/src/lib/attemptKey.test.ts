import { describe, expect, it } from "vitest";

import { attemptKey, newAttemptKeyring } from "./attemptKey";

describe("attemptKey", () => {
  it("reuses the key for the same request, so a retry replays instead of repeating", () => {
    const ring = newAttemptKeyring();
    const first = attemptKey(ring, { lines: [{ quantity: "5" }] });
    expect(attemptKey(ring, { lines: [{ quantity: "5" }] })).toBe(first);
  });

  it("mints a new key once the request itself changed", () => {
    const ring = newAttemptKeyring();
    const first = attemptKey(ring, { lines: [{ quantity: "5" }] });
    expect(attemptKey(ring, { lines: [{ quantity: "6" }] })).not.toBe(first);
  });
});
