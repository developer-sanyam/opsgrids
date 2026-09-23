import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import { usePolling } from "./usePolling";

/**
 * Two polls can be in flight at once — the timer's, and the one a tab becoming visible again
 * fires — and nothing orders their answers. And a `refresh` is not a change of what is read, so it
 * must not blank what a stopped poll kept.
 */

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => {};
  let reject: (error: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("answers that arrive out of order", () => {
  it("drops the older poll's answer when a newer poll has already been answered", async () => {
    const slow = deferred<string>();
    const fast = deferred<string>();
    const loader = vi.fn().mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);
    const { result } = renderHook(() => usePolling(loader, [], 60_000));

    // The first poll is on the wire and slow. The tab is hidden and shown again: a second poll.
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(loader).toHaveBeenCalledTimes(2);

    // The second poll is answered first...
    await act(async () => {
      fast.resolve("new");
    });
    expect(result.current.data).toBe("new");

    // ...and the first, older one arrives last. It must not put old data back over new.
    await act(async () => {
      slow.resolve("old");
    });
    expect(result.current.data).toBe("new");
    expect(result.current.status).toBe("success");
  });

  it("drops an older poll's failure too, so a stale error cannot mark fresh data stopped", async () => {
    const slow = deferred<string>();
    const fast = deferred<string>();
    const loader = vi.fn().mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);
    const { result } = renderHook(() => usePolling(loader, [], 60_000));

    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await act(async () => {
      fast.resolve("new");
    });
    await act(async () => {
      slow.reject(new ApiError(403, "Forbidden", null));
    });

    expect(result.current.data).toBe("new");
    expect(result.current.stopped).toBe(false);
  });

  it("still applies answers that arrive in order", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const loader = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => usePolling(loader, [], 60_000));

    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await act(async () => {
      first.resolve("first");
    });
    expect(result.current.data).toBe("first");
    await act(async () => {
      second.resolve("second");
    });
    expect(result.current.data).toBe("second");
  });
});

describe("refresh", () => {
  it("re-reads without clearing what is on screen, and marks it refreshing meanwhile", async () => {
    const next = deferred<string>();
    const loader = vi.fn().mockResolvedValueOnce("first").mockReturnValueOnce(next.promise);
    const { result } = renderHook(() => usePolling(loader, [], null));
    await act(async () => {});
    expect(result.current.data).toBe("first");

    act(() => result.current.refresh());

    // Not back to the loading skeleton: the data is still there while the answer is awaited.
    expect(result.current.status).toBe("success");
    expect(result.current.data).toBe("first");
    expect(loader).toHaveBeenCalledTimes(2);
    await act(async () => {
      next.resolve("second");
    });
    expect(result.current.data).toBe("second");
    expect(result.current.isRefreshing).toBe(false);
  });

  it("keeps what a stopped poll kept when the refresh is refused too", async () => {
    vi.useFakeTimers();
    const refused = new ApiError(403, "Forbidden", null);
    const loader = vi
      .fn()
      .mockResolvedValueOnce({ sent: 60 })
      .mockRejectedValueOnce(refused)
      .mockRejectedValueOnce(refused);
    const { result } = renderHook(() => usePolling(loader, [], 1_000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(result.current.stopped).toBe(true);
    expect(result.current.data).toEqual({ sent: 60 });

    await act(async () => {
      result.current.refresh();
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(loader).toHaveBeenCalledTimes(3);
    expect(result.current.status).toBe("success");
    expect(result.current.data).toEqual({ sent: 60 });
    expect(result.current.stopped).toBe(true);
  });

  it("clears stopped, and resumes polling, when a refresh the person asked for succeeds", async () => {
    vi.useFakeTimers();
    const loader = vi
      .fn()
      .mockResolvedValueOnce("first")
      .mockRejectedValueOnce(new ApiError(403, "Forbidden", null))
      .mockResolvedValue("again");
    const { result } = renderHook(() => usePolling(loader, [], 1_000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(result.current.stopped).toBe(true);

    await act(async () => {
      result.current.refresh();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.stopped).toBe(false);
    expect(result.current.data).toBe("again");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(loader.mock.calls.length).toBeGreaterThan(3);
  });

  it("still starts from a loading state when what is being read changes", async () => {
    const loader = vi.fn().mockResolvedValue("rows");
    const { result, rerender } = renderHook(({ id }) => usePolling(loader, [id], null), {
      initialProps: { id: 1 },
    });
    await act(async () => {});
    expect(result.current.status).toBe("success");

    rerender({ id: 2 });
    // A different thing is being read: the old data is not this one's, and is not shown.
    expect(result.current.status).toBe("loading");
    expect(result.current.data).toBeNull();
    await act(async () => {});
    expect(result.current.status).toBe("success");
  });
});
