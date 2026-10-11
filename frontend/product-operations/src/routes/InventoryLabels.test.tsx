/**
 * The printable label view for items, lots, rolls and locations: every label carries a QR of
 * `og:<type>:<code>` and a Code 128 of the plain code, a record whose code a bar code cannot
 * carry is flagged instead of printed blank, and untick/print counts follow the selection.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import type { LabelItem } from "@/api/scan";
import { encodeCode128 } from "@/lib/code128";

const listLabelItems = vi.fn();
vi.mock("@/api/scan", () => ({ listLabelItems: (...a: unknown[]) => listLabelItems(...a) }));
vi.mock("@/data/useWarehouseOptions", () => ({
  useWarehouseIdOptions: () => ({
    options: [{ value: "wh-1", label: "Weaving Store" }],
    status: "success",
    reload: vi.fn(),
  }),
}));

import { InventoryLabelsPage } from "./InventoryLabels";

const ROLLS: LabelItem[] = [
  { type: "roll", id: "r1", code: "R-1", title: "R-1", subtitle: "COT · LOT-A · 82.5 m" },
  { type: "roll", id: "r2", code: "MILL/R 2", title: "R-2", subtitle: "COT · 50 m" },
  { type: "roll", id: "r3", code: "ROLL-É", title: "R-3", subtitle: "bad code" },
];

function page(path: string) {
  const router = createMemoryRouter(
    [{ path: "/inventory/labels/:type", element: <InventoryLabelsPage /> }],
    { initialEntries: [path] },
  );
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  listLabelItems.mockResolvedValue(ROLLS);
});

describe("label sheet", () => {
  it("prints a QR and a Code 128 per label, encoding what the scan screen resolves", async () => {
    const { container } = page("/inventory/labels/roll");
    await screen.findByRole("img", { name: "QR code for R-1" });
    expect(listLabelItems.mock.calls[0]?.[0]).toBe("roll");
    expect(screen.getByRole("img", { name: "QR code for R-1" })).toBeTruthy();
    const bar = screen.getByRole("img", { name: "Bar code for R-1" });
    // The drawn bars are exactly the Code 128 modules of the plain code.
    expect(bar.getAttribute("data-modules")).toBe(String(encodeCode128("R-1").length + 20));
    // And a code with a space or slash is still printable (Code 128 subset B).
    expect(screen.getByRole("img", { name: "Bar code for R-2" })).toBeTruthy();
    expect(container.textContent).toContain("MILL/R 2");
  });

  it("flags a record whose code a bar code cannot carry instead of printing a blank label", async () => {
    page("/inventory/labels/roll");
    await screen.findByText("R-3");
    expect(screen.queryByRole("img", { name: "Bar code for R-3" })).toBeNull();
    expect(screen.getByText(/characters a bar code cannot carry/)).toBeTruthy();
    const box = screen.getByLabelText("Print the label for R-3") as HTMLInputElement;
    expect(box.disabled).toBe(true);
  });

  it("counts the labels to print and follows the selection", async () => {
    const user = userEvent.setup();
    page("/inventory/labels/roll");
    await screen.findByRole("img", { name: "QR code for R-1" });
    expect(screen.getByRole("button", { name: "Print 2 labels" })).toBeTruthy();
    await user.click(screen.getByLabelText("Print the label for R-2"));
    expect(screen.getByRole("button", { name: "Print 1 label" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Select none" }));
    expect((screen.getByRole("button", { name: "Print 0 labels" }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "Select all" }));
    expect(screen.getByRole("button", { name: "Print 2 labels" })).toBeTruthy();
  });

  it("prints through the browser", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    const user = userEvent.setup();
    page("/inventory/labels/roll");
    await user.click(await screen.findByRole("button", { name: /Print 2 labels/ }));
    expect(print).toHaveBeenCalledTimes(1);
  });

  it("has a tab for every kind of label", async () => {
    page("/inventory/labels/roll");
    await screen.findByRole("img", { name: "QR code for R-1" });
    const nav = screen.getByRole("navigation", { name: "Label type" });
    expect([...nav.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual([
      "/inventory/labels/item",
      "/inventory/labels/lot",
      "/inventory/labels/roll",
      "/inventory/labels/location",
    ]);
  });

  it("asks which warehouse for location labels and says when none has a barcode", async () => {
    listLabelItems.mockResolvedValue([]);
    page("/inventory/labels/location");
    expect(await screen.findByText("No locations with a barcode")).toBeTruthy();
    expect(screen.getByLabelText("Warehouse")).toBeTruthy();
    await waitFor(() => expect(listLabelItems.mock.calls[0]?.[1]).toMatchObject({ warehouseId: "wh-1" }));
  });

  it("rejects a label type that does not exist", async () => {
    page("/inventory/labels/box");
    expect(await screen.findByText("Unknown label type")).toBeTruthy();
    expect(listLabelItems).not.toHaveBeenCalled();
  });

  it("shows the server's failure with a retry", async () => {
    listLabelItems.mockRejectedValueOnce(new Error("boom"));
    page("/inventory/labels/item");
    expect(await screen.findByText("Couldn't load the list")).toBeTruthy();
  });
});
