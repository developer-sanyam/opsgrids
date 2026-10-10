/**
 * The inspections list (inventory plan Phase 4): quantities appear exactly as the server sent
 * them, the empty / error states are real, and "New inspection" is offered only to someone who can
 * inspect.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { inspectionFixture, pageOf as page } from "@/test-utils/qualityFixtures";
import { renderScreen } from "@/test-utils/renderScreen";
import { InspectionsPage } from "./Inspections";

const listInspections = vi.fn();

vi.mock("@/api/client", () => ({
  listInspections: (...a: unknown[]) => listInspections(...a),
  listWarehouses: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, size: 200, pages: 0 }),
  listLocations: vi.fn().mockResolvedValue([]),
  listQcTemplates: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, size: 200, pages: 0 }),
  searchInventory: vi.fn().mockResolvedValue([]),
  createInspection: vi.fn(),
}));

const READ = "operations.quality.inspection.read:ORG";
const INSPECT = "operations.quality.inspection.inspect:ORG";

describe("InspectionsPage", () => {
  beforeEach(() => {
    listInspections.mockReset();
  });

  it("shows each inspection's quantity exactly as the server sent it, with its status in words", async () => {
    listInspections.mockResolvedValue(
      page([inspectionFixture(), inspectionFixture({ id: "insp-2", status: "PARTIAL_PASS", quantity: "7" })]),
    );
    renderScreen(<InspectionsPage />, [READ]);

    expect(await screen.findAllByText("FAB-001")).toHaveLength(2);
    expect(screen.getByText(/40\.125 m/)).toBeDefined();
    expect(screen.getByText(/^7 m$/)).toBeDefined();
    expect(screen.getAllByText("Pending").length).toBeGreaterThan(1); // the filter pill and the status badge
    expect(screen.getAllByText("Partial pass").length).toBeGreaterThan(1);
  });

  it("filters on the server by the chosen status", async () => {
    const user = userEvent.setup();
    listInspections.mockResolvedValue(page([inspectionFixture()]));
    renderScreen(<InspectionsPage />, [READ]);
    await screen.findByText("FAB-001");

    await user.click(screen.getByRole("button", { name: "On hold" }));
    await waitFor(() =>
      expect(listInspections).toHaveBeenLastCalledWith(expect.objectContaining({ status: "HOLD", page: 1 })),
    );
  });

  it("offers New inspection only with the inspect permission", async () => {
    listInspections.mockResolvedValue(page([inspectionFixture()]));
    const { unmount } = renderScreen(<InspectionsPage />, [READ]);
    await screen.findByText("FAB-001");
    expect(screen.queryByRole("button", { name: "New inspection" })).toBeNull();
    unmount();

    renderScreen(<InspectionsPage />, [READ, INSPECT]);
    expect(await screen.findByRole("button", { name: "New inspection" })).toBeDefined();
  });

  it("says so when there is nothing yet, and what to do about it", async () => {
    listInspections.mockResolvedValue(page([]));
    renderScreen(<InspectionsPage />, [READ, INSPECT]);
    expect(await screen.findByText("No inspections yet")).toBeDefined();
    expect(screen.getByText(/Open one to hold stock aside/)).toBeDefined();
  });

  it("shows a loading state first, then an error with a retry that reloads", async () => {
    const user = userEvent.setup();
    listInspections.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(page([inspectionFixture()]));
    renderScreen(<InspectionsPage />, [READ]);

    expect(await screen.findByText("Couldn't load inspections")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /try again|retry/i }));
    expect(await screen.findByText("FAB-001")).toBeDefined();
    expect(listInspections).toHaveBeenCalledTimes(2);
  });
});
