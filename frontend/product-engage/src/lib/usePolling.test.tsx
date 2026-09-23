import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import { usePolling } from "./usePolling";

/**
 * The two behaviours `CampaignRecipients` asks one hook for: poll while a campaign is sending,
 * read once when it is not — and never keep hammering an endpoint that has said 403.
 */

afterEach(() => {
  vi.useRealTimers();
});

describe("usePolling", () => {
  it("polls on the interval while one is given", async () => {
    vi.useFakeTimers();
    const loader = vi.fn().mockResolvedValue("rows");
    const { result } = renderHook(() => usePolling(loader, [], 1_000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.status).toBe("success");
    expect(loader).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("reads exactly once, and never again, when the interval is null", async () => {
    vi.useFakeTimers();
    const loader = vi.fn().mockResolvedValue("rows");
    const { result } = renderHook(() => usePolling(loader, [], null));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.status).toBe("success");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000);
    });
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("stops polling for good on a 403", async () => {
    vi.useFakeTimers();
    const loader = vi.fn().mockRejectedValue(new ApiError(403, "Forbidden", null));
    const { result } = renderHook(() => usePolling(loader, [], 1_000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.status).toBe("error");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("keeps the last good data, marked stopped, when a later poll is refused with a 403", async () => {
    vi.useFakeTimers();
    const loader = vi
      .fn()
      .mockResolvedValueOnce({ sent: 60 })
      .mockRejectedValueOnce(new ApiError(403, "Forbidden", null));
    const { result } = renderHook(() => usePolling(loader, [], 1_000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.stopped).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    // The figures that were true a minute ago are not replaced by nothing...
    expect(result.current.status).toBe("success");
    expect(result.current.data).toEqual({ sent: 60 });
    // ...and the screen is told they are no longer being kept current.
    expect(result.current.stopped).toBe(true);
    expect(result.current.isRefreshing).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("does not mark a transient failure stopped, and keeps polling", async () => {
    vi.useFakeTimers();
    const loader = vi
      .fn()
      .mockResolvedValueOnce("first")
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue("third");
    const { result } = renderHook(() => usePolling(loader, [], 1_000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(result.current.stopped).toBe(false);
    // Backed off to twice the interval after the failure, then recovered.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(result.current.data).toBe("third");
    expect(result.current.stopped).toBe(false);
  });

  it("keeps the last good rows when a later poll fails", async () => {
    vi.useFakeTimers();
    const loader = vi
      .fn()
      .mockResolvedValueOnce("first")
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const { result } = renderHook(() => usePolling(loader, [], 1_000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(loader).toHaveBeenCalledTimes(2);
    expect(result.current.status).toBe("success");
    expect(result.current.data).toBe("first");
  });
});
