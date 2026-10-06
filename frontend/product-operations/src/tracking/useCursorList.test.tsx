import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import type { CursorPage } from "@/api/trackingTypes";
import { useCursorList } from "./useCursorList";

function page(items: string[], next: string | null): CursorPage<string> {
  return { items, next_cursor: next };
}

describe("useCursorList", () => {
  it("loads the first page without a cursor, then appends the next on the server's cursor", async () => {
    const fetchPage = vi
      .fn<(cursor: string | undefined) => Promise<CursorPage<string>>>()
      .mockResolvedValueOnce(page(["a", "b"], "c1"))
      .mockResolvedValueOnce(page(["c"], null));
    const { result } = renderHook(() => useCursorList((cursor) => fetchPage(cursor), []));

    expect(result.current.status).toBe("loading");
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(fetchPage).toHaveBeenLastCalledWith(undefined);
    expect(result.current.items).toEqual(["a", "b"]);
    expect(result.current.hasMore).toBe(true);

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.items).toEqual(["a", "b", "c"]));
    expect(fetchPage).toHaveBeenLastCalledWith("c1");
    expect(result.current.hasMore).toBe(false);
  });

  it("ignores a second Load more while one is in flight", async () => {
    let resolve: (value: CursorPage<string>) => void = () => undefined;
    const fetchPage = vi
      .fn<(cursor: string | undefined) => Promise<CursorPage<string>>>()
      .mockResolvedValueOnce(page(["a"], "c1"))
      .mockReturnValueOnce(new Promise((r) => (resolve = r)));
    const { result } = renderHook(() => useCursorList((cursor) => fetchPage(cursor), []));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    act(() => {
      result.current.loadMore();
      result.current.loadMore();
    });
    expect(fetchPage).toHaveBeenCalledTimes(2);
    expect(result.current.loadingMore).toBe(true);

    await act(async () => resolve(page(["b"], null)));
    expect(result.current.items).toEqual(["a", "b"]);
    expect(result.current.loadingMore).toBe(false);
  });

  it("reports a failed first page as an error with a retry that starts over", async () => {
    const fetchPage = vi
      .fn<(cursor: string | undefined) => Promise<CursorPage<string>>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(page(["a"], null));
    const { result } = renderHook(() => useCursorList((cursor) => fetchPage(cursor), []));

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error?.message).toBe("offline");

    act(() => result.current.reload());
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.items).toEqual(["a"]);
  });

  it("keeps the rows when Load more fails, and retries the same cursor", async () => {
    const fetchPage = vi
      .fn<(cursor: string | undefined) => Promise<CursorPage<string>>>()
      .mockResolvedValueOnce(page(["a"], "c1"))
      .mockRejectedValueOnce(new Error("slow"))
      .mockResolvedValueOnce(page(["b"], null));
    const { result } = renderHook(() => useCursorList((cursor) => fetchPage(cursor), []));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.moreError?.message).toBe("slow"));
    expect(result.current.status).toBe("ready");
    expect(result.current.items).toEqual(["a"]);
    expect(result.current.hasMore).toBe(true);

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.items).toEqual(["a", "b"]));
    expect(fetchPage).toHaveBeenLastCalledWith("c1");
    expect(result.current.moreError).toBeNull();
  });

  it("starts over when the filters change, and drops the old filter's late response", async () => {
    let resolveOld: (value: CursorPage<string>) => void = () => undefined;
    const fetchPage = vi
      .fn<(filter: string) => Promise<CursorPage<string>>>()
      .mockReturnValueOnce(new Promise((r) => (resolveOld = r)))
      .mockResolvedValueOnce(page(["new"], null));
    const { result, rerender } = renderHook(
      ({ filter }) => useCursorList(() => fetchPage(filter), [filter]),
      { initialProps: { filter: "old" } },
    );

    rerender({ filter: "new" });
    await waitFor(() => expect(result.current.items).toEqual(["new"]));

    await act(async () => resolveOld(page(["old"], null)));
    expect(result.current.items).toEqual(["new"]);
  });

  it("is ready and empty, not loading, for a list with no rows", async () => {
    const { result } = renderHook(() => useCursorList(() => Promise.resolve(page([], null)), []));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.items).toEqual([]);
    expect(result.current.hasMore).toBe(false);
  });
});
