/**
 * "My tasks": `worker/useMyTasks.ts` asks the server for only the caller's own live offers
 * (`assigned_to_me=true`, one request per live status) and still re-checks each offer client-side
 * (an `OFFERED` one can have expired before the sweep says so).
 */
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { makeOffer, makeTask, page, renderScreen, stubGateway } from "@/worker/testKit";
import type { WorkerState } from "@/worker/context";

import { HomePage } from "./Home";

const TASKS_URL = "GET /api/v1/operations/fulfilment/tasks";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function renderHome(byStatus: Record<string, () => Response> = {}, worker: Partial<WorkerState> | undefined = undefined) {
  const gateway = stubGateway({
    [TASKS_URL]: (call) => {
      const status = call.search.get("status") ?? "";
      const answer = byStatus[status];
      return Promise.resolve(answer ? answer() : json(page([])));
    },
  });
  renderScreen(<HomePage />, { at: "/", path: "/", grants: ["operations.fulfilment.task.read:ORG"], worker });
  return gateway;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HomePage", () => {
  it("empty: explains there are no tasks yet, honestly, once every status has answered", async () => {
    renderHome();

    await screen.findByText("No tasks yet");
  });

  it("populated: shows an offered task and an accepted one, offered first", async () => {
    const offered = makeTask({
      id: "task-offered",
      status: "OFFERED",
      offers: [makeOffer({ task_id: "task-offered", state: "OFFERED" })],
    });
    const accepted = makeTask({
      id: "task-accepted",
      status: "ACCEPTED",
      offers: [makeOffer({ task_id: "task-accepted", state: "ACCEPTED", responded_at: "2026-09-29T09:00:00Z" })],
    });
    renderHome({
      OFFERED: () => json(page([offered])),
      ACCEPTED: () => json(page([accepted])),
    });

    const links = await screen.findAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]?.getAttribute("href")).toBe("/tasks/task-offered");
    expect(links[1]?.getAttribute("href")).toBe("/tasks/task-accepted");
  });

  it("does not show a task offered to a different worker", async () => {
    const notMine = makeTask({
      id: "task-not-mine",
      status: "OFFERED",
      offers: [makeOffer({ task_id: "task-not-mine", worker_id: "someone-else", state: "OFFERED" })],
    });
    renderHome({ OFFERED: () => json(page([notMine])) });

    await screen.findByText("No tasks yet");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("error: shows the failure and offers a retry", async () => {
    renderHome({
      OFFERED: () => json({ title: "Server error", status: 500, detail: "The server had a problem." }, 500),
    });

    await screen.findByText("Couldn't load your tasks");
    expect(await screen.findByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("no-identity: names the fix, not a generic error", async () => {
    renderHome({}, { status: "no-identity", worker: null });

    await screen.findByText("This account has no worker record");
  });

  it("not-found: the account has no roster row in this organization", async () => {
    renderHome({}, { status: "not-found", worker: null });

    await screen.findByText("You're not on this warehouse's roster yet");
  });
});

describe("HomePage asks the server for only my tasks", () => {
  it("sends assigned_to_me on every request, and never asks for the org-wide board", async () => {
    const gateway = renderHome();
    await screen.findByText("No tasks yet");

    const requests = gateway.calls.filter((call) => call.key === TASKS_URL);
    expect(requests.map((call) => call.search.get("status")).sort()).toEqual(
      ["ACCEPTED", "EXCEPTION", "IN_PROGRESS", "OFFERED"],
    );
    for (const request of requests) {
      expect(request.search.get("assigned_to_me")).toBe("true");
    }
  });
});
