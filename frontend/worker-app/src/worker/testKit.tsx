/**
 * Test-only harness for the fulfilment screens: a fake gateway keyed "METHOD /api/v1/path" (the
 * same shape `product-crm/src/reveal/testKit.tsx` uses), a worker identity dropped straight into
 * `WorkerContext` (skipping `WorkerProvider`'s own roster lookup — that lookup gets its own test),
 * and small fixture builders for the fulfilment wire shapes. The real api client, the real
 * `useMutation`/`useAsync` and the real screens run under it, so what a test sees is what crosses
 * the wire and what lands on screen.
 */
import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import { PermissionProvider } from "@opsgrids/client";

import type {
  AssignmentOfferOut,
  PackageOut,
  PickTaskLineOut,
  PickTaskOut,
  WorkerOut,
} from "@/api/types";
import { WorkerContext } from "@/worker/context";
import type { WorkerState } from "@/worker/context";

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

export function problem(status: number, detail: string, slug = "problem"): Response {
  return json({ type: `https://errors.opsgrids.dev/${slug}`, title: detail, status, detail }, status);
}

export function page<T>(items: T[]): { items: T[]; total: number; page: number; size: number; pages: number } {
  return { items, total: items.length, page: 1, size: items.length || 1, pages: items.length ? 1 : 0 };
}

export interface Call {
  key: string;
  search: URLSearchParams;
  body: unknown;
}

export type Answer = (call: Call) => Response | Promise<Response>;

/** Stubs `fetch`. A call to a route that was not declared fails the test loudly rather than
 *  hanging — the same choice `Login.test.tsx`'s own stub makes. */
export function stubGateway(routes: Record<string, Answer>): { calls: Call[] } {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const key = `${init?.method ?? "GET"} ${url.pathname}`;
      const call: Call = {
        key,
        search: url.searchParams,
        body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
      };
      calls.push(call);
      const route = routes[key];
      if (route === undefined) throw new Error(`No stub for ${key}`);
      return Promise.resolve(route(call));
    }),
  );
  return { calls };
}

const NOW = "2026-09-29T09:00:00.000Z";

export function makeWorker(overrides: Partial<WorkerOut> = {}): WorkerOut {
  return {
    id: "worker-1",
    employee_id: "employee-1",
    warehouse_id: "warehouse-1",
    display_name: "Asha Rao",
    is_active: true,
    skills: [],
    presence_status: "online",
    presence_last_seen_at: null,
    zone_location_ids: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

export function makeLine(overrides: Partial<PickTaskLineOut> = {}): PickTaskLineOut {
  return {
    id: "line-1",
    order_line_id: "order-line-1",
    inventory_item_id: "item-1",
    sku: "SKU-100",
    source_location_id: "loc-bin-1",
    destination_location_id: "loc-staging-1",
    quantity: "10",
    picked: "0",
    short: "0",
    sequence: 1,
    status: "pending",
    ...overrides,
  };
}

export function makeOffer(overrides: Partial<AssignmentOfferOut> = {}): AssignmentOfferOut {
  return {
    id: "offer-1",
    task_id: "task-1",
    worker_id: "worker-1",
    round: 1,
    state: "OFFERED",
    score_breakdown: {},
    offered_at: NOW,
    expires_at: new Date(Date.parse(NOW) + 5 * 60_000).toISOString(),
    responded_at: null,
    ...overrides,
  };
}

export function makeTask(overrides: Partial<PickTaskOut> = {}): PickTaskOut {
  return {
    id: "task-1",
    order_id: "11111111-2222-3333-4444-555555555555",
    warehouse_id: "warehouse-1",
    zone_location_id: null,
    status: "ACCEPTED",
    round: 1,
    max_rounds: 3,
    lines: [makeLine()],
    offers: [makeOffer({ state: "ACCEPTED", responded_at: NOW })],
    exceptions: [],
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

export function makePackage(overrides: Partial<PackageOut> = {}): PackageOut {
  return {
    id: "package-1",
    order_id: "11111111-2222-3333-4444-555555555555",
    packaging_material_id: null,
    destination_location_id: "loc-dock-1",
    status: "PACKED",
    weight_grams: null,
    lines: [],
    created_at: NOW,
    ...overrides,
  };
}

export const DEFAULT_WORKER_STATE: WorkerState = {
  status: "ready",
  worker: makeWorker(),
  error: null,
  reload: () => undefined,
};

export interface ScreenOptions {
  /** Where the router starts, e.g. "/tasks/task-1". */
  at: string;
  /** The route pattern the screen is mounted at, e.g. "/tasks/:taskId". */
  path: string;
  grants: string[];
  worker?: Partial<WorkerState> | undefined;
}

export function renderScreen(element: ReactElement, options: ScreenOptions) {
  const workerState: WorkerState = { ...DEFAULT_WORKER_STATE, ...options.worker };
  return render(
    <MemoryRouter initialEntries={[options.at]}>
      <PermissionProvider grants={options.grants}>
        <WorkerContext value={workerState}>
          <Routes>
            <Route path={options.path} element={element} />
            {/* Swallows a post-mutation `navigate()` to a screen this render did not mount —
                e.g. pick-confirm's own return to the task detail route — so the test's own
                assertions are what fails it, not a router console warning. */}
            <Route path="*" element={null} />
          </Routes>
        </WorkerContext>
      </PermissionProvider>
    </MemoryRouter>,
  );
}
