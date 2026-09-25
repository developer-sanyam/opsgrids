/**
 * The three hooks the design system exports for apps: the debounce ten apps copied, the browser's
 * leave-page prompt, and the unsaved-changes comparison every dirty check is built from.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { useDebouncedValue } from "./useDebouncedValue";
import { useFormDirty } from "./useFormDirty";
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

describe("useFormDirty", () => {
  it("is clean when current equals the initial snapshot by value, not by reference", () => {
    const { result } = renderHook(() =>
      useFormDirty({ name: "Acme", tags: ["a", "b"] }, { name: "Acme", tags: ["a", "b"] }),
    );
    expect(result.current).toBe(false);
  });

  it("turns dirty the moment a field, including one nested, differs from the snapshot", () => {
    const { rerender, result } = renderHook(
      ({ current }) => useFormDirty({ name: "Acme", address: { city: "Pune" } }, current),
      { initialProps: { current: { name: "Acme", address: { city: "Pune" } } } },
    );
    expect(result.current).toBe(false);
    rerender({ current: { name: "Acme", address: { city: "Mumbai" } } });
    expect(result.current).toBe(true);
  });

  it("goes clean again once typed text is deleted back to what the form opened with", () => {
    const { rerender, result } = renderHook(({ current }) => useFormDirty("Acme Industries", current), {
      initialProps: { current: "Acme Industries" },
    });
    rerender({ current: "Acme Industries Ltd" });
    expect(result.current).toBe(true);
    rerender({ current: "Acme Industries" });
    expect(result.current).toBe(false);
  });
});
