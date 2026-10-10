/**
 * The challan print view: renders the frozen document from the server (exact quantity strings,
 * money formatted once from minor units), has loading/error states, and prints on request.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue } from "@opsgrids/client";

import { challan } from "@/test-utils/jobwork";
import { JobWorkChallanPrintPage } from "./JobWorkChallanPrint";

const getChallan = vi.fn();

vi.mock("@/api/client", () => ({
  getChallan: (...a: unknown[]) => getChallan(...a),
}));

const AUTH: AuthContextValue = {
  status: "authenticated",
  user: {
    user_id: "u1",
    email: "ops@example.com",
    full_name: "Asha Rao",
    organization_id: "org-1",
    membership_id: "m1",
    roles: [],
    amr: [],
    must_change_password: false,
    mfa_enabled: false,
    organizations: [
      { organization_id: "org-1", slug: "acme", name: "Acme Textiles Pvt Ltd", roles: [] },
    ],
  },
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

function renderPage() {
  const router = createMemoryRouter(
    [{ path: "/jobwork/challans/:id/print", element: <JobWorkChallanPrintPage /> }],
    { initialEntries: ["/jobwork/challans/ch-1/print"] },
  );
  return render(
    <AuthContext.Provider value={AUTH}>
      <PermissionProvider grants={["operations.jobwork.order.read:ORG"]} entitlements={[]}>
        <RouterProvider router={router} />
      </PermissionProvider>
    </AuthContext.Provider>,
  );
}

describe("JobWorkChallanPrintPage", () => {
  beforeEach(() => {
    getChallan.mockReset();
  });

  it("renders the challan: number, parties, goods and total", async () => {
    getChallan.mockResolvedValue(challan());
    renderPage();

    expect(screen.getByLabelText("Loading challan")).toBeDefined();
    const doc = await screen.findByRole("article", { name: "Delivery challan DC/26-27/0001" });
    expect(within(doc).getByText("DC/26-27/0001")).toBeDefined();
    expect(within(doc).getByText("Acme Textiles Pvt Ltd")).toBeDefined();
    expect(within(doc).getByText("Rainbow Dyers")).toBeDefined();
    expect(within(doc).getByText("Process: Dyeing")).toBeDefined();
    const row = within(doc).getByRole("row", { name: /FAB-GREIGE/ });
    expect(within(row).getByText("82.5 m")).toBeDefined();
    expect(within(row).getByText("₹400.00")).toBeDefined();
    expect(within(row).getByText("₹33,000.00")).toBeDefined();
    expect(within(doc).getByText("MH12AB1234")).toBeDefined();
    expect(within(doc).getByText("Not required")).toBeDefined();
  });

  it("shows the e-way bill number once finance has one, and Pending while it is requested", async () => {
    getChallan.mockResolvedValue(challan({ eway_status: "GENERATED", eway_bill_no: "181000123456" }));
    const { unmount } = renderPage();
    expect(await screen.findByText("181000123456")).toBeDefined();
    unmount();

    getChallan.mockResolvedValue(challan({ eway_status: "REQUESTED" }));
    renderPage();
    expect(await screen.findByText("Pending")).toBeDefined();
  });

  it("prints on request", async () => {
    const user = userEvent.setup();
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    getChallan.mockResolvedValue(challan());
    renderPage();
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "Print" }));
    expect(print).toHaveBeenCalledTimes(1);
  });

  it("disables Print and shows an error with retry when the challan can't be loaded", async () => {
    const user = userEvent.setup();
    getChallan.mockRejectedValueOnce(new Error("down"));
    getChallan.mockResolvedValueOnce(challan());
    renderPage();

    expect(await screen.findByText("Couldn't load this challan")).toBeDefined();
    expect((screen.getByRole("button", { name: "Print" }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: /retry|try again/i }));
    expect(await screen.findByRole("article")).toBeDefined();
  });
});
