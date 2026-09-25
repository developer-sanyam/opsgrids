/**
 * The two hooks the design system exports for apps: the debounce ten apps copied, and the
 * browser's leave-page prompt.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { useDebouncedValue } from "./useDebouncedValue";
import { useUnsavedChangesWarning } from "./useUnsavedChangesWarning";

afterEach(() => {
  vi.useRealTimers();
});

describe("useDebouncedValue", () => {
  it("holds a burst of changes back and settles on the last one", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 300), {
      initialProps: { value: "a" },
    });
    rerender({ value: "ac" });
    rerender({ value: "acm" });
    act(() => vi.advanceTimersByTime(299));
    expect(result.current).toBe("a");
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe("acm");
  });
});

describe("useUnsavedChangesWarning", () => {
  function fireBeforeUnload(): Event {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event;
  }

  it("asks the browser to confirm only while there is something to lose", () => {
    const { rerender, unmount } = renderHook(({ dirty }) => useUnsavedChangesWarning(dirty), {
      initialProps: { dirty: false },
    });
    expect(fireBeforeUnload().defaultPrevented).toBe(false);

    rerender({ dirty: true });
    expect(fireBeforeUnload().defaultPrevented).toBe(true);

    rerender({ dirty: false });
    expect(fireBeforeUnload().defaultPrevented).toBe(false);
    unmount();
  });

  it("takes its listener with it when the form goes", () => {
    const { unmount } = renderHook(() => useUnsavedChangesWarning(true));
    unmount();
    expect(fireBeforeUnload().defaultPrevented).toBe(false);
  });
});
