import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { json, makeOffer, makeTask, renderScreen, stubGateway } from "@/worker/testKit";

import { TaskDetailPage } from "./TaskDetail";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TaskDetailPage", () => {
  it("permission-denied: a caller with no task.read grant sees the denial, not the task", async () => {
    stubGateway({});
    renderScreen(<TaskDetailPage />, { at: "/tasks/task-1", path: "/tasks/:taskId", grants: [] });

    expect(await screen.findByText("You can't open this task right now")).toBeTruthy();
  });

  it("shows an offer and lets the worker accept it, then reflects the accepted state from the server", async () => {
    const offer = makeOffer({ state: "OFFERED" });
    const offered = makeTask({ status: "OFFERED", offers: [offer] });
    const accepted = makeTask({ status: "ACCEPTED", offers: [makeOffer({ state: "ACCEPTED", responded_at: "2026-09-29T09:05:00Z" })] });
    let getCount = 0;
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => {
        getCount += 1;
        return json(getCount === 1 ? offered : accepted);
      },
      "POST /api/v1/operations/fulfilment/offers/offer-1/accept": () => json({ ...offer, state: "ACCEPTED" }),
    });
    renderScreen(<TaskDetailPage />, {
      at: "/tasks/task-1",
      path: "/tasks/:taskId",
      grants: ["operations.fulfilment.task.read:ORG", "operations.fulfilment.task.execute:OWN"],
    });

    await screen.findByText("You've been offered this task.");
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));

    // The screen re-fetches the task rather than trusting the offer response — never trust
    // client-side task state as the source of truth.
    await waitFor(() => expect(getCount).toBe(2));
    await screen.findByText("Scan");
  });

  it("declining an offer is a separate action from accepting", async () => {
    const offer = makeOffer({ state: "OFFERED" });
    const offered = makeTask({ status: "OFFERED", offers: [offer] });
    let acceptCalls = 0;
    let declineCalls = 0;
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(offered),
      "POST /api/v1/operations/fulfilment/offers/offer-1/accept": () => {
        acceptCalls += 1;
        return json({ ...offer, state: "ACCEPTED" });
      },
      "POST /api/v1/operations/fulfilment/offers/offer-1/decline": () => {
        declineCalls += 1;
        return json({ ...offer, state: "DECLINED" });
      },
    });
    renderScreen(<TaskDetailPage />, {
      at: "/tasks/task-1",
      path: "/tasks/:taskId",
      grants: ["operations.fulfilment.task.read:ORG", "operations.fulfilment.task.execute:OWN"],
    });

    await screen.findByText("You've been offered this task.");
    await userEvent.click(screen.getByRole("button", { name: "Decline" }));

    await waitFor(() => expect(declineCalls).toBe(1));
    expect(acceptCalls).toBe(0);
  });

  it("shows an open exception and lets the worker resolve it to resume picking", async () => {
    const exception = {
      id: "exc-1",
      task_id: "task-1",
      task_line_id: "line-1",
      reason: "short_pick" as const,
      quantity: "2",
      note: "Only 8 on the shelf.",
      status: "open" as const,
      raised_at: "2026-09-29T09:00:00Z",
      resolved_at: null,
    };
    const withException = makeTask({ status: "EXCEPTION", exceptions: [exception] });
    const resolved = makeTask({ status: "IN_PROGRESS", exceptions: [] });
    let getCount = 0;
    let resolveBody: unknown;
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => {
        getCount += 1;
        return json(getCount === 1 ? withException : resolved);
      },
      "POST /api/v1/operations/fulfilment/tasks/task-1/exceptions/exc-1/resolve": (call) => {
        resolveBody = call.body;
        return json(resolved);
      },
    });
    renderScreen(<TaskDetailPage />, {
      at: "/tasks/task-1",
      path: "/tasks/:taskId",
      grants: ["operations.fulfilment.task.read:ORG", "operations.fulfilment.task.execute:OWN"],
    });

    await screen.findByText("Only 8 on the shelf.");
    await userEvent.click(screen.getByRole("button", { name: "Resolve and resume picking" }));

    await waitFor(() => expect(resolveBody).toEqual({ note: "" }));
    expect(await screen.findByText("Order 11111111")).toBeTruthy();
  });

  it("a task not part of this organization is a clear not-found, not a generic failure", async () => {
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () =>
        json({ title: "Not found", status: 404, detail: "That task is not part of this organization" }, 404),
    });
    renderScreen(<TaskDetailPage />, {
      at: "/tasks/task-1",
      path: "/tasks/:taskId",
      grants: ["operations.fulfilment.task.read:ORG"],
    });

    expect(await screen.findByText("That task isn't available")).toBeTruthy();
  });
});
