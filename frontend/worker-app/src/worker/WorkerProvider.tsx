import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { decodeAccessToken, useAuth } from "@opsgrids/client";

import { FULL_PAGE, listWorkers } from "@/api/client";
import { WorkerContext } from "./context";
import type { WorkerState } from "./context";

const IDLE: Omit<WorkerState, "reload"> = { status: "loading", worker: null, error: null };

/**
 * Resolves "which `workers` row am I" once, for the whole app.
 *
 * There is no `GET /fulfilment/workers/me` and no `worker_id` filter on the task list
 * (`api/client.ts`'s own note on `listTasks`), so this is the one place that does the lookup: the
 * access token's `sref` claim (`decodeAccessToken().sref`, the same `subject_ref` the backend's
 * own `caller_worker` resolves from — `app/fulfilment/services.py`) matched against the org's
 * worker roster (`operations.fulfilment.worker.read:ORG`, which every `OPERATIONS_WORKER` holds).
 * Every screen that needs "my worker id" reads it from here rather than repeating the lookup.
 *
 * `"no-identity"` is a real, reachable state: an account with no `sref` claim (no linked employee
 * record) can still sign in to this app but holds no worker identity, and every task-execute call
 * would then fail server-side (`caller_worker` raises `ForbiddenProblem`). `"not-found"` is the
 * same fact one step later — an `sref` that names no row in this organization's roster.
 */
export function WorkerProvider({ children }: { children: ReactNode }) {
  const { status: authStatus } = useAuth();
  const [state, setState] = useState<Omit<WorkerState, "reload">>(IDLE);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (authStatus !== "authenticated") {
      setState(IDLE);
      return;
    }
    const sref = decodeAccessToken()?.sref;
    if (!sref) {
      setState({ status: "no-identity", worker: null, error: null });
      return;
    }
    let active = true;
    setState({ status: "loading", worker: null, error: null });
    listWorkers({ size: FULL_PAGE })
      .then((page) => {
        if (!active) return;
        const mine = page.items.find((w) => w.employee_id === sref) ?? null;
        setState({ status: mine ? "ready" : "not-found", worker: mine, error: null });
      })
      .catch((err: unknown) => {
        if (!active) return;
        setState({
          status: "error",
          worker: null,
          error: err instanceof Error ? err : new Error(String(err)),
        });
      });
    return () => {
      active = false;
    };
  }, [authStatus, nonce]);

  const value = useMemo(() => ({ ...state, reload }), [state, reload]);
  return <WorkerContext value={value}>{children}</WorkerContext>;
}
