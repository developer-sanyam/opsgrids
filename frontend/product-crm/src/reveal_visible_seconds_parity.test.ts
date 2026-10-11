/**
 * The field says "Hides again in N seconds" from `@opsgrids/ui`; the hook's timer counts N from
 * `@opsgrids/client`. Neither package may import the other, so the two numbers are twins by
 * structure, and this is the test that keeps them twins.
 */
import { describe, expect, it } from "vitest";

import { DEFAULT_REVEAL_VISIBLE_SECONDS } from "@opsgrids/client";
import { REVEAL_VISIBLE_SECONDS } from "@opsgrids/ui";

describe("reveal visible seconds", () => {
  it("is the same number in the field's wording and the hook's timer", () => {
    expect(REVEAL_VISIBLE_SECONDS).toBe(DEFAULT_REVEAL_VISIBLE_SECONDS);
  });
});
