import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { json, makeLine, makeTask, renderScreen, stubGateway } from "@/worker/testKit";

import { PickConfirmPage } from "./PickConfirm";

afterEach(() => {
  vi.unstubAllGlobals();
});

const PICK_PATH = "/tasks/:taskId/lines/:lineId/pick";
const AT = "/tasks/task-1/lines/line-1/pick";
const GRANTS = ["operations.fulfilment.task.read:ORG", "operations.fulfilment.task.execute:OWN"];

describe("PickConfirmPage", () => {
  it("confirms a full pick and returns to the task once the server confirms it", async () => {
    const task = makeTask({ lines: [makeLine({ quantity: "10", picked: "0" })] });
    let pickBody: unknown;
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(task),
      "POST /api/v1/operations/fulfilment/tasks/task-1/lines/line-1/pick": (call) => {
        pickBody = call.body;
        return json(makeTask({ lines: [makeLine({ quantity: "10", picked: "10" })] }));
      },
    });
    renderScreen(<PickConfirmPage />, { at: AT, path: PICK_PATH, grants: GRANTS });

    await screen.findByText("10 remaining on this line");
    await userEvent.click(screen.getByRole("button", { name: "Confirm pick" }));

    await waitFor(() => expect(pickBody).toEqual({ quantity: "10", note: "" }));
  });

  it("a short entry requires a reason before it can be submitted", async () => {
    const task = makeTask({ lines: [makeLine({ quantity: "10", picked: "0" })] });
    let pickCalls = 0;
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(task),
      "POST /api/v1/operations/fulfilment/tasks/task-1/lines/line-1/pick": () => {
        pickCalls += 1;
        return json(task);
      },
    });
    renderScreen(<PickConfirmPage />, { at: AT, path: PICK_PATH, grants: GRANTS });

    const quantity = await screen.findByLabelText("Quantity picked");
    await userEvent.clear(quantity);
    await userEvent.type(quantity, "6");
    await userEvent.click(screen.getByRole("button", { name: "Confirm short pick" }));

    expect(await screen.findByText("Say why the rest wasn't picked.")).toBeTruthy();
    expect(pickCalls).toBe(0);

    await userEvent.click(screen.getByRole("radio", { name: /Damaged/ }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm short pick" }));

    await waitFor(() => expect(pickCalls).toBe(1));
  });

  it("refuses a quantity greater than what remains, without calling the server", async () => {
    const task = makeTask({ lines: [makeLine({ quantity: "10", picked: "4" })] });
    let pickCalls = 0;
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(task),
      "POST /api/v1/operations/fulfilment/tasks/task-1/lines/line-1/pick": () => {
        pickCalls += 1;
        return json(task);
      },
    });
    renderScreen(<PickConfirmPage />, { at: AT, path: PICK_PATH, grants: GRANTS });

    const quantity = await screen.findByLabelText("Quantity picked");
    await userEvent.clear(quantity);
    await userEvent.type(quantity, "9");
    await userEvent.click(screen.getByRole("button", { name: "Confirm pick" }));

    expect(await screen.findByText("Only 6 remains on this line.")).toBeTruthy();
    expect(pickCalls).toBe(0);
  });

  it("a duplicate submit while the first request is in flight only reaches the server once", async () => {
    const task = makeTask({ lines: [makeLine({ quantity: "10", picked: "0" })] });
    let pickCalls = 0;
    let resolvePick: ((response: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      resolvePick = resolve;
    });
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(task),
      "POST /api/v1/operations/fulfilment/tasks/task-1/lines/line-1/pick": () => {
        pickCalls += 1;
        return pending;
      },
    });
    renderScreen(<PickConfirmPage />, { at: AT, path: PICK_PATH, grants: GRANTS });

    await screen.findByText("10 remaining on this line");
    const button = screen.getByRole("button", { name: "Confirm pick" });
    await userEvent.click(button);
    await userEvent.click(button);
    await userEvent.click(button);

    expect(pickCalls).toBe(1);
    resolvePick?.(json(makeTask({ lines: [makeLine({ quantity: "10", picked: "10" })] })));
  });

  it("a line already fully confirmed says so instead of offering the form again", async () => {
    const task = makeTask({ lines: [makeLine({ quantity: "10", picked: "10" })] });
    stubGateway({
      "GET /api/v1/operations/fulfilment/tasks/task-1": () => json(task),
    });
    renderScreen(<PickConfirmPage />, { at: AT, path: PICK_PATH, grants: GRANTS });

    expect(await screen.findByText("This line has already been confirmed")).toBeTruthy();
    expect(screen.queryByLabelText("Quantity picked")).toBeNull();
  });
});
