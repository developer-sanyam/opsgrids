import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import * as api from "@/api/automations";
import { AUTOMATION_ACTIVE, RUN_DETAIL_FAILED, TRIGGERS, page } from "@/api/automations.fixtures";
import { routes } from "./index";

/**
 * The route list the orchestrator mounts: the five paths the contract names, each of which loads
 * its screen on demand and shows a placeholder until it arrives.
 */

afterEach(() => {
  vi.restoreAllMocks();
});

describe("routes", () => {
  it("names the five automation paths, with the specific ones ahead of the id", () => {
    const paths = routes.map((r) => r.path);
    expect(paths).toEqual([
      "/automations",
      "/automations/new",
      "/automations/analytics",
      "/automations/:automationId",
      "/automations/:automationId/runs/:runId",
    ]);
    expect(paths.indexOf("/automations/analytics")).toBeLessThan(paths.indexOf("/automations/:automationId"));
    expect(routes[0]?.nav).toEqual({ label: "Automations", group: "Communication" });
  });

  it("shows a placeholder, then the list, when the list route opens", async () => {
    vi.spyOn(api, "listTriggers").mockResolvedValue(TRIGGERS);
    vi.spyOn(api, "listAutomations").mockResolvedValue(page([AUTOMATION_ACTIVE]));
    const router = createMemoryRouter(routes.map((r) => ({ path: r.path, element: r.element })), {
      initialEntries: ["/automations"],
    });
    render(<RouterProvider router={router} />);
    expect(screen.getByRole("status").textContent).toBe("Loading");
    expect(await screen.findByRole("heading", { name: "Automations" })).toBeTruthy();
    expect(await screen.findByText("Invoice Payment Reminder")).toBeTruthy();
  });

  it("opens a run at its own path", async () => {
    vi.spyOn(api, "getRun").mockResolvedValue(RUN_DETAIL_FAILED);
    const router = createMemoryRouter(routes.map((r) => ({ path: r.path, element: r.element })), {
      initialEntries: [`/automations/${AUTOMATION_ACTIVE.id}/runs/${RUN_DETAIL_FAILED.id}`],
    });
    render(<RouterProvider router={router} />);
    expect(await screen.findByRole("heading", { name: "Steps" })).toBeTruthy();
  });
});
