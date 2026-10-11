import { describe, expect, it } from "vitest";

import { createSubmissionKeys } from "./useSubmissionKeys";

describe("createSubmissionKeys", () => {
  it("gives the same body the same key, and a changed body a fresh one", () => {
    const keyFor = createSubmissionKeys();
    const first = keyFor({ quantity: "5" });
    expect(keyFor({ quantity: "5" })).toBe(first);
    expect(keyFor({ quantity: "6" })).not.toBe(first);
  });

  it("does not share keys between two instances", () => {
    expect(createSubmissionKeys()({ a: 1 })).not.toBe(createSubmissionKeys()({ a: 1 }));
  });
});
