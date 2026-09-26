import { useEffect } from "react";

import type { ImportPhase } from "@opsgrids/client";

/**
 * Reloads a screen's list once an import has finished. The dialog stays open on its result, so
 * without this the rows it just created would only appear after the person closed it and
 * refreshed. A cancelled import also ends in `done` and may have created rows before it stopped.
 */
export function useReloadAfterImport(phase: ImportPhase, reload: () => void): void {
  useEffect(() => {
    if (phase === "done") reload();
  }, [phase, reload]);
}
