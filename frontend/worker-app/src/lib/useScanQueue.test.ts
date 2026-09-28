import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { enqueueScan, queuedScansForTask } from "./scanQueue";
import { useScanQueue } from "./useScanQueue";

vi.mock("@/api/client", () => ({ scanTask: vi.fn() }));

import { scanTask } from "@/api/client";

const scanTaskMock = vi.mocked(scanTask);

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("useScanQueue", () => {
  it("replays a scan left over from a previous session once mounted online", async () => {
    enqueueScan("task-1", { barcode: "SKU-1" });
    scanTaskMock.mockResolvedValue({} as never);

    const { result } = renderHook(() => useScanQueue("task-1"));

    expect(result.current.pending).toBe(1);
    await waitFor(() => expect(result.current.pending).toBe(0));
    expect(scanTaskMock).toHaveBeenCalledWith("task-1", { barcode: "SKU-1" });
    expect(queuedScansForTask("task-1")).toHaveLength(0);
  });

  it("a failed replay leaves the scan queued, not dropped", async () => {
    enqueueScan("task-1", { barcode: "SKU-1" });
    scanTaskMock.mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useScanQueue("task-1"));

    await waitFor(() => expect(result.current.replayError).not.toBeNull());
    expect(result.current.pending).toBe(1);
    expect(queuedScansForTask("task-1")).toHaveLength(1);
  });

  it("enqueue adds to the same task's queue and updates the pending count", async () => {
    scanTaskMock.mockImplementation(() => new Promise(() => undefined));
    const { result } = renderHook(() => useScanQueue("task-1"));
    await waitFor(() => expect(result.current.pending).toBe(0));

    act(() => {
      result.current.enqueue({ barcode: "SKU-2" });
    });

    expect(result.current.pending).toBe(1);
  });
});
