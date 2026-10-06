import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";

import { useIdempotencyKey } from "./idempotency";

describe("useIdempotencyKey", () => {
  it("gives the same body the same key, so a retry replays", () => {
    const { result } = renderHook(() => useIdempotencyKey());
    const first = result.current({ quantity: "5" });
    expect(first).toMatch(/^[0-9a-f-]{36}$/);
    expect(result.current({ quantity: "5" })).toBe(first);
  });

  it("gives a corrected body a new key", () => {
    const { result } = renderHook(() => useIdempotencyKey());
    expect(result.current({ quantity: "5" })).not.toBe(result.current({ quantity: "6" }));
  });

  it("keeps the key across renders but not across dialogs", () => {
    const one = renderHook(() => useIdempotencyKey());
    const key = one.result.current({ a: 1 });
    one.rerender();
    expect(one.result.current({ a: 1 })).toBe(key);
    const other = renderHook(() => useIdempotencyKey());
    expect(other.result.current({ a: 1 })).not.toBe(key);
  });
});
