import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, renderHook, screen } from "@testing-library/react";

import { SlowLoadNotice } from "@/ui/SlowLoadNotice";
import { SLOW_LOAD_MS, useSlowLoad } from "./useSlowLoad";

describe("useSlowLoad", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("flips to slow only after the load has run 8 seconds without a break", () => {
    expect(SLOW_LOAD_MS).toBe(8000);
    const { result } = renderHook(() => useSlowLoad(true));
    expect(result.current).toBe(false);
    act(() => {
      vi.advanceTimersByTime(7999);
    });
    expect(result.current).toBe(false);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe(true);
  });

  it("never says slow for a load that finished in time, and resets when the load ends", () => {
    const { result, rerender } = renderHook(({ active }) => useSlowLoad(active), { initialProps: { active: true } });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    rerender({ active: false });
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(result.current).toBe(false);

    rerender({ active: true });
    act(() => {
      vi.advanceTimersByTime(8000);
    });
    expect(result.current).toBe(true);
    rerender({ active: false });
    expect(result.current).toBe(false);
  });

  it("is quiet when the load is not running", () => {
    const { result } = renderHook(() => useSlowLoad(false));
    act(() => {
      vi.advanceTimersByTime(20_000);
    });
    expect(result.current).toBe(false);
  });
});

describe("SlowLoadNotice", () => {
  it("is an empty polite region until shown, then says what is slow", () => {
    const { rerender } = render(<SlowLoadNotice show={false} what="the floor plan" />);
    const region = screen.getByRole("status");
    expect(region.getAttribute("aria-live")).toBe("polite");
    expect(region.textContent).toBe("");
    rerender(<SlowLoadNotice show what="the floor plan" />);
    expect(screen.getByRole("status").textContent).toContain("Loading the floor plan is taking longer than usual");
  });
});
