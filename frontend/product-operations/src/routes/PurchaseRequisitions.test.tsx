/**
 * The requisition list: loading, error (and the plain 403), empty, filtered-empty and populated all
 * get a screen, and "New requisition" follows the write permission.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import type { Page, RequisitionSummaryOut } from "@/api/types";
import { renderRoute } from "@/test-utils/renderRoute";
import { PurchaseRequisitionsPage } from "./PurchaseRequisitions";

const listRequisitions = vi.fn();

vi.mock("@/api/client", () => ({
  listRequisitions: (...args: unknown[]) => listRequisitions(...args),
}));

const ROW: RequisitionSummaryOut = {
  id: "req-1",
  number: "PR-0001",
  status: "SUBMITTED",
  requested_by: "Asha Rao",
  warehouse_name: "Bhiwandi DC",
  needed_by: "2026-10-20",
  line_count: 3,
  converted_po_number: null,
  created_at: "2026-10-01T09:00:00Z",
};

function page(items: RequisitionSummaryOut[]): Page<RequisitionSummaryOut> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length === 0 ? 0 : 1 };
}

function renderPage(grants: string[] = []) {
  return renderRoute(
    [
      { path: "/purchase-requisitions", element: <PurchaseRequisitionsPage /> },
      { path: "/purchase-requisitions/new", element: <h1>New form</h1> },
      { path: "/purchase-requisitions/:id", element: <h1>Detail</h1> },
    ],
    "/purchase-requisitions",
    grants,
  );
}

describe("PurchaseRequisitionsPage", () => {
  beforeEach(() => {
    listRequisitions.mockReset();
  });

  it("shows a loading state that holds the layout until the list arrives", async () => {
    let resolve: (p: Page<RequisitionSummaryOut>) => void = () => undefined;
    listRequisitions.mockReturnValue(new Promise((r) => (resolve = r)));
    renderPage();

    expect(screen.getByRole("status", { name: "Loading requisitions" })).toBeDefined();
    resolve(page([ROW]));
    expect(await screen.findByText("PR-0001")).toBeDefined();
  });

  it("renders the rows with their status as a word", async () => {
    listRequisitions.mockResolvedValue(page([ROW]));
    renderPage();

    expect(await screen.findByText("PR-0001")).toBeDefined();
    expect(screen.getByText("Submitted", { selector: "span span" })).toBeDefined();
    expect(screen.getByText("Bhiwandi DC")).toBeDefined();
  });

  it("explains an empty list and offers the first requisition only with write permission", async () => {
    listRequisitions.mockResolvedValue(page([]));
    renderPage([]);
    expect(await screen.findByText("No requisitions yet")).toBeDefined();
    expect(screen.queryByRole("button", { name: "New requisition" })).toBeNull();
  });

  it("offers New requisition with the write permission, and it navigates to the form", async () => {
    listRequisitions.mockResolvedValue(page([]));
    const router = renderPage(["operations.procurement.requisition.write:OWN"]);
    await screen.findByText("No requisitions yet");

    const buttons = screen.getAllByRole("button", { name: "New requisition" });
    expect(buttons.length).toBeGreaterThan(0);
    await userEvent.setup().click(buttons[0] as HTMLElement);
    await waitFor(() => expect(router.state.location.pathname).toBe("/purchase-requisitions/new"));
  });

  it("says a filter matched nothing, rather than that none exist", async () => {
    listRequisitions.mockResolvedValue(page([ROW]));
    renderPage();
    await screen.findByText("PR-0001");

    listRequisitions.mockResolvedValue(page([]));
    await userEvent.setup().click(screen.getByRole("button", { name: "Rejected" }));
    expect(await screen.findByText("No requisitions match these filters")).toBeDefined();
    await waitFor(() => expect(listRequisitions).toHaveBeenLastCalledWith(expect.objectContaining({ status: "REJECTED" })));
  });

  it("shows the server's sentence with a retry when the list fails", async () => {
    listRequisitions.mockRejectedValueOnce(new ApiError(500, "boom", { type: "x", title: "Server error", status: 500, detail: "The requisition service is down." }));
    renderPage();

    expect(await screen.findByText("The requisition service is down.")).toBeDefined();
    listRequisitions.mockResolvedValue(page([ROW]));
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("PR-0001")).toBeDefined();
  });

  it("renders a 403 as no access, with no retry that cannot help", async () => {
    listRequisitions.mockRejectedValue(new ApiError(403, "Forbidden", { type: "x", title: "Forbidden", status: 403, detail: "You can't read requisitions." }));
    renderPage();

    expect(await screen.findByText("You don't have access to requisitions")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("says the connection is the problem when there is no problem document", async () => {
    listRequisitions.mockRejectedValue(new TypeError("Failed to fetch"));
    renderPage();
    expect(await screen.findByText("Can't reach the server")).toBeDefined();
  });
});
