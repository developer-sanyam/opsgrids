import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as api from "@/api/customer360";
import type { Customer360 } from "@/api/customer360";
import { CUSTOMER_360, CUSTOMER_ID } from "@/api/customer360.fixtures";
import { Customer360Panel } from "./Customer360Panel";

/**
 * The panel's promises: it says who the customer is without ever unmasking them, it reads a
 * partial answer section by section instead of failing whole, and it passes a `402`/`403` through
 * in the service's words rather than dressing it as a fault to retry.
 */

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPanel(customerId: string | null = CUSTOMER_ID) {
  return render(<Customer360Panel customerId={customerId} />);
}

function row(name: string): HTMLElement {
  const heading = screen.getByRole("heading", { name, level: 3 });
  const item = heading.closest("li");
  if (item === null) throw new Error(`no section row for ${name}`);
  return item;
}

describe("populated", () => {
  it("shows the profile with masked contact details, tags and owner", async () => {
    vi.spyOn(api, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderPanel();

    expect(await screen.findByText("Priya Raghavan")).toBeTruthy();
    expect(screen.getByText("Active")).toBeTruthy();
    expect(screen.getByText(CUSTOMER_360.customer.phone)).toBeTruthy();
    expect(screen.getByText(CUSTOMER_360.customer.email)).toBeTruthy();
    expect(screen.getByText("Vaayu Foods · Bengaluru")).toBeTruthy();
    expect(screen.getByText("Asha Menon")).toBeTruthy();
    const tags = within(screen.getByRole("list", { name: "Tags" }));
    expect(tags.getByText("Wholesale")).toBeTruthy();
    expect(tags.getByText("VIP")).toBeTruthy();
    // A masked value is never offered as something to dial or write to.
    expect(screen.queryByRole("link", { name: /4210|vaayu\.example\.com/ })).toBeNull();
  });

  it("shows WhatsApp consent and the 24-hour window, and email consent", async () => {
    vi.spyOn(api, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderPanel();

    expect(await screen.findByText("WhatsApp: Opted in")).toBeTruthy();
    expect(screen.getByText(/Customer window: Active \(\d+ hours? left\)/)).toBeTruthy();
    expect(screen.getByText("Email: Subscribed")).toBeTruthy();
  });

  it("says a closed window and an opt-out plainly", async () => {
    const data: Customer360 = {
      ...CUSTOMER_360,
      channels: {
        whatsapp: {
          opted_in: false,
          window_open_until: "2026-01-01T00:00:00Z",
          last_message_at: null,
          conversations: 0,
        },
        email: { subscribed: false, last_email_at: null },
      },
    };
    vi.spyOn(api, "getCustomer360").mockResolvedValue(data);
    renderPanel();

    expect(await screen.findByText("WhatsApp: Not opted in")).toBeTruthy();
    expect(screen.getByText("Customer window: Closed")).toBeTruthy();
    expect(screen.getByText("No messages yet")).toBeTruthy();
    expect(screen.getByText("Email: Unsubscribed")).toBeTruthy();
  });

  it("shows CRM, Orders, Finance and Support counts", async () => {
    vi.spyOn(api, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderPanel();
    await screen.findByText("Priya Raghavan");

    expect(within(row("CRM")).getByText(/Stage: Negotiation · 2 open opportunities/)).toBeTruthy();
    expect(within(row("CRM")).getByText("Hot")).toBeTruthy();
    expect(within(row("Orders")).getByText(/5 orders in the last 90 days/)).toBeTruthy();
    expect(within(row("Orders")).getByText(/Latest SO-1042 · shipped/)).toBeTruthy();
    expect(within(row("Finance")).getByText(/1 overdue invoice/)).toBeTruthy();
    // The date follows the browser's locale, so the test pins the words and the year, not the order.
    expect(within(row("Finance")).getByText(/Latest INV-2231 · overdue · due .*10.*2026/)).toBeTruthy();
    expect(within(row("Support")).getByText("1 open ticket of 4")).toBeTruthy();
  });

  it("links only to routes that exist in the other apps", async () => {
    vi.spyOn(api, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderPanel();
    await screen.findByText("Priya Raghavan");

    expect(screen.getByRole("link", { name: "View CRM records" }).getAttribute("href")).toMatch(
      /\/contacts$/,
    );
    expect(screen.getByRole("link", { name: "View Finance records" }).getAttribute("href")).toMatch(
      /\/invoices$/,
    );
    expect(screen.getByRole("link", { name: "View Support records" }).getAttribute("href")).toMatch(
      /\/queue$/,
    );
    // There is no customer orders screen anywhere, so there is no link to it.
    expect(screen.queryByRole("link", { name: "View Orders records" })).toBeNull();
    expect(screen.getByRole("link", { name: /Create invoice/ }).getAttribute("href")).toMatch(
      /\/invoices\/new$/,
    );
  });
});

describe("partial results", () => {
  it("marks a section named in `unavailable`, or null, as not available and keeps the rest", async () => {
    const data: Customer360 = {
      ...CUSTOMER_360,
      finance: null,
      orders: null,
      unavailable: ["finance", "orders"],
    };
    vi.spyOn(api, "getCustomer360").mockResolvedValue(data);
    renderPanel();
    await screen.findByText("Priya Raghavan");

    expect(within(row("Finance")).getByText("Not available")).toBeTruthy();
    expect(within(row("Orders")).getByText("Not available")).toBeTruthy();
    expect(within(row("CRM")).getByText(/Stage: Negotiation/)).toBeTruthy();
    expect(within(row("Support")).getByText("1 open ticket of 4")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "View Finance records" })).toBeNull();
    // The one quick action is a finance action, so it goes with the finance section.
    expect(screen.queryByText("Quick actions")).toBeNull();
    expect(screen.getByRole("link", { name: "View CRM records" })).toBeTruthy();
  });

  it("treats a section named in `unavailable` as unavailable even if data came with it", async () => {
    vi.spyOn(api, "getCustomer360").mockResolvedValue({ ...CUSTOMER_360, unavailable: ["support"] });
    renderPanel();
    await screen.findByText("Priya Raghavan");

    expect(within(row("Support")).getByText("Not available")).toBeTruthy();
  });

  it("says a channel is not available when the messaging summary is missing", async () => {
    vi.spyOn(api, "getCustomer360").mockResolvedValue({
      ...CUSTOMER_360,
      channels: { whatsapp: null, email: null },
    });
    renderPanel();
    await screen.findByText("Priya Raghavan");

    expect(screen.getAllByText("Not available")).toHaveLength(2);
    expect(screen.queryByText(/WhatsApp: /)).toBeNull();
  });
});

describe("partial results: channels named in `unavailable`", () => {
  it("hides a channel the service named, and keeps the other", async () => {
    vi.spyOn(api, "getCustomer360").mockResolvedValue({ ...CUSTOMER_360, unavailable: ["whatsapp"] });
    renderPanel();
    await screen.findByText("Priya Raghavan");

    expect(screen.queryByText(/WhatsApp: /)).toBeNull();
    expect(screen.getAllByText("Not available")).toHaveLength(1);
    expect(screen.getByText("Email: Subscribed")).toBeTruthy();
  });
});

describe("no customer linked", () => {
  it("says so, and makes no request", () => {
    const load = vi.spyOn(api, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderPanel(null);

    expect(screen.getByText("No customer linked to this conversation")).toBeTruthy();
    expect(load).not.toHaveBeenCalled();
  });
});

describe("loading", () => {
  it("announces loading and keeps the heading while the request is pending", () => {
    vi.spyOn(api, "getCustomer360").mockReturnValue(new Promise(() => {}));
    renderPanel();

    expect(screen.getByRole("heading", { name: "Customer 360", level: 2 })).toBeTruthy();
    expect(screen.getByText("Loading customer details")).toBeTruthy();
    expect(screen.queryByText("Priya Raghavan")).toBeNull();
  });
});

describe("errors", () => {
  it("offers a retry after a failure and recovers on it", async () => {
    const load = vi
      .spyOn(api, "getCustomer360")
      .mockRejectedValueOnce(new ApiError(503, "The customer service is unavailable.", null))
      .mockResolvedValue(CUSTOMER_360);
    renderPanel();

    expect(await screen.findByText("Couldn't load customer details")).toBeTruthy();
    expect(screen.getByText("The customer service is unavailable.")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Priya Raghavan")).toBeTruthy();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("passes a 403 through in the service's words, with no retry", async () => {
    vi.spyOn(api, "getCustomer360").mockRejectedValue(
      new ApiError(403, "You do not have crm.contacts.read", null),
    );
    renderPanel();

    expect(await screen.findByText("You don't have access to this customer's details")).toBeTruthy();
    expect(screen.getByText("You do not have crm.contacts.read")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("passes a 402 through in the service's words, with no retry", async () => {
    vi.spyOn(api, "getCustomer360").mockRejectedValue(
      new ApiError(402, "crm is not active for this organization", null),
    );
    renderPanel();

    expect(await screen.findByText("Customer 360 isn't included in your plan")).toBeTruthy();
    expect(screen.getByText("crm is not active for this organization")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

describe("announcements", () => {
  it("does not wrap the profile in a live region, so a screen reader is not read the whole panel", async () => {
    vi.spyOn(api, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    const { container } = renderPanel();

    const name = await screen.findByText("Priya Raghavan");
    expect(container.querySelector("[aria-live]")).toBeNull();
    // The only live region is the one-line status, and the profile is not inside it.
    expect(name.closest('[role="status"], [role="alert"], [aria-live]')).toBeNull();
    const status = screen.getByRole("status");
    expect(status.textContent).toBe("Customer details loaded");
    expect(status.contains(name)).toBe(false);
  });

  it("says nothing while loading, then a short line when the details arrive", async () => {
    let arrive: (value: Customer360) => void = () => {};
    vi.spyOn(api, "getCustomer360").mockReturnValue(
      new Promise<Customer360>((resolve) => {
        arrive = resolve;
      }),
    );
    renderPanel();

    expect(screen.getByRole("status").textContent).toBe("");
    arrive(CUSTOMER_360);
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Customer details loaded"));
  });

  it("goes quiet and announces again when another conversation's customer is selected", async () => {
    let arrive: (value: Customer360) => void = () => {};
    const load = vi
      .spyOn(api, "getCustomer360")
      .mockResolvedValueOnce(CUSTOMER_360)
      .mockReturnValueOnce(
        new Promise<Customer360>((resolve) => {
          arrive = resolve;
        }),
      );
    const { rerender } = renderPanel();
    await screen.findByText("Priya Raghavan");

    rerender(<Customer360Panel customerId="another-customer" />);
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("status").textContent).toBe("");
    expect(screen.queryByText("Priya Raghavan")).toBeNull();

    arrive({ ...CUSTOMER_360, customer: { ...CUSTOMER_360.customer, name: "Arun Kumar" } });
    expect(await screen.findByText("Arun Kumar")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Customer details loaded");
  });

  it("announces a failure once, in a short line, and keeps the alert itself silent", async () => {
    vi.spyOn(api, "getCustomer360").mockRejectedValue(new ApiError(503, "The customer service is unavailable.", null));
    const { container } = renderPanel();

    expect(await screen.findByText("The customer service is unavailable.")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Customer details didn't load.");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.querySelectorAll('[role="status"], [role="alert"]').length).toBe(1);
  });

  it("announces a refusal in a short line too", async () => {
    vi.spyOn(api, "getCustomer360").mockRejectedValue(new ApiError(403, "You do not have crm.contacts.read", null));
    renderPanel();

    expect(await screen.findByText("You do not have crm.contacts.read")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Access to customer details was refused.");
  });
});

describe("collapsing", () => {
  it("hides the body and stops reading, and reads again when shown", async () => {
    const load = vi.spyOn(api, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderPanel();
    await screen.findByText("Priya Raghavan");

    const toggle = screen.getByRole("button", { name: "Hide" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await userEvent.click(toggle);

    expect(screen.queryByText("Priya Raghavan")).toBeNull();
    expect(screen.getByRole("button", { name: "Show" }).getAttribute("aria-expanded")).toBe("false");

    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(await screen.findByText("Priya Raghavan")).toBeTruthy();
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });
});
