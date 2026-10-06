/**
 * The drainer's own behaviour — when it runs, how often, one pass at a time, and for whom — through
 * the real api client and queue, with only the network stubbed. What each answer means for an entry
 * is `putawaySync.test.ts`; what a worker sees on the Put away screen is `Putaway.test.tsx`.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@opsgrids/ui";

import type { PutawayIn } from "@/api/types";
import { json, makePutawayOut, problem, stubGateway } from "@/worker/testKit";
import type { Call } from "@/worker/testKit";

import { PutawayQueueProvider } from "./PutawayQueueProvider";
import { enqueuePutaway, getPutawayQueue, purgePutawayQueue } from "./putawayQueue";
import { usePutawayQueue } from "./putawayQueueContext";

const PUTAWAY = "POST /api/v1/operations/inventory/putaway";

const SUMMARY = { sku: "SKU-100", itemName: "Cotton roll", uom: "m", fromLocationCode: "RCV-01", toBarcode: "A-01-03" };

function seed(owner = "user-1", quantity = "3") {
  const body: PutawayIn = {
    inventory_item_id: "item-1",
    from_location_id: "loc-rcv-1",
    to_barcode: "A-01-03",
    quantity,
    captured_at: "2026-09-28T10:00:00.000Z",
    occurred_on: "2026-09-28",
  };
  return enqueuePutaway({ ownerSub: owner, organizationId: "org-1", body, summary: SUMMARY });
}

function Probe() {
  const queue = usePutawayQueue();
  return (
    <div>
      <p>pending={queue.pending}</p>
      <p>attention={queue.attention.length}</p>
      <p>syncing={String(queue.syncing)}</p>
      <button type="button" onClick={queue.syncNow}>
        sync now
      </button>
    </div>
  );
}

function mount(ownerSub = "user-1") {
  const tree = (owner: string) => (
    <Toaster>
      <PutawayQueueProvider ownerSub={owner} organizationId="org-1">
        <Probe />
      </PutawayQueueProvider>
    </Toaster>
  );
  const view = render(tree(ownerSub));
  return { ...view, as: (owner: string) => view.rerender(tree(owner)) };
}

const posts = (calls: Call[]) => calls.filter((call) => call.key === PUTAWAY);

/** Real timers for `waitFor`, but `setTimeout` faked so a 15 s backoff can be stepped through. */
function fakeTimeouts() {
  vi.useRealTimers();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  window.scrollTo = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  purgePutawayQueue();
  localStorage.clear();
});

describe("PutawayQueueProvider", () => {
  it("retries by itself while the server keeps failing: 15 s, then 30 s, with the very same key each time", async () => {
    fakeTimeouts();
    const entry = seed();
    let answers = [503, 503, 201];
    const { calls } = stubGateway({
      [PUTAWAY]: () => {
        const status = answers.shift() ?? 201;
        return status === 201 ? json(makePutawayOut(), 201) : problem(status, "Service unavailable");
      },
    });
    mount();

    await advance(0);
    expect(posts(calls)).toHaveLength(1);

    await advance(14_999);
    expect(posts(calls)).toHaveLength(1);
    await advance(1);
    expect(posts(calls)).toHaveLength(2);

    await advance(29_999);
    expect(posts(calls)).toHaveLength(2);
    await advance(1);
    expect(posts(calls)).toHaveLength(3);

    expect(new Set(posts(calls).map((call) => call.headers["idempotency-key"]))).toEqual(new Set([entry.idempotencyKey]));
    expect(getPutawayQueue()).toEqual([]);

    // Nothing left to retry, so nothing is scheduled.
    answers = [];
    await advance(10 * 60_000);
    expect(posts(calls)).toHaveLength(3);
  });

  it("does not retry while the phone believes it is offline, and picks up the moment it is back", async () => {
    const online = vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    seed();
    const { calls } = stubGateway({ [PUTAWAY]: () => json(makePutawayOut(), 201) });
    mount();

    expect(screen.getByText("pending=1")).toBeTruthy();
    expect(calls).toHaveLength(0);

    online.mockReturnValue(true);
    window.dispatchEvent(new Event("online"));

    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    await waitFor(() => expect(screen.getByText("pending=0")).toBeTruthy());
  });

  it("one pass at a time: a manual sync while one is in flight does not put the entry on the wire twice", async () => {
    seed();
    let inFlight = 0;
    let peak = 0;
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { calls } = stubGateway({
      [PUTAWAY]: async () => {
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await gate;
        inFlight -= 1;
        return json(makePutawayOut(), 201);
      },
    });
    const user = userEvent.setup();
    mount();

    await waitFor(() => expect(inFlight).toBe(1));
    await user.click(screen.getByRole("button", { name: "sync now" }));
    await user.click(screen.getByRole("button", { name: "sync now" }));
    release?.();

    await waitFor(() => expect(screen.getByText("pending=0")).toBeTruthy());
    expect(peak).toBe(1);
    expect(posts(calls)).toHaveLength(1);
  });

  it("an app killed mid-request loses nothing: the entry was saved first, and the next launch resends the same key", async () => {
    const entry = seed();
    let abandon: (() => void) | undefined;
    stubGateway({
      [PUTAWAY]: () =>
        new Promise<Response>(() => {
          // The request never answers — the phone lost the signal, or the tab was closed.
          abandon = () => undefined;
        }),
    });
    const first = mount();
    await waitFor(() => expect(abandon).toBeDefined());
    first.unmount();

    expect(getPutawayQueue()).toEqual([entry]);

    const { calls } = stubGateway({ [PUTAWAY]: () => json(makePutawayOut({ replayed: true }), 200) });
    mount();

    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]?.headers["idempotency-key"]).toBe(entry.idempotencyKey);
    await waitFor(() => expect(getPutawayQueue()).toEqual([]));
  });

  it("signing in as someone else replays only that person's entries", async () => {
    const mine = seed("user-1", "1");
    const theirs = seed("user-2", "2");
    const { calls } = stubGateway({ [PUTAWAY]: () => json(makePutawayOut(), 201) });
    const view = mount("user-1");

    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(posts(calls)[0]?.headers["idempotency-key"]).toBe(mine.idempotencyKey);
    expect(getPutawayQueue().map((e) => e.idempotencyKey)).toEqual([theirs.idempotencyKey]);

    view.as("user-2");

    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[1]?.headers["idempotency-key"]).toBe(theirs.idempotencyKey);
  });

  it("a refusal on a background replay parks the entry, surfaces it, and the next one still goes", async () => {
    const refused = seed("user-1", "1");
    seed("user-1", "2");
    let n = 0;
    const { calls } = stubGateway({
      [PUTAWAY]: () => {
        n += 1;
        return n === 1 ? problem(409, "That stock is reserved.") : json(makePutawayOut(), 201);
      },
    });
    mount();

    await waitFor(() => expect(screen.getByText("attention=1")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("pending=0")).toBeTruthy());
    expect(posts(calls)).toHaveLength(2);
    expect(getPutawayQueue()).toHaveLength(1);
    expect(getPutawayQueue()[0]).toMatchObject({ idempotencyKey: refused.idempotencyKey, status: "needs_attention" });
  });

  it("a lapsed session stops the replay without parking or discarding anything", async () => {
    seed();
    const { calls } = stubGateway({
      [PUTAWAY]: () => problem(401, "Token expired"),
      "POST /api/v1/auth/refresh": () => problem(401, "No session"),
    });
    mount();

    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    await waitFor(() => expect(screen.getByText("syncing=false")).toBeTruthy());
    expect(screen.getByText("pending=1")).toBeTruthy();
    expect(screen.getByText("attention=0")).toBeTruthy();
  });

  it("tells the worker, with a toast, about put-aways that synced while they were busy elsewhere", async () => {
    seed("user-1", "1");
    seed("user-1", "2");
    stubGateway({ [PUTAWAY]: () => json(makePutawayOut(), 201) });
    mount();

    expect(await screen.findByText("2 queued put-aways synced.")).toBeTruthy();
  });

  it("unmounting stops everything: no timer fires and nothing is sent afterwards", async () => {
    fakeTimeouts();
    seed();
    const { calls } = stubGateway({ [PUTAWAY]: () => problem(503, "down") });
    const view = mount();
    await advance(0);
    expect(posts(calls)).toHaveLength(1);

    view.unmount();
    await advance(10 * 60_000);

    expect(posts(calls)).toHaveLength(1);
    expect(getPutawayQueue()).toHaveLength(1);
  });
});
