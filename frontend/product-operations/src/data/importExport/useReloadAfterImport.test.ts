import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import type { ImportPhase } from "@opsgrids/client";

import { useReloadAfterImport } from "./useReloadAfterImport";

function setup(initial: ImportPhase) {
  const reload = vi.fn();
  const view = renderHook(({ phase }) => useReloadAfterImport(phase, reload), {
    initialProps: { phase: initial },
  });
  return { reload, ...view };
}

describe("useReloadAfterImport", () => {
  it("leaves the list alone while the dialog is picking, reading, previewing or importing", () => {
    const { reload, rerender } = setup("menu");
    for (const phase of ["reading", "preview", "importing", "error"] as const) rerender({ phase });
    expect(reload).not.toHaveBeenCalled();
  });

  it("reloads the list once when an import finishes", () => {
    const { reload, rerender } = setup("importing");
    rerender({ phase: "done" });
    expect(reload).toHaveBeenCalledTimes(1);
    rerender({ phase: "done" });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("reloads again after the next import", () => {
    const { reload, rerender } = setup("done");
    expect(reload).toHaveBeenCalledTimes(1);
    rerender({ phase: "menu" });
    rerender({ phase: "importing" });
    rerender({ phase: "done" });
    expect(reload).toHaveBeenCalledTimes(2);
  });
});
