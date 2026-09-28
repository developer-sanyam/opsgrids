import { afterEach, describe, expect, it } from "vitest";

import { dequeueScan, enqueueScan, queuedScansForTask } from "./scanQueue";

afterEach(() => {
  localStorage.clear();
});

describe("scanQueue", () => {
  it("keeps a scan queued for its own task, oldest first", () => {
    enqueueScan("task-1", { barcode: "SKU-1" });
    enqueueScan("task-1", { barcode: "SKU-2" });
    enqueueScan("task-2", { barcode: "SKU-3" });

    const forTask1 = queuedScansForTask("task-1");
    expect(forTask1.map((entry) => entry.payload.barcode)).toEqual(["SKU-1", "SKU-2"]);
    expect(queuedScansForTask("task-2")).toHaveLength(1);
  });

  it("removes exactly the dequeued entry", () => {
    const first = enqueueScan("task-1", { barcode: "SKU-1" });
    const second = enqueueScan("task-1", { barcode: "SKU-2" });

    dequeueScan(first.id);

    const remaining = queuedScansForTask("task-1");
    expect(remaining).toHaveLength(1);
    expect(remaining[0]?.id).toBe(second.id);
  });

  it("survives being read back after a reload — it is `localStorage`, not memory", () => {
    enqueueScan("task-1", { barcode: "SKU-1", location_id: "loc-1" });

    expect(queuedScansForTask("task-1")[0]?.payload).toEqual({ barcode: "SKU-1", location_id: "loc-1" });
  });
});
