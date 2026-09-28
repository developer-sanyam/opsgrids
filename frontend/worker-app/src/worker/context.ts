/**
 * The worker-identity context and its hook.
 *
 * Kept in a `.ts` file with no component export so React Fast Refresh stays happy — the provider
 * lives next door in `WorkerProvider.tsx`, the same split `@opsgrids/client` uses for
 * `auth/context.ts` / `AuthProvider.tsx` and `permissions/context.ts` / `PermissionProvider.tsx`.
 */
import { createContext, useContext } from "react";

import type { WorkerOut } from "@/api/types";

export type WorkerRecordStatus = "loading" | "ready" | "error" | "no-identity" | "not-found";

export interface WorkerState {
  status: WorkerRecordStatus;
  /** The caller's own `workers` row — `null` until `status` is `"ready"`. */
  worker: WorkerOut | null;
  error: Error | null;
  reload: () => void;
}

export const WorkerContext = createContext<WorkerState | null>(null);

export function useMyWorker(): WorkerState {
  const value = useContext(WorkerContext);
  if (value === null) throw new Error("useMyWorker must be used within a <WorkerProvider>");
  return value;
}
