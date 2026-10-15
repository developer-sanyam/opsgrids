import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { CONNECTORS } from "@/lib/integrations";

import { IntegrationsPage } from "./Integrations";

function arrange() {
  render(
    <MemoryRouter initialEntries={["/integrations"]}>
      <IntegrationsPage />
    </MemoryRouter>,
  );
}

describe("IntegrationsPage", () => {
  it("lists every category and marks only Razorpay and Cashfree available", () => {
    arrange();
    for (const name of ["Sales", "POS", "ERP", "Accounting", "Lab", "Couriers", "Payments"]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeTruthy();
    }
    const available = CONNECTORS.filter((c) => c.status === "available").map((c) => c.name);
    expect(available.sort()).toEqual(["Cashfree", "Razorpay"]);
    expect(screen.getAllByRole("link", { name: /Connect in/ })).toHaveLength(2);
    expect(CONNECTORS.some((c) => /dunzo|^ola$/i.test(c.name))).toBe(false);
  });

  it("filters by search and offers a way out of an empty result", async () => {
    arrange();
    const box = screen.getByRole("searchbox", { name: "Search integrations" });
    await userEvent.type(box, "delhivery");
    expect(screen.getByRole("heading", { name: "Delhivery" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Shopify" })).toBeNull();
    await userEvent.clear(box);
    await userEvent.type(box, "zzzz");
    expect(screen.getByText("No integrations match that search")).toBeTruthy();
  });
});
